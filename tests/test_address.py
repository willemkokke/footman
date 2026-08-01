"""Addresses: every record's tree-derived, deterministic name."""

from __future__ import annotations

import json
import textwrap

from footman import Context, parallel, run, step, use_context
from footman.testing import Runner


def test_same_labelled_steps_take_ordinals_in_written_order():
    ctx = Context()
    ctx.address = "build"
    with use_context(ctx):
        run("git --version", nofail=True)
        run("git --version", nofail=True)
    assert [s.address for s in ctx.steps] == ["build/git", "build/git#2"]


def test_step_items_and_blocks_join_the_same_tree():
    @step
    def clean(): ...

    ctx = Context()
    ctx.address = "release"
    with use_context(ctx):
        clean()()
        with step("prepare"):
            pass
    assert [s.address for s in ctx.steps] == ["release/clean", "release/prepare"]


def test_rows_and_body_calls_carry_the_request_path(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from footman import task

            @task
            def child() -> int:
                return 1

            @task
            def parent():
                child()
            """
        )
    )
    result = Runner().invoke("--json parent", cwd=tmp_path)
    assert result.ok, result.stderr
    rows = [i for i in json.loads(result.stdout)["items"] if "task" in i]
    addresses = {r["task"]: r["address"] for r in rows}
    assert addresses["parent"] == "parent"
    assert addresses["child"] == "parent/child"


def test_split_nodes_are_distinct_by_ordinal(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from footman import Forward, task

            @task
            def shared(fix: bool = False): ...

            @task(pre=[shared])
            def a(fix: Forward[bool] = True): ...

            @task(pre=[shared])
            def b(fix: Forward[bool] = False): ...
            """
        )
    )
    result = Runner().invoke("--json a b", cwd=tmp_path)
    assert result.ok, result.stderr
    rows = [i for i in json.loads(result.stdout)["items"] if "task" in i]
    shared_addresses = sorted(r["address"] for r in rows if r["task"] == "shared")
    assert shared_addresses == ["shared", "shared#2"]


def test_parallel_children_branch_the_parents_path():
    seen: dict[str, str] = {}

    def probe():
        from footman.context import current

        seen[current().task] = current().address

    ctx = Context()
    ctx.address = "go"
    with use_context(ctx):
        parallel(step(probe, title="x")(), step(probe, title="y")())
    assert seen == {"x": "go/x", "y": "go/y"}


def test_command_leaves_carry_the_verb_and_never_the_flags():
    ctx = Context()
    ctx.address = "release"
    with use_context(ctx):
        run("git fetch", nofail=True)
        run("git push", nofail=True)
        run("git --version", nofail=True)
    assert [s.address for s in ctx.steps] == [
        "release/git-fetch",
        "release/git-push",
        "release/git",  # a flag is not a verb
    ]


def test_leaves_are_parse_safe():
    from footman.context import _leaf

    assert _leaf("./ship") == "ship"  # the separator cannot fake a level
    assert _leaf("prepared 3 fixtures") == "prepared-3-fixtures"
    assert _leaf("make target#2") == "make-target-2"  # ordinals stay ours
    assert _leaf("///") == "step"  # never empty
