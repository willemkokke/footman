"""Shell completion installers: script generation and idempotent install."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from footman import _app, _shellcomp


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)  # don't depend on the dev machine
    return tmp_path


def test_bash_install_writes_script_and_rc_line(home):
    lines = _shellcomp.install("bash", "fm")
    script = home / ".local" / "share" / "fm" / "completion.bash"
    assert script.exists()
    body = script.read_text()
    assert "fm --complete --" in body and "complete -o default" in body
    # F46: glob-safe — no split-and-glob COMPREPLY=($(...)); %q-quoted read loop.
    assert "COMPREPLY=($(" not in body
    assert "printf -v line '%q'" in body
    rc = (home / ".bashrc").read_text()
    # The spelling bash can actually read: identical to the path on POSIX,
    # MSYS-form (/c/Users/...) under git-bash, where a backslashed Windows
    # path would be a string of escapes sourcing nothing.
    assert f"source {_shellcomp._bash_path(script)}" in rc
    assert any("installed" in line for line in lines)


def test_install_is_idempotent(home):
    _shellcomp.install("bash", "fm")
    _shellcomp.install("bash", "fm")
    rc = (home / ".bashrc").read_text()
    assert rc.count("completion.bash") == 1  # sourced once, not twice


def test_append_once_utf16_profile(tmp_path):
    # F47: a WinPS5 UTF-16 profile is appended in UTF-16 — not crashed on read
    # nor corrupted with a UTF-8 tail — with only its original BOM.
    rc = tmp_path / "profile.ps1"
    rc.write_bytes("Set-Alias foo bar\n".encode("utf-16"))  # utf-16 + BOM
    assert _shellcomp._append_once(rc, '. "x"') is True
    text = rc.read_bytes().decode("utf-16")  # round-trips
    assert "Set-Alias foo bar" in text and '. "x"' in text
    assert rc.read_bytes().count(b"\xff\xfe") == 1  # no second BOM mid-file
    assert _shellcomp._append_once(rc, '. "x"') is False  # idempotent


def test_append_once_latin1_file(tmp_path):
    # F47: a non-UTF-8 rc file (latin-1 bytes) doesn't crash; latin-1 round-trips.
    rc = tmp_path / ".bashrc"
    rc.write_bytes(b"# caf\xe9\n")  # latin-1 'é' — invalid UTF-8
    assert _shellcomp._append_once(rc, "source x") is True
    assert b"source x" in rc.read_bytes()


def test_append_once_unwritable_is_install_error(tmp_path):
    # F47: a write failure is a taught InstallError (exit-2 path), not a
    # traceback.
    (tmp_path / "sub").write_text("i am a file, not a dir")
    rc = tmp_path / "sub" / "rc"
    with pytest.raises(_shellcomp.InstallError, match="add this line yourself"):
        _shellcomp._append_once(rc, "source x")


def test_zsh_install(home):
    _shellcomp.install("zsh", "fm")
    script = home / ".local" / "share" / "fm" / "completion.zsh"
    body = script.read_text()
    assert "compdef _fm_complete fm" in body
    assert "_describe -t fm 'fm' items" in body  # 11.2: aligned description column
    assert "source" in (home / ".zshrc").read_text()


def test_zsh_install_honors_zdotdir(home, monkeypatch):
    # F48/D11: zsh reads $ZDOTDIR/.zshrc when set — target that, not ~/.zshrc.
    zdot = home / "zdot"
    zdot.mkdir()
    monkeypatch.setenv("ZDOTDIR", str(zdot))
    _shellcomp.install("zsh", "fm")
    assert "source" in (zdot / ".zshrc").read_text()
    assert not (home / ".zshrc").exists()


def test_bash_install_targets_login_profile_on_macos(home, monkeypatch):
    # F48/D11: macOS Terminal opens login shells that read a login profile, not
    # .bashrc — so completion must land in both. No login profile exists here,
    # so .bash_profile is created.
    monkeypatch.setattr(_shellcomp.sys, "platform", "darwin")
    _shellcomp.install("bash", "fm")
    assert (home / ".bashrc").read_text().count("completion.bash") == 1
    assert (home / ".bash_profile").read_text().count("completion.bash") == 1


def test_bash_install_respects_existing_login_profile_on_macos(home, monkeypatch):
    # D11: never create .bash_profile over an existing .profile — use what's there.
    monkeypatch.setattr(_shellcomp.sys, "platform", "darwin")
    (home / ".profile").write_text("# existing login profile\n")
    _shellcomp.install("bash", "fm")
    assert "completion.bash" in (home / ".profile").read_text()
    assert not (home / ".bash_profile").exists()


def test_fish_install_needs_no_rc_edit(home):
    _shellcomp.install("fish", "fm")
    script = home / ".config" / "fish" / "completions" / "fm.fish"
    assert "complete -c fm" in script.read_text()
    assert not (home / ".fishrc").exists()


def test_branded_prog_threads_through(home):
    body = _shellcomp.script_for("bash", "acme-tool")
    assert "acme-tool --complete --" in body
    assert "_acme_tool_complete" in body  # function names sanitised


def test_cli_install_end_to_end(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    assert _app.run(["--install-completion", "fish"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out
    assert Path(home / ".config" / "fish" / "completions" / "fm.fish").exists()


def test_install_completion_yields_to_help(home, tmp_path, monkeypatch, capsys):
    # F06: --help anywhere must never write rc files — asking for help touches
    # nothing on disk. `fm --install-completion fish --help` prints help.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    assert _app.run(["--install-completion", "fish", "--help"]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out  # help, not an install confirmation
    assert not (home / ".config" / "fish" / "completions" / "fm.fish").exists()


# --- --setup-completion: print the hook for `eval`, session-only ----------------


def test_setup_completion_prints_hook_and_writes_nothing(home, capsys):
    assert _app.run(["--setup-completion", "zsh"]) == 0
    out = capsys.readouterr().out
    assert "_fm_complete" in out and "compdef" in out  # the sourceable hook
    assert not (home / ".local" / "share" / "fm" / "completion.zsh").exists()
    assert not (home / ".zshrc").exists()  # session-only: no rc edit


def test_setup_completion_detection_note_stays_off_stdout(home, monkeypatch, capsys):
    # A bare flag detects the shell, but the note MUST go to stderr — stdout is
    # eval'd, so a stray "detected shell:" line would be a syntax error.
    monkeypatch.setattr(_shellcomp, "detect_shell", lambda: "bash")
    assert _app.run(["--setup-completion"]) == 0
    cap = capsys.readouterr()
    assert "_fm_complete" in cap.out and "detected shell" not in cap.out
    assert "detected shell: bash" in cap.err


def test_setup_completion_unknown_shell_teaches(home, capsys):
    assert _app.run(["--setup-completion", "tcsh"]) == 2
    err = capsys.readouterr().err
    assert "--setup-completion expects one of" in err and "tcsh" in err


def test_setup_completion_alias_and_yields_to_help(home, tmp_path, monkeypatch, capsys):
    assert _app.run(["--setup-completion", "nu"]) == 0  # nu → nushell
    assert "completions.external.completer" in capsys.readouterr().out
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    assert _app.run(["--setup-completion", "zsh", "--help"]) == 0
    assert "usage:" in capsys.readouterr().out  # help wins, no hook printed


# --- pwsh ----------------------------------------------------------------------


def test_pwsh_install_writes_script_and_profile_line(home, monkeypatch):
    profile = home / "pwsh-profile" / "Microsoft.PowerShell_profile.ps1"
    monkeypatch.setattr(_shellcomp, "_pwsh_profiles", lambda: [profile])
    _shellcomp.install("pwsh", "fm")
    _shellcomp.install("pwsh", "fm")  # idempotent
    script = home / ".local" / "share" / "fm" / "completion.ps1"
    body = script.read_text()
    assert "Register-ArgumentCompleter -Native -CommandName fm" in body
    assert "--complete $empty --" in body  # F16: --empty-partial via $empty
    assert "'--empty-partial'" in body
    assert profile.read_text().count("completion.ps1") == 1


def test_pwsh_install_covers_every_powershell_profile(home, monkeypatch):
    # PowerShell 7 and Windows PowerShell keep different $PROFILEs: on a
    # machine with both, the hook lands in each, so completion works in
    # whichever shell the user opens.
    seven = home / "Documents" / "PowerShell" / "profile.ps1"
    five = home / "Documents" / "WindowsPowerShell" / "profile.ps1"

    def fake_ask(candidates, args):
        return {"pwsh": str(seven), "powershell": str(five)}.get(candidates[0])

    monkeypatch.setattr(_shellcomp, "_ask_shell", fake_ask)
    lines = _shellcomp.install("pwsh", "fm")
    _shellcomp.install("pwsh", "fm")  # idempotent across both
    for profile in (seven, five):
        assert profile.read_text().count("completion.ps1") == 1
    assert sum("added" in line for line in lines) == 2


def test_pwsh_install_single_shell_machines_get_one_profile(home, monkeypatch):
    seven = home / "Documents" / "PowerShell" / "profile.ps1"

    def fake_ask(candidates, args):
        return str(seven) if candidates[0] == "pwsh" else None

    monkeypatch.setattr(_shellcomp, "_ask_shell", fake_ask)
    _shellcomp.install("pwsh", "fm")
    assert seven.read_text().count("completion.ps1") == 1


# --- uninstall -----------------------------------------------------------------


def test_bash_uninstall_reverses_install(home):
    _shellcomp.install("bash", "fm")
    script = home / ".local" / "share" / "fm" / "completion.bash"
    assert script.exists()
    lines = _shellcomp.uninstall("bash", "fm")
    assert not script.exists()
    assert "source" not in (home / ".bashrc").read_text()
    assert any("removed" in line for line in lines)
    # Idempotent: a second uninstall reports, never fails.
    lines = _shellcomp.uninstall("bash", "fm")
    assert any("nothing to remove" in line for line in lines)


def test_zsh_uninstall_reverses_install(home):
    _shellcomp.install("zsh", "fm")
    _shellcomp.uninstall("zsh", "fm")
    assert not (home / ".local" / "share" / "fm" / "completion.zsh").exists()
    assert "completion.zsh" not in (home / ".zshrc").read_text()


def test_fish_uninstall_removes_the_dropin(home):
    _shellcomp.install("fish", "fm")
    target = home / ".config" / "fish" / "completions" / "fm.fish"
    assert target.exists()
    assert any("removed" in line for line in _shellcomp.uninstall("fish", "fm"))
    assert not target.exists()
    lines = _shellcomp.uninstall("fish", "fm")
    assert any("nothing to remove" in line for line in lines)


def test_remove_once_keeps_utf16_profile_utf16(tmp_path):
    # The uninstall mirror of the F47 append test: removing the hook line from
    # a UTF-16 profile leaves it UTF-16, one BOM, other content intact.
    rc = tmp_path / "profile.ps1"
    rc.write_bytes("Set-Alias foo bar\n".encode("utf-16"))
    _shellcomp._append_once(rc, '. "x"')
    assert _shellcomp._remove_once(rc, '. "x"') is True
    text = rc.read_bytes().decode("utf-16")
    assert "Set-Alias foo bar" in text and '. "x"' not in text
    assert rc.read_bytes().count(b"\xff\xfe") == 1  # still exactly one BOM
    assert _shellcomp._remove_once(rc, '. "x"') is False  # idempotent


def test_pwsh_uninstall_when_shell_is_gone_still_removes_script(home, monkeypatch):
    profile = home / "pwsh-profile" / "profile.ps1"
    monkeypatch.setattr(_shellcomp, "_pwsh_profiles", lambda: [profile])
    _shellcomp.install("pwsh", "fm")
    script = home / ".local" / "share" / "fm" / "completion.ps1"
    assert script.exists()

    # PowerShell was uninstalled since: its profile can't be located any more.
    def gone():
        raise _shellcomp.InstallError("pwsh (or powershell) not found on PATH")

    monkeypatch.setattr(_shellcomp, "_pwsh_profiles", gone)
    lines = _shellcomp.uninstall("pwsh", "fm")
    assert not script.exists()  # the script goes regardless
    assert any("remove this line yourself" in line for line in lines)
    assert "completion.ps1" in profile.read_text()  # named for hand-removal


def test_uninstall_via_cli_unknown_shell_teaches(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    assert _app.run(["--uninstall-completion", "tcsh"]) == 2
    assert "bash|zsh|fish" in capsys.readouterr().err


def test_uninstall_via_cli(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    assert _app.run(["--install-completion", "bash"]) == 0
    capsys.readouterr()
    assert _app.run(["--uninstall-completion", "bash"]) == 0
    out = capsys.readouterr().out
    assert "removed" in out
    assert not (home / ".local" / "share" / "fm" / "completion.bash").exists()


def test_pwsh_missing_is_a_taught_error(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_shellcomp.shutil, "which", lambda _: None)
    assert _app.run(["--install-completion", "powershell"]) == 2  # alias accepted
    assert "not found on PATH" in capsys.readouterr().err


# --- functional: the generated hooks driven by the real shells -------------------
# (each skips when its shell is absent; CI's `shells` job installs them all
# so nothing skips silently there)


@pytest.fixture
def fm_project_dir(home, tmp_path, monkeypatch):
    """A tiny project with a built manifest, plus the venv bin for PATH."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef lint(fix: bool = False):\n    "Lint."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert _app.run(["--list"]) == 0  # builds the manifest the hot path serves
    return tmp_path


