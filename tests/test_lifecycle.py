"""The `pre_tasks` lifecycle hook: the invocation, before anything runs."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from footman import discover, manifest, registry
from footman.registry import Group, RegistrationError
from footman.testing import Runner


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def test_a_hook_edits_the_merged_tree(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @task
        def deploy_web(): ...

        @footman.pre_tasks
        def gate(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if t.name.startswith("deploy"):
                    t.add_pre(audit)
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["audit"].fn in view["deploy-web"].pre


def test_a_hook_reaches_a_subfolder_task(tmp_path):
    # A ROOT hook edits a task defined in a subfolder's file — the whole
    # merged tree, not just its own file.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate(inv):
            if "ship" in inv.tasks:
                inv.tasks["ship"].add_pre(inv.tasks["audit"])
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from footman import task

        @task
        def ship(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([root, sub]))
    assert view["audit"].fn in view["ship"].pre


def test_hooks_run_in_cascade_order_specific_last(tmp_path):
    # Root's hook runs first, the folder nearest cwd last — the same
    # "local overrides global" precedence the task cascade uses.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "root"]
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        import footman

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "svc"]
        """,
    )
    tree = discover.load_tree([root, sub])
    assert getattr(tree.tasks["x"], "_order") == ["root", "svc"]


def test_a_hook_that_raises_is_named(tmp_path):
    bad = _write(
        tmp_path / "tasks.py",
        """
        import footman

        @footman.pre_tasks
        def boom(inv):
            raise ValueError("nope")
        """,
    )
    with pytest.raises(discover.HookError, match="boom"):
        discover.load_tree([bad])


