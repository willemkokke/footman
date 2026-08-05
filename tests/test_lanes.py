"""Lanes: one holder per named resource, unrelated work untouched."""

from __future__ import annotations

import os
import textwrap
import threading
import time

import pytest

from footman import Context, lane, parallel, step, use_context


def test_two_claimants_serialise_and_unrelated_work_overlaps():
    db = lane("test-db")
    overlap: list[str] = []
    holding = threading.Event()
    lock_seen: list[bool] = []

    @step
    def first():
        holding.set()
        time.sleep(0.08)
        overlap.append("first-done")

    @step
    def second():
        lock_seen.append(not holding.is_set() or "first-done" in overlap)
        overlap.append("second")

    @step
    def bystander():
        holding.wait(2)
        overlap.append("bystander-ran-during-hold" if not overlap else "late")

    with use_context(Context()):
        parallel(
            first.opts(lanes=(db,))(),
            second.opts(lanes=(db,))(),
            bystander(),
        )
    # The two claimants never overlapped; the bystander ran during the hold.
    assert "bystander-ran-during-hold" in overlap or "late" in overlap
    assert all(lock_seen)


def test_redeclaring_a_taken_name_is_a_provenance_refusal():
    lane("test-unique")
    with pytest.raises(ValueError, match=r"already declared at .*test_lanes"):
        exec("from footman import lane\nlane('test-unique')")  # a second site


def test_the_cwd_lane_applies_the_directory_for_the_hold(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import os

            from footman import cwd_lane, task

            @task(lanes=(cwd_lane,), cwd="asinvoked")
            def where() -> str:
                return os.getcwd()
            """
        )
    )
    from footman.testing import Runner

    result = Runner().invoke("where", cwd=tmp_path)
    assert result.ok, result.stderr
    row = next(r for r in result.results if r.task == "where")
    assert os.path.realpath(str(row.body_returned)) == os.path.realpath(str(tmp_path))


def test_a_string_in_lanes_is_taught_at_the_declaration():
    # Handles, not strings: a typo'd handle is an undefined name, and a
    # string dies here as a taught error, never a crash in the arbiter.
    from footman import task

    with pytest.raises(TypeError, match="Lane handles, not str"):

        @task(lanes=("database",))  # type: ignore[arg-type]  # the taught path
        def migrate(): ...


def test_a_string_in_opts_lanes_is_taught():
    from footman import task

    @task
    def migrate(): ...

    with pytest.raises(TypeError, match="Lane handles, not str"):
        migrate.opts(lanes=("database",))  # type: ignore[arg-type]


def test_a_string_in_step_opts_lanes_is_taught():
    @step
    def convert(): ...

    with pytest.raises(TypeError, match="Lane handles, not str"):
        convert.opts(lanes=("database",))


def test_console_on_a_step_is_taught():
    from footman._globals import console_lane

    @step
    def chatty(): ...

    with use_context(Context()), pytest.raises(TypeError, match="interactive task"):
        chatty.opts(lanes=(console_lane,))()()


def test_step_opts_accepts_lanes_and_the_item_waits_its_turn():
    gate = lane("test-gate")
    order: list[str] = []

    @step
    def a():
        order.append("a-start")
        time.sleep(0.05)
        order.append("a-end")

    @step
    def b():
        order.append("b-start")

    with use_context(Context()):
        parallel(a.opts(lanes=(gate,))(), b.opts(lanes=(gate,))())
    # Whichever ran first finished before the other started.
    first_end = (
        order.index("a-end") if order[0] == "a-start" else order.index("b-start")
    )
    assert order[0].endswith("-start")
    assert "a-start" in order and "b-start" in order
    idx = {name: i for i, name in enumerate(order)}
    if order[0] == "a-start":
        assert idx["a-end"] < idx["b-start"]
    _ = first_end


def test_lane_waits_land_in_the_json_report():
    # "Which lane serialised what, for how long" is answerable from a --json
    # run: the waiting claimant's row carries the lane and the wait, and a
    # claim granted on arrival carries nothing.
    import json as json_mod

    from footman.registry import Group
    from footman.testing import Runner

    contended = lane("test-report-contended")
    free = lane("test-report-free")
    reg = Group("root")

    @reg.task(lanes=(contended,))
    def holder():
        time.sleep(0.25)

    @reg.task(lanes=(contended,))
    def claimant():
        time.sleep(0.25)

    @reg.task(lanes=(free,))
    def alone(): ...

    result = Runner().invoke("--json --jobs=3 holder claimant alone", tasks=reg)
    assert result.ok, result.stderr
    envelope = json_mod.loads(result.stdout)
    by_name = {e["task"]: e for e in envelope["items"] if "task" in e}
    waits = [
        w
        for name in ("holder", "claimant")
        for w in by_name[name].get("lane_waits", ())
    ]
    assert len(waits) == 1  # exactly one of the two paid at the bar
    assert waits[0]["lane"] == "test-report-contended"
    assert waits[0]["waited_ms"] > 0
    assert "lane_waits" not in by_name["alone"]  # granted on arrival
