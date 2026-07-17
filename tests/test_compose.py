"""Composition: when=/disabled tasks, include(), plugin entry points."""

from __future__ import annotations

import sys
import textwrap

import pytest

from footman import compose, manifest, registry
from footman.registry import Group, RegistrationError
from footman.testing import Runner

# --- when= / disabled tasks -----------------------------------------------------


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def test_when_false_is_listed_but_disabled():
    def tasks(reg):
        @reg.task(when=False, reason="CI only")
        def release(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["release"]["disabled"] == "CI only"


def test_when_callable_is_reevaluated_live():
    gate = {"open": False}

    def tasks(reg):
        @reg.task(when=lambda: gate["open"], reason="gate closed")
        def guarded():
            print("ran")

    reg, _ = _tree(tasks)
    runner = Runner()

    result = runner.invoke("guarded", tasks=reg)
    assert result.exit_code == 2
    assert "gate closed" in str(result.results[0].error)

    gate["open"] = True  # the manifest is stale now — execution must not care
    result = runner.invoke("guarded", tasks=reg)
    assert result.ok
    assert "ran" in result.stdout


def test_when_raising_predicate_reads_as_unavailable():
    def tasks(reg):
        @reg.task(when=lambda: 1 / 0, reason="broken gate")
        def guarded(): ...

    _, tree = _tree(tasks)
    assert "when() raised ZeroDivisionError" in tree["tasks"]["guarded"]["disabled"]
    reg, _ = _tree(tasks)
    result = Runner().invoke("guarded", tasks=reg)
    assert result.exit_code == 2


def test_disabled_prerequisite_fails_the_dependent():
    ran = []

    def tasks(reg):
        @reg.task(when=False, reason="requires docker")
        def up(): ...

        @reg.task(pre=[up])
        def integration():
            ran.append("integration")

    reg, _ = _tree(tasks)
    result = Runner().invoke("integration", tasks=reg)
    assert result.exit_code == 2  # hard failure, not a silent skip
    assert ran == []  # the dependent was skipped


def test_disabled_annotation_in_listing(fm_project):
    fm = fm_project(
        """
        from footman import task

        @task(when=False, reason="requires docker on PATH")
        def up():
            "Start the containers."
        """
    )
    result = fm.invoke("--list")
    assert "unavailable: requires docker on PATH" in result.stdout
    helped = fm.invoke("--help up")
    assert "unavailable here: requires docker on PATH" in helped.stdout


# --- include() -------------------------------------------------------------------


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A real provider package on sys.path, plus dist-info advertising it."""
    pkg = tmp_path / "shared_tasks.py"
    pkg.write_text(
        textwrap.dedent(
            """
            from footman import task, group

            @task
            def lint(fix: bool = False):
                "Shared lint."
                print(f"lint fix={fix}")

            @task
            def fmt():
                "Shared format."
                print("fmt")

            docs = group("docs", help="Shared docs tasks")

            @docs.task
            def build():
                "Build docs."
                print("docs-build")
            """
        )
    )
    dist = tmp_path / "shared_tasks-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: shared-tasks\nVersion: 1.0\n"
    )
    (dist / "entry_points.txt").write_text("[footman.tasks]\nshared = shared_tasks\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("shared_tasks", None)
    yield tmp_path
    sys.modules.pop("shared_tasks", None)


def test_include_grafts_all(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks")
    assert set(captured.tasks) == {"lint", "fmt"}
    assert set(captured.groups) == {"docs"}


def test_include_cherry_picks_and_namespaces(provider):
    with registry.capture():
        target = Group("sub")
        compose.include("shared_tasks", into=target, only=["lint"])
    assert set(target.tasks) == {"lint"}
    assert not target.groups


def test_include_unknown_only_name_is_a_typo_error(provider):
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match="no task or group named 'lnt'"),
    ):
        compose.include("shared_tasks", only=["lnt"])


def test_include_collision_is_loud_unless_override(provider):
    with registry.capture() as captured:

        @registry.task
        def lint(): ...

        with pytest.raises(RegistrationError, match="already has a task"):
            compose.include("shared_tasks", only=["lint"])
        compose.include("shared_tasks", only=["lint"], override=True)
        assert captured.tasks["lint"] is not lint  # provider's won


def test_include_memoises_per_module(provider):
    with registry.capture() as a:
        compose.include("shared_tasks", only=["lint"])
    with registry.capture() as b:
        compose.include("shared_tasks", only=["fmt"])  # second include still works
    assert set(a.tasks) == {"lint"} and set(b.tasks) == {"fmt"}


def test_included_tasks_run_from_the_includers_dir(provider, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "import pathlib\n"
        "from footman import task, include\n"
        "include('shared_tasks', only=['lint'])\n"
        "@task\n"
        "def where():\n"
        "    import pathlib\n"
        "    print(pathlib.Path.cwd())\n"
    )
    result = Runner().invoke("lint --fix", cwd=project)
    assert result.ok
    assert "lint fix=True" in result.stdout


# --- plugin() / entry points -------------------------------------------------------


def test_plugin_resolves_entry_point(provider):
    tree = compose.plugin("shared")
    assert set(tree.tasks) == {"lint", "fmt"}


def test_plugin_unknown_names_installed(provider):
    with pytest.raises(RegistrationError, match=r"installed: .*shared"):
        compose.plugin("nope")


def test_config_mounts_plugin_as_group(provider, tmp_path):
    project = tmp_path / "proj2"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["shared"]\n'
    )
    (project / "tasks.py").write_text(
        "from footman import task\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("shared lint", cwd=project)
    assert result.ok
    assert "lint fix=False" in result.stdout
    listing = Runner().invoke("--list", cwd=project)
    assert "shared lint" in listing.stdout and "own" in listing.stdout


def test_user_task_shadows_plugin_group(provider, tmp_path):
    project = tmp_path / "proj3"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["shared"]\n'
    )
    (project / "tasks.py").write_text(
        "from footman import task\n@task\ndef shared():\n    print('mine')\n"
    )
    result = Runner().invoke("shared", cwd=project)
    assert result.ok
    assert "mine" in result.stdout  # the user's name wins, silently


def test_missing_configured_plugin_is_exit_2(tmp_path):
    project = tmp_path / "proj4"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["ghost"]\n'
    )
    (project / "tasks.py").write_text(
        "from footman import task\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("own", cwd=project)
    assert result.exit_code == 2
    assert "ghost" in result.stderr
