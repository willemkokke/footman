"""The console-script entry and the completion CLI dispatch."""

from __future__ import annotations

import json
import sys

import pytest

import footman
from footman._complete import complete_cli


def test_complete_cli_reads_explicit_manifest(tree, tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree}))
    assert complete_cli(["--manifest", str(path), "--", "docs", ""]) == 0
    assert set(capsys.readouterr().out.split()) == {"serve", "build"}


def test_complete_cli_missing_manifest_is_silent(tmp_path, capsys):
    assert complete_cli(["--manifest", str(tmp_path / "none.json"), "--", ""]) == 0
    assert capsys.readouterr().out == ""


def test_main_dispatches_complete(tree, tmp_path, monkeypatch, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree}))
    monkeypatch.setattr(
        sys, "argv", ["fm", "--complete", "--manifest", str(path), "--", "che"]
    )
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.split() == ["check"]


def test_main_dispatches_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fm", "--version"])
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert "footman" in capsys.readouterr().out


def test_lazy_reexports():
    assert footman.task is not None
    assert footman.group is not None
    assert footman.Group is not None
    with pytest.raises(AttributeError):
        _ = footman.does_not_exist
