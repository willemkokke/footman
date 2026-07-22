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