VENV_BIN = Path(sys.executable).parent

# POSIX shells on Windows (git-bash and friends) are out of scope: paths and
# process semantics differ, and pwsh is the Windows completion story — which
# has its own functional test running on every platform.
_posix_shell = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX shells on Windows are out of scope"
)


@_posix_shell
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_bash_completion_functional(home, fm_project_dir):
    script = home / "completion.bash"
    script.write_text(_shellcomp.script_for("bash", "fm"), encoding="utf-8")
    body = (
        f'PATH="{VENV_BIN}:$PATH"\n'
        f'source "{script}"\n'
        "COMP_WORDS=(fm li); COMP_CWORD=1\n"
        "_fm_complete\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
        "COMP_WORDS=(fm lint --f); COMP_CWORD=2\n"
        "_fm_complete\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    out = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=fm_project_dir,
    )
    assert out.returncode == 0, out.stderr
    # `lint` has a "Lint." docstring → resolver emits `lint\tLint.`; bash strips
    # the description column, leaving the bare name as its own line.
    assert "lint" in out.stdout.splitlines()
    assert "Lint." not in out.stdout
    assert "--fix" in out.stdout.split()  # the bash-3.2 slice regression case


@_posix_shell
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_zsh_completion_functional(home, fm_project_dir):
    """Sources the real hook (registration incl. the compinit fallback), then
    validates the hook's exact expansion idiom — full compadd needs an
    interactive zle context no CI has."""
    script = home / "completion.zsh"
    script.write_text(_shellcomp.script_for("zsh", "fm"), encoding="utf-8")
    body = (
        f"path=('{VENV_BIN}' $path)\n"
        f'source "{script}" || exit 9\n'
        'words=(fm lint ""); CURRENT=3\n'
        'raw="$(fm --complete -- "${(@)words[2,CURRENT]}" 2>/dev/null)"\n'
        "completions=(${(f)raw})\n"
        "print -rl -- $completions\n"
    )
    out = subprocess.run(
        ["zsh", "-c", body],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=fm_project_dir,
    )
    assert out.returncode == 0, out.stderr
    assert "--fix" in out.stdout.split()  # empty current word survives quoting
    assert "Lint." in out.stdout  # 11.2: the description column reaches zsh


