"""Shell completion installers: script generation and idempotent install."""

from __future__ import annotations

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
    body = _shellcomp.script_for("bash", "hse-tool")
    assert "hse-tool --complete --" in body
    assert "_hse_tool_complete" in body  # function names sanitised


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
