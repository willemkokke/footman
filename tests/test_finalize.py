"""The `@finalize` discovery hook: edit the merged command tree at discovery."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from footman import discover, manifest, registry


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def test_finalize_edits_the_merged_tree(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @task
        def deploy_web(): ...

        @footman.finalize
        def gate(tasks):
            audit = tasks["audit"]
            for t in tasks:
                if t.name.startswith("deploy"):
                    t.add_pre(audit)
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["audit"].fn in view["deploy-web"].pre


def test_finalize_reaches_a_subfolder_task(tmp_path):
    # A ROOT finalizer edits a task defined in a subfolder's file — the whole
    # merged tree, not just its own file.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.finalize
        def gate(tasks):
            if "ship" in tasks:
                tasks["ship"].add_pre(tasks["audit"])
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


def test_finalize_runs_in_cascade_order_specific_last(tmp_path):
    # Root's finalizer runs first, the folder nearest cwd last — the same
    # "local overrides global" precedence the task cascade uses.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.finalize
        def f(tasks):
            fn = tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "root"]
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        import footman

        @footman.finalize
        def f(tasks):
            fn = tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "svc"]
        """,
    )
    tree = discover.load_tree([root, sub])
    assert getattr(tree.tasks["x"], "_order") == ["root", "svc"]


def test_finalize_that_raises_is_named(tmp_path):
    bad = _write(
        tmp_path / "tasks.py",
        """
        import footman

        @footman.finalize
        def boom(tasks):
            raise ValueError("nope")
        """,
    )
    with pytest.raises(discover.FinalizeError, match="boom"):
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


def test_finalize_disable_reaches_the_manifest(tmp_path):
    # Finalizers run at discovery, before the manifest is built — so a
    # `disable()` shows up in the baked node (`disabled`).
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.finalize
        def off(tasks):
            tasks["x"].disable("off by policy")
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

        @footman.finalize
        def wire(tasks):
            tasks["deploy"].add_post(tasks["notify"])
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

        @footman.finalize
        def off(tasks):
            tasks["a"].disable("off by policy")
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

        @footman.finalize
        def policy(tasks):
            tasks["x"].set_opts(keep_going=False, atomic=True)
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

        @footman.finalize
        def bad(tasks):
            tasks["x"].set_opts(fix=True)  # a task parameter, not a policy option
        """,
    )
    # `set_opts` reuses `.opts()`'s validation, so a stray parameter is a taught
    # error surfaced (named) through the finalizer.
    with pytest.raises(discover.FinalizeError, match="bad"):
        discover.load_tree([src])


def test_finalize_uses_defining_dir_for_a_cascade_decision(tmp_path):
    # The motivating case: gate every task defined under an `infra/` folder,
    # deciding purely from provenance the finalizer reads off the view.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.finalize
        def gate_infra(tasks):
            audit = tasks["audit"]
            for t in tasks:
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