@_posix_shell
@pytest.mark.skipif(shutil.which("fish") is None, reason="fish not installed")
def test_fish_completion_functional(home, fm_project_dir):
    """fish can query its own completion engine: `complete -C 'fm li'`."""
    script = home / "completion.fish"
    script.write_text(_shellcomp.script_for("fish", "fm"), encoding="utf-8")
    body = (
        f"set -gx PATH {VENV_BIN} $PATH\n"
        f'source "{script}"\n'
        'complete -C "fm li"\n'
        'complete -C "fm lint --f"\n'
    )
    out = subprocess.run(
        ["fish", "-c", body],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=fm_project_dir,
    )
    assert out.returncode == 0, out.stderr
    assert "lint" in out.stdout.split()
    assert "--fix" in out.stdout.split()
    assert "Lint." in out.stdout  # 11.2: fish renders the tab-separated description


# --- bare --install-completion: shell auto-detection -----------------------------


def test_bare_install_detects_and_installs(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_shellcomp, "detect_shell", lambda: "fish")
    assert _app.run(["--install-completion"]) == 0
    out = capsys.readouterr().out
    assert "detected shell: fish" in out
    assert (home / ".config" / "fish" / "completions" / "fm.fish").exists()


def test_bare_install_undetectable_teaches(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_shellcomp, "detect_shell", lambda: None)
    assert _app.run(["--install-completion"]) == 2
    err = capsys.readouterr().err
    assert "could not detect" in err and "bash|zsh|fish|pwsh|nushell" in err


