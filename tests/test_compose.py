"""Composition: @requires availability gates, include(), plugin entry points."""

from __future__ import annotations

import sys
import textwrap

import pytest

from footman import compose, manifest, registry
from footman.registry import (
    Group,
    RegistrationError,
    requires,
    requires_dep,
    requires_env,
    requires_tool,
)
from footman.testing import Runner

# --- @requires availability gates -----------------------------------------------


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def test_requires_false_predicate_is_listed_but_disabled():
    def tasks(reg):
        @reg.task(name="release")
        @requires(lambda: False, reason="CI only")
        def release(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["release"]["disabled"] == "CI only"


def test_requires_predicate_is_reevaluated_live():
    gate = {"open": False}

    def tasks(reg):
        @reg.task(name="guarded")
        @requires(lambda: gate["open"], reason="gate closed")
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


def test_requires_raising_predicate_reads_as_unavailable():
    def tasks(reg):
        @reg.task(name="guarded")
        @requires(lambda: 1 / 0, reason="broken gate")
        def guarded(): ...

    _, tree = _tree(tasks)
    assert "broken gate (ZeroDivisionError" in tree["tasks"]["guarded"]["disabled"]
    reg, _ = _tree(tasks)
    result = Runner().invoke("guarded", tasks=reg)
    assert result.exit_code == 2


def test_requires_dep_present_is_available():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("io")  # a stdlib module: always importable
        def publish(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]


def test_requires_dep_missing_is_listed_but_disabled():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope", "google_nope")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "requires stripe_nope, google_nope"

    reg, _ = _tree(tasks)
    result = Runner().invoke("publish", tasks=reg)
    assert result.exit_code == 2
    assert "requires stripe_nope" in str(result.results[0].error)


def test_requires_dep_custom_reason():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope", reason="pip install devkit[release]")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "pip install devkit[release]"


def test_requires_dep_does_not_import(monkeypatch):
    # Availability is find_spec-only: building the manifest for a task that
    # requires a module must never import that module.
    import sys

    calls = []
    real = __import__

    def tracking_import(name, *a, **k):
        if name == "textwrap":
            calls.append(name)
        return real(name, *a, **k)

    # monkeypatch.delitem evicts textwrap AND restores it on teardown — a bare
    # sys.modules.pop here would leak the eviction into the rest of the session.
    monkeypatch.delitem(sys.modules, "textwrap", raising=False)
    monkeypatch.setattr("builtins.__import__", tracking_import)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("textwrap")  # importable, but must stay unimported
        def publish(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]  # found via find_spec
    assert calls == []  # ...without importing it
    assert "textwrap" not in sys.modules


def test_requires_dep_broken_parent_lists_unavailable_not_crash(tmp_path, monkeypatch):
    # A dotted dep imports parent packages via find_spec; a parent whose
    # __init__ raises must read as unavailable, never crash fm --list.
    pkg = tmp_path / "brokenparent"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('parent boom')\n")
    (pkg / "child.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("brokenparent.child")
        def publish(): ...

    _, tree = _tree(tasks)  # must not raise
    assert tree["tasks"]["publish"]["disabled"] == "requires brokenparent.child"


def test_requires_env_gates_on_the_environment(monkeypatch):
    monkeypatch.delenv("FM_GATE_VAR", raising=False)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_env("FM_GATE_VAR")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "set FM_GATE_VAR"

    monkeypatch.setenv("FM_GATE_VAR", "1")  # live: set it and it clears
    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]


def test_requires_tool_gates_on_path():
    def tasks(reg):
        @reg.task(name="up")
        @requires_tool("no_such_tool_xyz")
        def up(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["up"]["disabled"] == "requires no_such_tool_xyz on PATH"


def test_requires_tool_present_is_available(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def tasks(reg):
        @reg.task(name="up")
        @requires_tool("docker")
        def up(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["up"]


def test_requires_collects_all_failures(monkeypatch):
    # No short-circuit: a task gated on a missing dep AND a missing var reports
    # both, each in its own words.
    monkeypatch.delenv("FM_GATE_VAR", raising=False)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope")
        @requires_env("FM_GATE_VAR")
        def publish(): ...

    _, tree = _tree(tasks)
    disabled = tree["tasks"]["publish"]["disabled"]
    assert "requires stripe_nope" in disabled
    assert "set FM_GATE_VAR" in disabled


def test_disabled_prerequisite_fails_the_dependent():
    ran = []

    def tasks(reg):
        @reg.task(name="up")
        @requires(lambda: False, reason="requires docker")
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
        from footman import task, requires

        @task
        @requires(lambda: False, reason="requires docker on PATH")
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


def test_include_missing_module_names_the_call_not_the_file():
    # A missing module used to surface as "failed to import <tasks.py>", blaming
    # the file. Now it names the include() call and the reason.
    with (
        registry.capture(),
        pytest.raises(
            RegistrationError,
            match=r"include\('no_such_provider_xyz'\): failed to import "
            r"\(ModuleNotFoundError",
        ),
    ):
        compose.include("no_such_provider_xyz")


def test_include_collision_is_loud_unless_override(provider):
    with registry.capture() as captured:

        @registry.task
        def lint(): ...

        with pytest.raises(RegistrationError, match="already has a task"):
            compose.include("shared_tasks", only=["lint"])
        compose.include("shared_tasks", only=["lint"], override=True)
        assert captured.tasks["lint"] is not lint  # provider's won


def test_include_forks_provider_tree_no_memo_leak(provider):
    # F38: grafting a provider group hands the project a private copy — a later
    # mutation (as the cascade overlay/tag does) must not leak into the shared
    # _module_trees memo and thus into the next in-process invocation.
    with registry.capture():
        target = Group("proj")
        compose.include("shared_tasks", into=target)  # grafts lint/fmt + docs

    target.groups["docs"].tasks["injected"] = lambda: None
    memo = compose._module_trees["shared_tasks"]
    assert "injected" not in memo.groups["docs"].tasks  # memo untouched
    assert target.tasks["lint"] is memo.tasks["lint"]  # fns still shared


def test_include_memoises_per_module(provider):
    with registry.capture() as a:
        compose.include("shared_tasks", only=["lint"])
    with registry.capture() as b:
        compose.include("shared_tasks", only=["fmt"])  # second include still works
    assert set(a.tasks) == {"lint"} and set(b.tasks) == {"fmt"}


def test_included_tasks_run_from_the_includers_dir(tmp_path, monkeypatch):
    # F58: an included provider task is stamped with the INCLUDER's directory,
    # not the provider module's. Observe it directly: the task prints ctx.cwd,
    # which must equal the includer's project dir even though the provider lives
    # elsewhere on disk.
    provider_dir = tmp_path / "elsewhere"
    provider_dir.mkdir()
    (provider_dir / "prov.py").write_text(
        "from footman import task\n@task\ndef show(ctx):\n    print(ctx.cwd)\n"
    )
    monkeypatch.syspath_prepend(str(provider_dir))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text("from footman import include\ninclude('prov')\n")

    result = Runner().invoke("show", cwd=project)
    assert result.ok
    printed = [ln.strip() for ln in result.stdout.splitlines()]
    assert str(project) in printed  # ctx.cwd = the includer's dir
    assert str(provider_dir) not in result.stdout  # not the provider module's


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


def _advertise(tmp_path, monkeypatch, module, body, entry):
    """Put a provider *module* on sys.path with dist-info advertising *entry*."""
    (tmp_path / f"{module}.py").write_text(textwrap.dedent(body))
    dist = tmp_path / f"{module}-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {module}\nVersion: 1.0\n"
    )
    (dist / "entry_points.txt").write_text(f"[footman.tasks]\n{entry}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop(module, None)


def test_plugin_import_failure_is_taught(tmp_path, monkeypatch):
    # F07: a plugin that fails to import teaches instead of dumping a traceback.
    _advertise(
        tmp_path,
        monkeypatch,
        "broken_plugin",
        "import totally_missing_dep_xyz  # noqa\n",
        "broken = broken_plugin",
    )
    with pytest.raises(RegistrationError, match="failed to import"):
        compose.plugin("broken")


def test_dotted_plugin_name_nests_and_shares_namespace(tmp_path, monkeypatch):
    # A plugin's name is its command path: two dotted names sharing a prefix
    # mount under one auto-created `suite` group, neither owning it.
    _advertise(
        tmp_path,
        monkeypatch,
        "nest_alpha",
        """
        from footman import group

        tasks = group("alpha", help="Alpha tasks")

        @tasks.task
        def go():
            "Go alpha."
            print("alpha-go!")
        """,
        "suite.alpha = nest_alpha:tasks",
    )
    _advertise(
        tmp_path,
        monkeypatch,
        "nest_beta",
        """
        from footman import group

        tasks = group("beta", help="Beta tasks")

        @tasks.task
        def go():
            "Go beta."
        """,
        "suite.beta = nest_beta:tasks",
    )
    project = tmp_path / "proj_nest"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["suite.alpha", "suite.beta"]\n'
    )
    (project / "tasks.py").write_text(
        "from footman import task\n@task\ndef own(): ...\n"
    )
    listing = Runner().invoke("--list", cwd=project)
    assert listing.ok
    # Both leaves live under the one shared `suite` namespace group.
    assert "suite alpha go" in listing.stdout and "suite beta go" in listing.stdout
    ran = Runner().invoke("suite alpha go", cwd=project)
    assert ran.ok and "alpha-go!" in ran.stdout


def test_broken_plugin_config_mount_is_exit_2(tmp_path, monkeypatch):
    # F07 end-to-end: a broken config-mounted plugin is a clean exit 2, not a
    # raw traceback on every invocation.
    _advertise(
        tmp_path,
        monkeypatch,
        "broken2_plugin",
        "import totally_missing_dep_xyz  # noqa\n",
        "broken2 = broken2_plugin",
    )
    project = tmp_path / "proj_broken"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["broken2"]\n'
    )
    (project / "tasks.py").write_text(
        "from footman import task\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("own", cwd=project)
    assert result.exit_code == 2
    assert "failed to import" in result.stderr


def test_plugin_explicit_group_module_is_adopted(tmp_path, monkeypatch):
    # F08: an entry point naming a *module* that registers nothing but exposes
    # one explicit Group is adopted — the documented provider convention, which
    # previously hit the misleading "already imported outside include()" error.
    _advertise(
        tmp_path,
        monkeypatch,
        "explicit_plugin",
        """
        from footman.registry import Group

        tasks = Group("explicit", "Explicit provider")

        @tasks.task
        def ping():
            print("pong")
        """,
        "explicit = explicit_plugin",
    )
    tree = compose.plugin("explicit")
    assert set(tree.tasks) == {"ping"}


# --- include()/plugin() taught errors -----------------------------------------
# footman markets its error messages; the ones nobody had exercised are
# exactly the ones that can rot. Each of these asserts the *teaching*, not
# just the raising.


def test_include_of_a_pre_imported_module_teaches(tmp_path, monkeypatch):
    """A module already imported outside include() never had its tasks
    captured — re-executing it would double every side effect, so footman
    refuses with guidance instead of guessing."""
    (tmp_path / "early_tasks.py").write_text(
        "from footman import task\n\n@task\ndef early():\n    'Early.'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "early_tasks", raising=False)
    import early_tasks  # type: ignore[import-not-found]  # the wrong way, on purpose

    with pytest.raises(RegistrationError, match="already imported outside"):
        compose.include(early_tasks)


def test_include_of_a_module_with_no_tasks_teaches(tmp_path, monkeypatch):
    """Nothing to adopt: the message says what to define, and counts what
    it did find."""
    (tmp_path / "empty_tasks.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "empty_tasks", raising=False)
    with pytest.raises(RegistrationError, match="no module-level Group"):
        compose.include("empty_tasks")


def test_include_of_a_module_with_two_groups_teaches(tmp_path, monkeypatch):
    """Ambiguous: two Groups and no tasks means footman cannot know which
    one you meant — it says so, with the count."""
    # Group(...) constructs without registering; group(...) would register
    # and the module would no longer be "no tasks at all".
    (tmp_path / "two_groups.py").write_text(
        "from footman.registry import Group\n\na = Group('a')\nb = Group('b')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "two_groups", raising=False)
    with pytest.raises(RegistrationError, match="2 Groups"):
        compose.include("two_groups")


def test_include_accepts_a_group_directly(tmp_path):
    """The simplest source of all: a Group object you already hold."""
    donor = Group("donor")

    @donor.task
    def ship():
        "Ship."

    root = Group("root")
    compose.include(donor, into=root)
    assert "ship" in root.tasks


def test_plugin_claimed_by_two_distributions_teaches(monkeypatch):
    """Two dists advertising the same plugin name is ambiguous — the error
    names both so the user can uninstall one."""

    class FakeEP:
        def __init__(self, dist):
            self.name = "twice"
            self.dist = dist
            self.group = compose.ENTRY_POINT_GROUP

    import importlib.metadata

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP("alpha 1.0"), FakeEP("beta 2.0")],
    )
    monkeypatch.setattr(compose, "_module_trees", {})
    with pytest.raises(RegistrationError, match=r"more than one distribution"):
        compose.plugin("twice")


# --- _fork carries every Group field (F: default_task/finalizers were dropped) --


def test_fork_copies_every_group_field():
    # A structural census: _fork must carry EVERY Group field, or a composed
    # group silently loses it — which is exactly how `@group.default` and
    # `@finalize` hooks vanished across include(). This fails the moment a field
    # is added to Group.__init__ without teaching _fork (and this test) to copy
    # it, so a new field can't be dropped in silence.
    assert set(vars(Group("x"))) == {
        "name",
        "help",
        "tasks",
        "groups",
        "default_task",
        "finalizers",
    }


def test_fork_preserves_default_and_finalizers():
    src = Group("release", "Release tasks")

    @src.task
    def notes(): ...

    @src.default
    def run(*, armed: bool = False): ...

    def sentinel(tasks): ...

    src.finalizers.append(sentinel)

    fork = compose._fork(src)
    assert fork.default_task is src.default_task  # shared fn, like the task fns
    assert fork.finalizers == [sentinel]
    assert fork.finalizers is not src.finalizers  # a fresh list — no memo leak


@pytest.fixture
def default_provider(tmp_path, monkeypatch):
    """A provider module whose group carries a `@group.default`."""
    (tmp_path / "reltasks.py").write_text(
        textwrap.dedent(
            """
            from footman import group

            release = group("release", help="Release tasks")

            @release.default
            def run(*, armed: bool = False):
                "Cut a release."
                print(f"release armed={armed}")

            @release.task
            def notes():
                "Show the notes."
                print("notes")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("reltasks", None)
    yield tmp_path
    sys.modules.pop("reltasks", None)


def test_include_preserves_group_default(default_provider):
    # F: include() grafted the subtasks but dropped the group's @group.default,
    # so the bare-runnable group broke and its options vanished from the manifest.
    with registry.capture() as captured:
        compose.include("reltasks")
    assert captured.groups["release"].default_task is not None
    tree = manifest.build_manifest(captured)["tree"]
    node = tree["groups"]["release"]
    assert "default" in node  # the runnable-group node the splitter/help read
    assert [p["name"] for p in node["default"]["params"]] == ["armed"]


def test_included_group_default_runs_end_to_end(default_provider, tmp_path):
    project = tmp_path / "proj_default"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from footman import include\ninclude('reltasks')\n"
    )
    result = Runner().invoke("release --armed", cwd=project)
    assert result.ok, result.stderr
    assert "release armed=True" in result.stdout


def test_include_runs_provider_finalizers(tmp_path, monkeypatch):
    # A provider's @finalize hook edits the whole tree; include() must surface it
    # on the live root so discovery collects and runs it — it was dropped before.
    (tmp_path / "finmod.py").write_text(
        textwrap.dedent(
            """
            from footman import task, finalize

            @task
            def build():
                "Build it."
                print("build")

            @finalize
            def note(tasks):
                tasks["build"].disable("finalizer ran")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("finmod", None)

    project = tmp_path / "proj_fin"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from footman import include\ninclude('finmod')\n"
    )
    listing = Runner().invoke("--list", cwd=project)
    assert listing.ok
    assert "finalizer ran" in listing.stdout  # the hook ran and disabled the task


def test_plugin_entry_point_of_the_wrong_type_teaches(monkeypatch):
    """An entry point resolving to something that isn't a Group (or a
    module of tasks) names the type it got."""

    class FakeEP:
        name = "wrong"
        dist = "wrong 1.0"
        group = compose.ENTRY_POINT_GROUP

        def load(self):
            return 42

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [FakeEP()])
    monkeypatch.setattr(compose, "_module_trees", {})
    with pytest.raises(RegistrationError, match="got int"):
        compose.plugin("wrong")