def test_tasks_view_iterates_nested_tasks(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task, group

        @task
        def a(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert sorted(t.name for t in view) == ["a", "build"]


def test_a_hook_disable_reaches_the_manifest(tmp_path):
    # Finalizers run at discovery, before the manifest is built — so a
    # `disable()` shows up in the baked node (`disabled`).
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["x"].disable("off by policy")
        """,
    )
    tree = discover.load_tree([src])
    node = manifest.build_manifest(tree)["tree"]["tasks"]["x"]
    assert node["disabled"] == "off by policy"


def test_task_view_add_post_and_read_post(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def deploy(): ...

        @task
        def notify(): ...

        @footman.pre_tasks
        def wire(inv):
            inv.tasks["deploy"].add_post(inv.tasks["notify"])
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["notify"].fn in view["deploy"].post
    assert "missing" not in view
    with pytest.raises(KeyError):
        _ = view["missing"]


def test_task_view_disabled_reads_the_reason(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def a(): ...

        @task
        def b(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["a"].disable("off by policy")
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["a"].disabled == "off by policy"
    assert view["b"].disabled is None


def test_task_view_reads_policy_flags(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task(keep_going=False, atomic=True, confirm="sure?")
        def gated(): ...

        @task
        def plain(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    gated, plain = view["gated"], view["plain"]
    assert gated.keep_going is False
    assert gated.atomic is True
    assert gated.confirm == "sure?"
    # A plain task reports the neutral defaults, not None-vs-missing noise.
    assert plain.keep_going is None
    assert plain.atomic is False
    assert plain.infinite is False
    assert plain.interactive is False
    assert plain.timed is True
    assert plain.confirm == ""


def test_task_view_infinite_is_untimed(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task(infinite=True)
        def serve(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["serve"].infinite is True
    assert view["serve"].timed is False  # infinite implies no timing history


def test_task_view_owning_group(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task, group

        @task
        def top(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["top"].group is None  # top-level task is in no named group
    assert view["build"].group is not None
    assert view["build"].group.name == "docs"


def test_task_view_provenance_single_file(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task
        def x(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    x = view["x"]
    assert x.defining_dir == str(src.parent)
    assert x.source_file is not None and x.source_file.endswith("tasks.py")
    assert x.shadowed is None
    assert x.shadow_chain == (x.fn,)


def test_task_view_shadow_chain_across_cascade(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task
        def x():
            "root version"
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from footman import task

        @task
        def x():
            "svc version"
        """,
    )
    view = registry.Tasks(discover.load_tree([root, sub]))
    x = view["x"]  # the winning (nearest-cwd) definition
    assert x.defining_dir == str(sub.parent)
    assert x.fn.__doc__ == "svc version"
    assert x.shadowed is not None and x.shadowed.__doc__ == "root version"
    chain = x.shadow_chain
    assert [t.__doc__ for t in chain] == ["svc version", "root version"]


def test_task_view_set_opts_is_permanent(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def policy(inv):
            inv.tasks["x"].set_opts(keep_going=False, atomic=True)
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["x"].keep_going is False
    assert view["x"].atomic is True
    # It writes the same attributes the policy accessors read.
    assert registry.keeps_going(view["x"].fn) is False


def test_task_view_set_opts_rejects_a_task_parameter(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def bad(inv):
            inv.tasks["x"].set_opts(fix=True)  # a task parameter, not a policy option
        """,
    )
    # `set_opts` reuses `.opts()`'s validation, so a stray parameter is a taught
    # error surfaced (named) through the hook.
    with pytest.raises(discover.HookError, match="bad"):
        discover.load_tree([src])


def test_a_hook_uses_defining_dir_for_a_cascade_decision(tmp_path):
    # The motivating case: gate every task defined under an `infra/` folder,
    # deciding purely from provenance the hook reads off the view.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate_infra(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if (t.defining_dir or "").endswith("infra"):
                    t.add_pre(audit)
        """,
    )
    infra = _write(
        tmp_path / "infra" / "tasks.py",
        """
        from footman import task

        @task
        def deploy(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([root, infra]))
    assert view["audit"].fn in view["deploy"].pre


# --- what the invocation adds over the tree view it replaced ------------------


def test_a_hook_sets_the_environment_every_task_sees(tmp_path, monkeypatch):
    # The moment is pre-DAG and single-threaded, so os.environ is ordinary code
    # here — and it lands before availability gates, which is the point: a
    # requires_env task is available because a hook supplied the variable.
    monkeypatch.delenv("LIFECYCLE_TOKEN", raising=False)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import os
            import footman
            from footman import task, requires_env

            @footman.pre_tasks
            def supply(inv):
                os.environ["LIFECYCLE_TOKEN"] = "from-the-hook"

            @task
            @requires_env("LIFECYCLE_TOKEN", reason="needs a token")
            def publish():
                print("token:", os.environ["LIFECYCLE_TOKEN"])
            """
        )
    )
    result = Runner().invoke("publish", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "token: from-the-hook" in result.stdout


def test_the_invocation_carries_what_the_line_asked(tmp_path):
    seen: dict[str, object] = {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import json, pathlib
            import footman
            from footman import task

            @footman.pre_tasks
            def record(inv):
                pathlib.Path("seen.json").write_text(json.dumps({
                    "keep_going": inv.cli.get("keep_going"),
                    "cwd_is_set": bool(inv.cwd),
                    "root_is_set": bool(inv.root),
                    "task_names": sorted(t.name for t in inv.tasks),
                }))

            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("-k build", cwd=tmp_path)
    assert result.ok, result.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["keep_going"] is True  # the line's own globals
    assert seen["cwd_is_set"] and seen["root_is_set"]
    assert seen["task_names"] == ["build"]


def test_a_hook_with_the_wrong_arity_is_refused_at_registration():
    # The lifecycle names its moments and they differ by arity, so a typo is a
    # taught error naming the hook rather than a TypeError at the first task.
    reg = Group("root")

    with pytest.raises(RegistrationError, match=r"pre_tasks is handed 1"):

        @reg.pre_tasks
        def no_args(): ...

    with pytest.raises(RegistrationError, match=r"takes 2 argument"):

        @reg.pre_tasks
        def too_many(inv, extra): ...


def test_finalize_is_gone():
    # Removed outright rather than kept as a refusing shim: the lifecycle has
    # one name per moment, and a retired alias is a second one.
    import footman

    reg = Group("root")
    assert not hasattr(reg, "finalize")
    assert not hasattr(footman, "finalize")
    assert "finalize" not in footman.__all__


def test_the_invocation_refuses_writes_once_frozen():
    from footman.invocation import Frozen, Invocation

    inv = Invocation(cwd="/tmp")
    inv.root = "/tmp"  # editable at pre_tasks
    inv.freeze()
    with pytest.raises(Frozen, match=r"editable only at pre_tasks"):
        inv.root = "/elsewhere"