@_posix_shell
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_detection_through_a_real_shell(home, tmp_path, monkeypatch):
    """`zsh -c '...'` must detect zsh — via the process tree, since $SHELL may
    disagree with the shell actually running us. The trailing `:` matters: a
    shell running `-c` with a single command exec-replaces itself, and an
    exec'd-away shell is genuinely not in the process tree any more."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.setenv("SHELL", "/bin/false")  # the login shell must not win
    venv_bin = Path(sys.executable).parent
    out = subprocess.run(
        ["zsh", "-c", f'PATH="{venv_bin}:$PATH"; fm --install-completion; :'],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "detected shell: zsh" in out.stdout
    assert (home / ".local" / "share" / "fm" / "completion.zsh").exists()


@_posix_shell
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_setup_completion_evals_in_real_zsh(tmp_path):
    """`eval "$(fm --setup-completion zsh)"` defines the completer in-session,
    with no rc file written — the session-only counterpart to install."""
    venv_bin = Path(sys.executable).parent
    out = subprocess.run(
        [
            "zsh",
            "-fc",
            f'PATH="{venv_bin}:$PATH"; eval "$(fm --setup-completion zsh)"; '
            "typeset -f _fm_complete >/dev/null && echo DEFINED",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "DEFINED" in out.stdout


def _fake_ps(tree: dict[int, tuple[int, str]]):
    """A subprocess.run stand-in serving `ps -p PID -o ppid=,comm=` from a dict."""

    def run(cmd, **kwargs):
        pid = int(cmd[2])
        if pid not in tree:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        ppid, comm = tree[pid]
        return subprocess.CompletedProcess(cmd, 0, f"{ppid} {comm}\n", "")

    return run


def test_detect_walks_past_uv_to_the_shell(monkeypatch):
    # fm's parent is uv (pid 20), uv's parent is a login zsh (pid 10).
    monkeypatch.setattr(_shellcomp.os, "getppid", lambda: 20)
    monkeypatch.setattr(
        _shellcomp.subprocess,
        "run",
        _fake_ps({20: (10, "/opt/uv/uv"), 10: (1, "-zsh")}),
    )
    assert _shellcomp._detect_posix() == "zsh"


def test_detect_recognises_nu_by_process_name(monkeypatch):
    monkeypatch.setattr(_shellcomp.os, "getppid", lambda: 20)
    monkeypatch.setattr(
        _shellcomp.subprocess,
        "run",
        _fake_ps({20: (1, "/opt/homebrew/bin/nu")}),
    )
    assert _shellcomp._detect_posix() == "nushell"


def test_detect_falls_back_to_login_shell(monkeypatch):
    monkeypatch.setattr(_shellcomp.os, "getppid", lambda: 20)
    monkeypatch.setattr(_shellcomp.subprocess, "run", _fake_ps({}))  # ps knows nothing
    monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
    assert _shellcomp._detect_posix() == "fish"


def test_detect_gives_up_honestly(monkeypatch):
    monkeypatch.setattr(_shellcomp.os, "getppid", lambda: 20)
    monkeypatch.setattr(_shellcomp.subprocess, "run", _fake_ps({}))
    monkeypatch.setenv("SHELL", "/bin/tcsh")  # unsupported login shell
    assert _shellcomp._detect_posix() is None


def test_detect_stops_at_pid_one(monkeypatch):
    # An unbroken chain of non-shells must terminate, not loop.
    monkeypatch.setattr(_shellcomp.os, "getppid", lambda: 30)
    monkeypatch.setattr(
        _shellcomp.subprocess,
        "run",
        _fake_ps({30: (20, "python"), 20: (1, "launchd")}),
    )
    monkeypatch.delenv("SHELL", raising=False)
    assert _shellcomp._detect_posix() is None


# --- nushell -------------------------------------------------------------------


def test_nushell_install_writes_script_and_config_line(home, monkeypatch):
    config = home / "nu-config" / "config.nu"
    monkeypatch.setattr(_shellcomp, "_nu_config_path", lambda: config)
    _shellcomp.install("nushell", "fm")
    _shellcomp.install("nushell", "fm")  # idempotent
    script = home / ".local" / "share" / "fm" / "completion.nu"
    body = script.read_text()
    assert "$env.config.completions.external.completer" in body
    assert "^fm --complete --" in body
    assert "__fm_prev" in body  # wraps, never replaces, an existing completer
    assert config.read_text().count("completion.nu") == 1


def test_nu_missing_is_a_taught_error(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_shellcomp.shutil, "which", lambda _: None)
    assert _app.run(["--install-completion", "nu"]) == 2  # alias accepted
    assert "not found on PATH" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("nu") is None, reason="nushell not installed")
def test_nushell_completion_functional(home, tmp_path, monkeypatch):
    """The generated hook, sourced and invoked by a real nushell."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef lint(fix: bool = False):\n    "Lint."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert _app.run(["--list"]) == 0  # builds the manifest the hot path serves

    script = home / "completion.nu"
    script.write_text(_shellcomp.script_for("nushell", "fm"), encoding="utf-8")
    # Single-quote both paths and use forward slashes: nushell double quotes
    # process `\` escapes, which would mangle a Windows path — `as_posix()`
    # sidesteps that, and nushell accepts `/` on every platform.
    venv_bin = Path(sys.executable).parent.as_posix()
    nu_script = (
        f"$env.PATH = ($env.PATH | prepend '{venv_bin}')\n"
        f"source '{script.as_posix()}'\n"
        # A task name completes to a {value, description} record; print both
        # columns (multi-statement `nu -c` only auto-renders the last pipeline)
        # so the description is proven to reach nushell's menu.
        "print (do $env.config.completions.external.completer [fm li]"
        " | each {|r| $'($r.value)=($r.description? | default NONE)'} | to text)\n"
        'print (do $env.config.completions.external.completer [fm lint ""]'
        " | get value | to text)\n"
    )
    out = subprocess.run(
        ["nu", "-c", nu_script],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "lint=Lint." in out.stdout.split()  # value carries its description
    assert "--fix" in out.stdout.split()  # a bare option value still completes


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_pwsh_completion_functional(home, tmp_path, monkeypatch):
    """The generated completer, driven by PowerShell's own completion engine."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef lint(fix: bool = False):\n    "Lint."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert _app.run(["--list"]) == 0  # builds the manifest the hot path serves

    script = home / "completion.ps1"
    script.write_text(_shellcomp.script_for("pwsh", "fm"), encoding="utf-8")
    venv_bin = Path(sys.executable).parent
    ps = (
        f'$env:PATH = "{venv_bin}" + [IO.Path]::PathSeparator + $env:PATH; '
        f". '{script}'; "
        "$r = [System.Management.Automation.CommandCompletion]::CompleteInput("
        '"fm li", 5, $null); '
        "$r.CompletionMatches | ForEach-Object CompletionText"
    )
    out = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "lint" in out.stdout.split()


# --- CI guard: prove no functional shell test is silently skipping ---------------


def _ci_required_shell_exes() -> set[str]:
    """Executables whose functional tests are expected to run on this platform.

    bash/zsh/fish tests are `@_posix_shell`-gated, so on Windows only the
    cross-platform pair (nushell + pwsh) is exercised; macOS and Linux drive the
    whole set. nushell's binary is `nu`.
    """
    if sys.platform.startswith("win"):
        return {"nu", "pwsh"}
    return {"bash", "zsh", "fish", "nu", "pwsh"}


@_posix_shell
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
@pytest.mark.skipif(
    importlib.util.find_spec("rich") is None
    or importlib.util.find_spec("pyte") is None,
    reason="rich+pyte (the shots group) not installed",
)
def test_zsh_cast_records_an_animated_completion(home, tmp_path, monkeypatch):
    """End-to-end `docs cast`: a real interactive zsh from a scratch config,
    the hook loaded via --setup-completion, TAB answered from the warm cache
    (FOOTMAN_CACHE_DIR carries it past the scratch HOME), and the frames
    composed into one animated SVG."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nplugins = ['footman']\n"
    )
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef lint(fix: bool = False):\n    "Lint."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert _app.run(["--list"]) == 0  # warm the manifest TAB will serve
    dest = tmp_path / "cast.svg"
    line = ["footman", "docs", "cast", "--out", str(dest), "--shell", "zsh"]
    # The trailing wait gives the hook's `fm --complete` subprocess room on
    # a cold CI runner — the settle window alone raced its Python startup.
    line += ["--width", "70", "--height", "10", "--", "fm li", "<TAB>", "<WAIT:2500>"]
    assert _app.run(line) == 0
    svg = dest.read_text(encoding="utf-8")
    assert svg.count("cast-frame") >= 2  # it animates
    if "lint" not in svg:  # diagnose with the scratch shell's own answers
        pytest.fail(_diagnose_zsh_cast(), pytrace=False)


