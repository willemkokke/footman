"""Shell completion installers: script generation and idempotent install."""

from __future__ import annotations

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
    assert f"source {script}" in rc
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
    assert "compdef _fm_complete fm" in script.read_text()
    assert "source" in (home / ".zshrc").read_text()


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
    assert "lint" in out.stdout.split()
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
    venv_bin = Path(sys.executable).parent
    nu_script = (
        f"$env.PATH = ($env.PATH | prepend '{venv_bin}')\n"
        f'source "{script}"\n'
        "do $env.config.completions.external.completer [fm li] | to text\n"
        'do $env.config.completions.external.completer [fm lint ""] | to text\n'
    )
    out = subprocess.run(
        ["nu", "-c", nu_script],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "lint" in out.stdout.split()
    assert "--fix" in out.stdout.split()


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
