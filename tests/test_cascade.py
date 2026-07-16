"""The monorepo cascade: discovery, merge, defining-dir cwd, config, caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from footman import _app, _paths, config, discover, executor
from footman.context import Context
from footman.split import Segment

# --- path primitives ---------------------------------------------------------


def test_find_repo_root_stops_at_git(tmp_path):
    (tmp_path / ".git").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_repo_root(deep) == tmp_path


def test_find_repo_root_without_git_falls_back(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    deep = tmp_path / "a"
    deep.mkdir()
    assert _paths.find_repo_root(deep) == tmp_path  # via find_project_root


def test_dir_chain_is_root_first(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.dir_chain(deep, tmp_path) == [tmp_path, tmp_path / "a", deep]


def test_dir_chain_unrelated_ceiling_is_just_cwd(tmp_path):
    other = tmp_path / "sibling"
    other.mkdir()
    cwd = tmp_path / "here"
    cwd.mkdir()
    assert _paths.dir_chain(cwd, other) == [cwd]


def test_task_files_collects_existing_only(tmp_path):
    (tmp_path / "tasks.py").write_text("")
    (tmp_path / "a").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir()
    (deep / "tasks.py").write_text("")  # 'a' has none
    files = _paths.task_files(deep, tmp_path)
    assert files == [tmp_path / "tasks.py", deep / "tasks.py"]


def test_manifest_path_is_per_directory(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _paths.manifest_path(a) != _paths.manifest_path(b)


# --- merge semantics ---------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_cascade_appends_new_names(tmp_path):
    root = _write(
        tmp_path / "tasks.py", "from footman import task\n@task\ndef a():...\n"
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py", "from footman import task\n@task\ndef b():...\n"
    )
    merged = discover.load_tree([root, sub])
    assert set(merged.tasks) == {"a", "b"}


def test_cascade_local_overrides_by_name(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        "from footman import task\n@task\ndef build():\n    return 1\n",
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from footman import task\n@task\ndef build():\n    return 0\n",
    )
    merged = discover.load_tree([root, sub])
    # the local (svc) build wins, and is tagged with the svc directory
    assert discover.defining_dir(merged.tasks["build"]) == str(tmp_path / "svc")
    assert merged.tasks["build"]() == 0


def test_cascade_merges_groups(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        "from footman import group\nd = group('dist')\n@d.task\ndef build():...\n",
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from footman import group\nd = group('dist')\n@d.task\ndef deploy():...\n",
    )
    merged = discover.load_tree([root, sub])
    assert set(merged.groups["dist"].tasks) == {"build", "deploy"}


def test_cascade_tags_defining_dir(tmp_path):
    root = _write(
        tmp_path / "tasks.py", "from footman import task\n@task\ndef a():...\n"
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py", "from footman import task\n@task\ndef b():...\n"
    )
    merged = discover.load_tree([root, sub])
    assert discover.defining_dir(merged.tasks["a"]) == str(tmp_path)
    assert discover.defining_dir(merged.tasks["b"]) == str(tmp_path / "svc")


def test_load_tree_leaves_no_global_state(tmp_path):
    from footman import registry

    root = _write(
        tmp_path / "tasks.py", "from footman import task\n@task\ndef a():...\n"
    )
    discover.load_tree([root])
    assert registry.root.tasks == {}  # reset after building


# --- defining-dir cwd at execution -------------------------------------------


def test_run_task_uses_defining_dir_as_cwd():
    def fn():
        return 0

    fn._footman_dir = "/some/place"  # type: ignore[attr-defined]
    ctx = Context()
    seg = Segment(task="fn", path=["fn"])
    executor.run_task(fn, seg, ctx)
    assert ctx.cwd == Path("/some/place")


def test_run_task_respects_explicit_cwd():
    def fn():
        return 0

    fn._footman_dir = "/some/place"  # type: ignore[attr-defined]
    ctx = Context(cwd=Path("/explicit"))
    executor.run_task(fn, Segment(task="fn", path=["fn"]), ctx)
    assert ctx.cwd == Path("/explicit")  # not overridden


# --- config discovery --------------------------------------------------------


def test_config_nearest_wins(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.footman]\ntasks = 'root.py'\n")
    sub = tmp_path / "svc"
    sub.mkdir()
    _write(sub / "footman.toml", "tasks = 'svc.py'\n")
    cfg = config.load_config(sub, tmp_path)
    assert cfg["tasks"] == "svc.py"  # cwd folder overrides the root


def test_config_footman_toml_beats_pyproject_in_same_dir(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.footman]\nsequential = false\n")
    _write(tmp_path / "footman.toml", "sequential = true\n")
    cfg = config.load_config(tmp_path, tmp_path)
    assert cfg["sequential"] is True


def test_config_cli_path_overrides_all(tmp_path):
    _write(tmp_path / "footman.toml", "tasks = 'a.py'\n")
    override = _write(tmp_path / "custom.toml", "tasks = 'b.py'\n")
    cfg = config.load_config(tmp_path, tmp_path, str(override))
    assert cfg["tasks"] == "b.py"


def test_config_corrupt_toml_is_ignored(tmp_path):
    _write(tmp_path / "footman.toml", "this is : not [[ valid")
    assert config.load_config(tmp_path, tmp_path) == {}


# --- end-to-end through the app ----------------------------------------------


@pytest.fixture
def mono(tmp_path, monkeypatch):
    """A monorepo: .git at the root, tasks at root and in svc/api."""
    (tmp_path / ".git").mkdir()
    _write(
        tmp_path / "tasks.py",
        "from footman import task\n"
        "@task\ndef build():\n    print('root-build')\n"
        "@task\ndef test():\n    print('root-test')\n",
    )
    _write(
        tmp_path / "svc" / "api" / "tasks.py",
        "from footman import task\n"
        "@task\ndef serve():\n    print('api-serve')\n"
        "@task\ndef build():\n    print('api-build')\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_app_lists_merged_tasks(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["-l"]) == 0
    out = capsys.readouterr().out
    assert "build" in out and "test" in out and "serve" in out


def test_app_local_override_runs(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["build"]) == 0
    assert "api-build" in capsys.readouterr().out  # not root-build


def test_app_inherited_task_runs_from_subdir(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["test"]) == 0  # inherited from root
    assert "root-test" in capsys.readouterr().out


def test_ceiling_excludes_files_above_git(mono, monkeypatch, capsys):
    # a tasks.py ABOVE the .git root must not enter the cascade
    _write(
        mono.parent / "tasks.py",
        "from footman import task\n@task\ndef outside():...\n",
    )
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["-l"]) == 0
    assert "outside" not in capsys.readouterr().out


def test_per_cwd_manifest_files_differ(mono, monkeypatch):
    monkeypatch.chdir(mono)
    _app.run(["-l"])
    root_cache = _paths.manifest_path(mono)
    monkeypatch.chdir(mono / "svc" / "api")
    _app.run(["-l"])
    api_cache = _paths.manifest_path(mono / "svc" / "api")
    assert root_cache.exists() and api_cache.exists()
    assert root_cache != api_cache


def test_config_sequential_default(mono, monkeypatch, capsys):
    _write(mono / "footman.toml", "sequential = true\n")
    monkeypatch.chdir(mono)
    # in --json mode, sequential still runs both; assert the run succeeds
    assert _app.run(["--json", "build", "test"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["task"] for r in payload["results"]] == ["build", "test"]


def test_config_tasks_filename_in_cascade(mono, monkeypatch, capsys):
    _write(mono / "footman.toml", "tasks = 'jobs.py'\n")
    _write(
        mono / "jobs.py",
        "from footman import task\n@task\ndef custom():\n    print('via-jobs')\n",
    )
    monkeypatch.chdir(mono)
    assert _app.run(["custom"]) == 0
    assert "via-jobs" in capsys.readouterr().out