def _diagnose_zsh_cast() -> str:
    """Boot the same scratch zsh and ask it why TAB had nothing to say —
    the final screen goes into the failure message, so a CI log carries
    the ground truth (hook registered? resolver answering? PATH sane?)."""
    import tempfile

    from footman.tasks.docs import _boot_shell, _pty_session, _screens, keystrokes

    with tempfile.TemporaryDirectory() as scratch:
        argv, env = _boot_shell("zsh", "fm", Path(scratch))
        chunks = _pty_session(
            argv,
            width=110,
            height=18,
            sends=keystrokes(
                (
                    "print PATH_FM=$(whence -p fm); print COMPS=${_comps[fm]}",
                    "<ENTER>",
                    "<WAIT>",
                    "print RAW; fm --complete -- 'li' 2>&1 | head -4",
                    "<ENTER>",
                    "<WAIT:2000>",
                )
            ),
            settle=1.5,
            env_extra=env,
        )
    frames = _screens(chunks, width=110, height=18)
    if not frames:
        return "cast produced no completion, and the probe session was silent"
    lines = ["".join(ch for ch, _ in row).rstrip() for row in frames[-1][1]]
    screen = "\n".join(line for line in lines if line)
    return f"TAB completed nothing; the scratch zsh reports:\n{screen}"


