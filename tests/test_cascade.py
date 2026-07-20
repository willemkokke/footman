"""The monorepo cascade: discovery, merge, defining-dir cwd, config, caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from footman import _app, _paths, config, discover, executor, registry
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


def test_footman_toml_marks_a_project_root(tmp_path):
    # F43: a footman.toml-only root (e.g. a Docker context with .git ignored) is
    # a project root, discoverable from a subdirectory.
    (tmp_path / "footman.toml").write_text("sequential = true\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_project_root(deep) == tmp_path


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


def test_cascade_isolates_sibling_helpers(tmp_path, capsys):
    # F14: two tasks files each `import helpers` from their own dir — each must
    # bind ITS OWN helpers module, not whoever-imported-first-wins.
    (tmp_path / "helpers.py").write_text("VALUE = 'root'\n")
    root = _write(
        tmp_path / "tasks.py",
        "from footman import task\nimport helpers\n"
        "@task\ndef a():\n    print(helpers.VALUE)\n",
    )
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "helpers.py").write_text("VALUE = 'svc'\n")
    sub = _write(
        svc / "tasks.py",
        "from footman import task\nimport helpers\n"
        "@task\ndef b():\n    print(helpers.VALUE)\n",
    )
    merged = discover.load_tree([root, sub])
    merged.tasks["a"]()
    merged.tasks["b"]()
    out = capsys.readouterr().out
    assert "root" in out and "svc" in out  # each resolved its own sibling


def test_failed_cascade_import_resets_registry(tmp_path):
    # F62: a file that registers a task then raises must not strand ghost tasks
    # in the global registry for the rest of the process.
    bad = _write(
        tmp_path / "tasks.py",
        "from footman import task\n@task\ndef ghost(): ...\n"
        "raise RuntimeError('boom')\n",
    )
    with pytest.raises(discover.TasksImportError):
        discover.load_tree([bad])
    assert "ghost" not in registry.root.tasks


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


def test_config_global_file_is_the_bottom_rung(tmp_path, monkeypatch):
    # The user-level file seeds the merge; every project layer beats it.
    global_file = _write(tmp_path / "global.toml", "uv = false\ntasks = 'g.py'\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    project = tmp_path / "proj"
    project.mkdir()
    cfg = config.load_config(project, project)
    assert cfg == {"uv": False, "tasks": "g.py"}  # global alone applies
    _write(project / "footman.toml", "tasks = 'p.py'\n")
    cfg = config.load_config(project, project)
    assert cfg["tasks"] == "p.py"  # the cascade wins the contested key
    assert cfg["uv"] is False  # and the uncontested global key survives


def test_config_global_default_location(tmp_path, monkeypatch):
    # Without FOOTMAN_CONFIG, the file lives under XDG config home.
    monkeypatch.delenv("FOOTMAN_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    spot = tmp_path / "xdg" / "footman" / "config.toml"
    spot.parent.mkdir(parents=True)
    _write(spot, "sequential = true\n")
    project = tmp_path / "proj"
    project.mkdir()
    assert config.load_config(project, project)["sequential"] is True


def test_config_malformed_global_warns_and_is_skipped(tmp_path, monkeypatch):
    global_file = _write(tmp_path / "global.toml", "not [[ toml")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    warnings: list[str] = []
    cfg = config.load_config(tmp_path, tmp_path, on_warning=warnings.append)
    assert cfg == {}
    assert any("malformed" in w for w in warnings)


def test_config_user_level_keys_stripped_from_the_cascade(tmp_path, monkeypatch):
    # `gc` governs the shared cache: a per-project value would lie. It only
    # counts from the user-level file; cascade files get a note (verbose runs
    # wire on_note; others pass None and the strip is silent).
    global_file = _write(tmp_path / "global.toml", "gc = false\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    _write(tmp_path / "footman.toml", "gc = true\ntasks = 'x.py'\n")
    notes: list[str] = []
    cfg = config.load_config(tmp_path, tmp_path, on_note=notes.append)
    assert cfg["gc"] is False  # the global value, not the project's
    assert cfg["tasks"] == "x.py"  # ordinary keys cascade as ever
    assert any("user-level" in n for n in notes)
    assert config.load_config(tmp_path, tmp_path)["gc"] is False  # silent too


def test_config_cli_path_replaces_global_and_cascade(tmp_path, monkeypatch):
    # --config is total control: the named file is exactly what applies.
    global_file = _write(tmp_path / "global.toml", "uv = false\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    _write(tmp_path / "footman.toml", "sequential = true\n")
    override = _write(tmp_path / "custom.toml", "tasks = 'b.py'\n")
    cfg = config.load_config(tmp_path, tmp_path, str(override))
    assert cfg == {"tasks": "b.py"}  # no uv, no sequential: replaced, not merged


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


def test_ceiling_excludes_files_above_git(tmp_path, monkeypatch, capsys):
    # A tasks.py ABOVE the .git root must not enter the cascade. The repo
    # nests inside this test's own tmp_path — writing to the *shared*
    # pytest basetemp (a fixture's parent) once poisoned every later test
    # whose ceiling walk reached it, invisibly in alphabetical runs.
    _write(
        tmp_path / "tasks.py",
        "from footman import task\n@task\ndef outside():...\n",
    )
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _write(
        repo / "svc" / "api" / "tasks.py",
        "from footman import task\n@task\ndef serve():...\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(repo / "svc" / "api")
    assert _app.run(["-l"]) == 0
    out = capsys.readouterr().out
    assert "serve" in out  # the repo's own cascade is intact
    assert "outside" not in out


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


# --- inherited(): extending an overridden task --------------------------------


def _inherit_repo(tmp_path, monkeypatch, leaf_body: str):
    """A three-level cascade whose leaf overrides `check`."""
    (tmp_path / ".git").mkdir()
    _write(
        tmp_path / "tasks.py",
        "from footman import task, run\n"
        "@task\ndef check(fix: bool = False):\n"
        '    """Root gate."""\n'
        '    run(f"echo root fix={fix}")\n',
    )
    _write(
        tmp_path / "svc" / "tasks.py",
        "from footman import inherited, task, run\n"
        "@task\ndef check(fix: bool = False):\n"
        '    """Mid gate."""\n'
        "    inherited()(fix=fix)\n"
        '    run("echo mid")\n',
    )
    _write(tmp_path / "svc" / "api" / "tasks.py", leaf_body)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path / "svc" / "api")
    return tmp_path


LEAF = (
    "from footman import inherited, task, run\n"
    "@task\ndef check(fix: bool = False, contracts: bool = True):\n"
    '    """Leaf gate."""\n'
    "    inherited()(fix=fix)\n"
    '    if contracts:\n        run("echo leaf")\n'
)


def test_inherited_walks_the_whole_cascade(tmp_path, monkeypatch, capsys):
    # Three levels deep: the leaf calls the mid, which calls the root —
    # each extending the last, in order.
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["check", "--fix"]) == 0
    out = capsys.readouterr().out
    assert out.index("root fix=True") < out.index("mid") < out.index("leaf")


def test_inherited_names_the_task_it_calls(tmp_path, monkeypatch, capsys):
    # functools.wraps keeps the name, so `parallel(inherited(), extra)`
    # labels its live line honestly instead of showing an anonymous call.
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    from footman import Context, discover, inherited, use_context

    files = _paths.task_files(Path.cwd(), tmp_path)
    tree = discover.load_tree(files)
    with use_context(Context(fn=tree.tasks["check"])):
        assert inherited().__name__ == "check"


def test_inherited_forwarding_is_explicit(tmp_path, monkeypatch, capsys):
    # The leaf chooses what to pass: the root never sees --contracts, and
    # can be given a different value entirely.
    leaf = (
        "from footman import inherited, task, run\n"
        "@task\ndef check(fix: bool = False, contracts: bool = True):\n"
        '    """Leaf gate."""\n'
        "    inherited()(fix=False)\n"
    )
    _inherit_repo(tmp_path, monkeypatch, leaf)
    assert _app.run(["check", "--fix"]) == 0
    assert "root fix=False" in capsys.readouterr().out


def test_inherited_without_a_shadow_is_taught(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    _write(
        tmp_path / "tasks.py",
        "from footman import inherited, task\n"
        "@task\ndef solo():\n"
        '    """No parent."""\n'
        "    inherited()\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    assert _app.run(["solo"]) != 0
    err = capsys.readouterr().err
    assert "does not shadow an inherited task" in err
    assert "--where solo" in err  # the message names the discovery command


def test_where_lists_the_shadow_chain(tmp_path, monkeypatch, capsys):
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["--where", "check"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3  # leaf, mid, root
    assert lines[0].endswith("api/tasks.py:2") and "(shadowed)" not in lines[0]
    assert all("(shadowed)" in line for line in lines[1:])


def test_help_shows_the_inherited_options(tmp_path, monkeypatch, capsys):
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["--help", "check"]) == 0
    out = capsys.readouterr().out
    assert "shadows" in out and "inherited() calls it" in out
    assert "fm check [--fix]" in out  # the parent's options, not the leaf's
