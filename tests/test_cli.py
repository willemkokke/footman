"""The console-script entry and the completion CLI dispatch."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

import footman
from footman import _complete
from footman._complete import complete_cli


def _completion_names(out: str) -> set[str]:
    """Candidate names from resolver output, dropping `\t` description columns."""
    return {line.split("\t", 1)[0] for line in out.splitlines() if line}


def test_complete_cli_reads_explicit_manifest(tree, tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree}))
    assert complete_cli(["--manifest", str(path), "--", "docs", ""]) == 0
    assert _completion_names(capsys.readouterr().out) == {"serve", "build"}


def test_complete_cli_missing_manifest_is_silent(tmp_path, capsys):
    assert complete_cli(["--manifest", str(tmp_path / "none.json"), "--", ""]) == 0
    assert capsys.readouterr().out == ""


def test_complete_cli_empty_partial_appends_blank(tree, tmp_path, capsys):
    # F16: pwsh drops the trailing "" arg, so its hook passes --empty-partial and
    # the resolver appends the "" itself — completing the fresh position, not the
    # previous word. `--empty-partial` (no trailing "") == "docs" + "".
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree}))
    args = ["--manifest", str(path), "--empty-partial", "--", "docs"]
    assert complete_cli(args) == 0
    assert _completion_names(capsys.readouterr().out) == {"serve", "build"}


# --- stale-while-revalidate completion refresh (D18) --------------------------


def _aged_manifest(tree, tmp_path, max_age, age_s=3600):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree, "completion_max_age": max_age}))
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


def test_swr_fresh_manifest_does_not_spawn(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda: spawns.append(1))
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree, "completion_max_age": 600}))  # just now
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == []


def test_swr_aged_manifest_spawns_and_bumps_mtime(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda: spawns.append(1))
    path = _aged_manifest(tree, tmp_path, 600)
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == [1]
    assert time.time() - os.stat(path).st_mtime < 60  # mtime bumped to ~now


def test_swr_disabled_never_spawns(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda: spawns.append(1))
    path = _aged_manifest(tree, tmp_path, None)  # off
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == []


def test_swr_rapid_tabs_spawn_exactly_once(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda: spawns.append(1))
    path = _aged_manifest(tree, tmp_path, 600)
    complete_cli(["--manifest", str(path), "--", ""])  # aged → spawn + bump mtime
    complete_cli(["--manifest", str(path), "--", ""])  # now fresh → no spawn
    assert spawns == [1]


def test_spawn_refresh_posix_is_detached(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(_complete.os, "name", "posix")
    monkeypatch.setattr(
        _complete.subprocess, "Popen", lambda cmd, **kw: captured.update(cmd=cmd, kw=kw)
    )
    _complete._spawn_refresh()
    assert "_refresh" in " ".join(captured["cmd"])
    assert captured["kw"]["start_new_session"] is True


def test_spawn_refresh_windows_uses_creationflags(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(_complete.os, "name", "nt")
    monkeypatch.setattr(
        _complete.subprocess, "Popen", lambda cmd, **kw: captured.update(cmd=cmd, kw=kw)
    )
    _complete._spawn_refresh()
    assert "creationflags" in captured["kw"]


def test_spawn_refresh_swallows_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("no fork today")

    monkeypatch.setattr(_complete.subprocess, "Popen", boom)
    _complete._spawn_refresh()  # must not raise


def test_refresh_cwd_no_tasks_file_builds_nothing(tmp_path, monkeypatch):
    from footman import _refresh, manifest

    (tmp_path / ".git").mkdir()  # ceiling here, so the cascade can't climb higher
    monkeypatch.chdir(tmp_path)
    built: list[int] = []
    monkeypatch.setattr(manifest, "sync_manifest", lambda *a, **k: built.append(1))
    _refresh.refresh_cwd()  # no tasks.py in the cascade — nothing built, no crash
    assert built == []


def test_completion_max_age_parsing():
    from footman import config

    assert config.completion_max_age({}) == 600  # default
    assert config.completion_max_age({"completion": {"max_age": "30s"}}) == 30
    assert config.completion_max_age({"completion": {"max_age": "5m"}}) == 300
    assert config.completion_max_age({"completion": {"max_age": "1h"}}) == 3600
    assert config.completion_max_age({"completion": {"max_age": "2d"}}) == 172800
    assert config.completion_max_age({"completion": {"max_age": "off"}}) is None
    assert config.completion_max_age({"completion": {"max_age": "none"}}) is None
    assert config.completion_max_age({"completion": {"max_age": 0}}) is None
    assert config.completion_max_age({"completion": {"max_age": -5}}) is None
    assert config.completion_max_age({"completion": {"max_age": 120}}) == 120
    assert config.completion_max_age({"completion": {"max_age": True}}) == 600
    assert config.completion_max_age({"completion": {"max_age": False}}) is None
    assert config.completion_max_age({"completion": {"max_age": "garbage"}}) == 600
    assert (
        config.completion_max_age({"completion": {"max_age": []}}) == 600
    )  # non-scalar


def test_refresh_cwd_rebuilds_the_manifest(tmp_path, monkeypatch):
    # The background child rebuilds the cwd cascade's manifest end-to-end.
    from footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef hi(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_cwd()
    data = json.loads(_paths.manifest_path(tmp_path).read_text())
    assert "hi" in data["tree"]["tasks"]
    assert data["completion_max_age"] == 600  # baked from the default


def test_refresh_source_rebuilds_the_manifest(tmp_path, monkeypatch):
    # The cold-build child rebuilds one -f file's (cwd, file) manifest — keyed
    # apart from the cwd cascade, with no background refresh (max_age 0).
    from pathlib import Path

    from footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "other.py").write_text(
        "from footman import task\n@task\ndef ship(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_source("other.py")
    data = json.loads(
        _paths.source_manifest_path(tmp_path, Path("other.py")).read_text()
    )
    assert "ship" in data["tree"]["tasks"]
    assert data["completion_max_age"] == 0  # -f: rebuilt on demand, not in the bg
    assert data["tasks_file"] == "other.py"  # baked, keyed apart from the cascade


def test_refresh_source_missing_file_builds_nothing(tmp_path, monkeypatch):
    from footman import _refresh, manifest

    monkeypatch.chdir(tmp_path)
    built: list[int] = []
    monkeypatch.setattr(manifest, "sync_manifest", lambda *a, **k: built.append(1))
    _refresh.refresh_source("nope.py")  # the -f value names no file — nothing built
    assert built == []


def test_main_dispatches_complete(tree, tmp_path, monkeypatch, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"tree": tree}))
    monkeypatch.setattr(
        sys, "argv", ["fm", "--complete", "--manifest", str(path), "--", "che"]
    )
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert _completion_names(capsys.readouterr().out) == {"check"}


def test_main_dispatches_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fm", "--version"])
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert "footman" in capsys.readouterr().out


def test_lazy_reexports():
    # F56: every __all__ entry must resolve (via __getattr__ or as a real attr)
    # — a permanent drift guard for the lazy public surface.
    for name in footman.__all__:
        assert getattr(footman, name) is not None, name
    with pytest.raises(AttributeError):
        _ = footman.does_not_exist