@_posix_shell
@pytest.mark.skipif(
    importlib.util.find_spec("rich") is None
    or importlib.util.find_spec("pyte") is None,
    reason="rich+pyte (the shots group) not installed",
)
@pytest.mark.parametrize("shell", ["bash", "fish", "pwsh", "nushell"])
def test_cast_completes_in_every_posix_shell(shell: str, home, tmp_path, monkeypatch):
    """The cast boot recipe for each remaining shell, end to end: a real
    interactive session, the hook loaded, TAB completing a unique prefix.
    Text is asserted tag-stripped — shells that style per cell (fish) export
    one <text> element per character."""
    import re as _re

    exe = {"nushell": "nu"}.get(shell, shell)
    if shutil.which(exe) is None:
        pytest.skip(f"{exe} not installed")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nplugins = ['footman']\n"
    )
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef lint(fix: bool = False):\n    "Lint."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    assert _app.run(["--list"]) == 0  # warm the manifest TAB will serve
    dest = tmp_path / "cast.svg"
    line = ["footman", "docs", "cast", "--out", str(dest), "--shell", shell]
    line += ["--width", "70", "--height", "12", "--", "fm li", "<TAB>", "<WAIT:2500>"]
    assert _app.run(line) == 0
    svg = dest.read_text(encoding="utf-8")
    text = _re.sub(r"&#160;", "", _re.sub(r"<[^>]+>", "", svg))
    assert svg.count("cast-frame") >= 2  # it animates
    assert "lint" in text  # TAB completed the prefix from the cached manifest


