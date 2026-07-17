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
    rc = (home / ".bashrc").read_text()
    assert f"source {script}" in rc
    assert any("installed" in line for line in lines)


def test_install_is_idempotent(home):
    _shellcomp.install("bash", "fm")
    _shellcomp.install("bash", "fm")
    rc = (home / ".bashrc").read_text()
    assert rc.count("completion.bash") == 1  # sourced once, not twice


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


# --- pwsh ----------------------------------------------------------------------


def test_pwsh_install_writes_script_and_profile_line(home, monkeypatch):
    profile = home / "pwsh-profile" / "Microsoft.PowerShell_profile.ps1"
    monkeypatch.setattr(_shellcomp, "_pwsh_profile", lambda: profile)
    _shellcomp.install("pwsh", "fm")
    _shellcomp.install("pwsh", "fm")  # idempotent
    script = home / ".local" / "share" / "fm" / "completion.ps1"
    body = script.read_text()
    assert "Register-ArgumentCompleter -Native -CommandName fm" in body
    assert "--complete --" in body
    assert profile.read_text().count("completion.ps1") == 1


def test_pwsh_missing_is_a_taught_error(home, tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef t(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_shellcomp.shutil, "which", lambda _: None)
    assert _app.run(["--install-completion", "powershell"]) == 2  # alias accepted
    assert "not found on PATH" in capsys.readouterr().err


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