@pytest.mark.skipif(
    not os.environ.get("FOOTMAN_REQUIRE_SHELLS"),
    reason="only where CI promises every shell (sets FOOTMAN_REQUIRE_SHELLS)",
)
def test_ci_provisions_every_testable_shell():
    """A `skipif(which(...) is None)` guard makes a broken shell install vanish
    into a green skip. Where CI sets FOOTMAN_REQUIRE_SHELLS it is promising the
    platform's shells are all installed — so assert they are actually on PATH,
    turning a 404 on the nushell tarball or an apt hiccup into a hard failure
    instead of a functional test that quietly never ran."""
    missing = sorted(s for s in _ci_required_shell_exes() if shutil.which(s) is None)
    assert not missing, f"expected shells not on PATH: {missing}"


# --- detect_shell: the dispatcher, on both platforms ---------------------------


def test_detect_shell_windows_reads_the_powershell_tell(monkeypatch):
    """Windows has no `ps`, so PowerShell's exported PSModulePath is the
    tell. Platform-independent by design — the branch is unreachable on a
    POSIX runner, and a Windows-only test would leave it dark everywhere
    else."""
    monkeypatch.setattr(_shellcomp.os, "name", "nt")
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\Modules")
    assert _shellcomp.detect_shell() == "pwsh"


def test_detect_shell_windows_without_the_tell_gives_up(monkeypatch):
    monkeypatch.setattr(_shellcomp.os, "name", "nt")
    monkeypatch.delenv("PSModulePath", raising=False)
    assert _shellcomp.detect_shell() is None


def test_detect_shell_posix_delegates_to_the_process_walk(monkeypatch):
    monkeypatch.setattr(_shellcomp.os, "name", "posix")
    monkeypatch.setattr(_shellcomp, "_detect_posix", lambda: "fish")
    assert _shellcomp.detect_shell() == "fish"


# Every shell footman installs for, driving the *real* detector from inside
# a *real* shell — the process-tree walk (or, on Windows, the PSModulePath
# tell) as users actually meet it. `_detect_posix` is unit-tested against a
# faked `ps` above; this is the other half. Each shell needs its own
# spelling: nushell calls externals through `^`, PowerShell through `&`,
# and a POSIX shell exec-replaces itself for a lone command (a trailing
# `true` keeps it in the process tree, where the walk can find it).
_PROBE = "from footman._shellcomp import detect_shell; print(detect_shell())"
_DETECT_CASES = [
    ("bash", ["bash", "-c"], "\"{py}\" -c '{code}'; true", "bash"),
    ("zsh", ["zsh", "-c"], "\"{py}\" -c '{code}'; true", "zsh"),
    ("fish", ["fish", "-c"], "\"{py}\" -c '{code}'; true", "fish"),
    ("nu", ["nu", "-c"], "^\"{py}\" -c '{code}'", "nushell"),
    ("pwsh", ["pwsh", "-NoProfile", "-Command"], "& \"{py}\" -c '{code}'", "pwsh"),
]


@pytest.mark.parametrize(("exe", "argv", "template", "expected"), _DETECT_CASES)
def test_detection_from_inside_every_real_shell(
    exe, argv, template, expected, monkeypatch
):
    if os.name == "nt" and exe in ("zsh", "fish"):
        # zsh and fish have no Windows story; git-bash does, and is driven
        # below — MSYSTEM is how footman recognises it.
        pytest.skip("zsh/fish on Windows are out of scope")
    if shutil.which(exe) is None:
        pytest.skip(f"{exe} is not installed")
    monkeypatch.setenv("SHELL", "/bin/false")  # $SHELL must not be the answer
    # git-bash cannot read a backslashed Windows path in its command line
    # any more than in an rc file; nushell would eat the backslashes as
    # escapes. Both want forward slashes.
    py = sys.executable
    if exe == "bash":
        py = _shellcomp._bash_path(py)
    elif exe == "nu":
        py = Path(py).as_posix()
    command = template.format(py=py, code=_PROBE)
    env = dict(os.environ)
    if os.name == "nt" and exe == "bash":
        # A git-bash *session* exports MSYSTEM — that is the launcher's
        # doing, not bash's, so a bare `bash.exe -c` spawned from a Windows
        # process does not have it and is genuinely indistinguishable from
        # any other Windows process. Provide it the way the launcher would,
        # which is the environment a user actually types in.
        env["MSYSTEM"] = "MINGW64"
    out = subprocess.run(
        [*argv, command], capture_output=True, text=True, timeout=60, env=env
    )
    assert out.returncode == 0, out.stderr
    # On Windows there is no process walk: git-bash is recognised by the
    # MSYSTEM it exports, and everything else falls to PowerShell's
    # machine-level PSModulePath — the documented over-claim.
    want = expected
    if os.name == "nt":
        want = "bash" if exe == "bash" else "pwsh"
    assert out.stdout.strip() == want


# --- git-bash on Windows -------------------------------------------------------


def test_detect_shell_windows_prefers_git_bash_over_the_powershell_tell(monkeypatch):
    """PSModulePath is machine-level and set inside git-bash too, so the
    MSYSTEM tell has to win — otherwise a git-bash user is told "pwsh" and
    installs a hook their shell will never read."""
    monkeypatch.setattr(_shellcomp.os, "name", "nt")
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\Modules")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    assert _shellcomp.detect_shell() == "bash"


def test_bash_path_speaks_msys_on_windows():
    """A Windows path is unusable in a bash rc — backslashes are escapes,
    so the source line would silently source nothing."""
    windows_path = r"C:\Users\me\.local\share\fm\completion.bash"
    assert (
        _shellcomp._bash_path(windows_path, windows=True)
        == "/c/Users/me/.local/share/fm/completion.bash"
    )


def test_bash_path_is_a_noop_off_windows():
    # A plain string, not a Path: `Path("/home/me/x")` on Windows is a
    # WindowsPath that stringifies with backslashes, which would be
    # testing pathlib's flavour rather than this translation.
    assert _shellcomp._bash_path("/home/me/x.bash", windows=False) == "/home/me/x.bash"


def test_bash_install_and_uninstall_agree_on_the_rc_line(home):
    """Both sides build the source line independently; if they ever
    disagree, uninstall removes the script and leaves an rc line pointing
    at nothing. (The Windows spelling has its own unit test above; this
    pins the parity that makes it safe.)"""
    _shellcomp.install("bash", "fm")
    assert "source" in (home / ".bashrc").read_text()
    _shellcomp.uninstall("bash", "fm")
    assert "source" not in (home / ".bashrc").read_text()
