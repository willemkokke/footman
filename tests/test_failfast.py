"""Failure policy: the tri-state keep-going resolution and `--fail-fast`."""

from __future__ import annotations

from footman import manifest
from footman.registry import Group
from footman.schedule import resolve_keep_going
from footman.split import _parse_globals, split_chain


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def _segs(tree, line):
    return split_chain(tree, line.split())[1]


def test_cli_choice_wins_over_a_declared_default():
    def tasks(reg):
        @reg.task(keep_going=True)
        def check(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "check")
    assert resolve_keep_going(reg, segs, None) is True  # unspecified -> declared
    assert resolve_keep_going(reg, segs, False) is False  # --fail-fast overrides
    assert resolve_keep_going(reg, segs, True) is True  # -k overrides


def test_no_declaration_falls_back_to_built_in_fail_fast():
    def tasks(reg):
        @reg.task
        def plain(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "plain")
    assert resolve_keep_going(reg, segs, None) is False
    assert resolve_keep_going(reg, segs, True) is True


def test_fail_fast_is_a_recognised_global():
    # It parses as a leading global (before the first task), like --keep-going.
    globals_, i = _parse_globals(["--fail-fast", "check"], 0)
    assert globals_ == ["--fail-fast"] and i == 1


def test_fail_fast_kills_an_in_flight_sibling_subprocess(tmp_path):
    import sys
    import time

    from footman.context import run
    from footman.schedule import run_plan

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(30)"]  # portable, killable

    def tasks(reg):
        @reg.task
        def slow():
            run(sleep)
            marker.write_text("done")  # reached only if the sleep was NOT killed

        @reg.task
        def boom():
            raise SystemExit(1)  # fails fast

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")  # parallel; boom fails → slow's sleep is killed
    started = time.perf_counter()
    run_plan(reg, segs, sequential=False)
    assert time.perf_counter() - started < 10  # did not wait out the 30s sleep
    assert not marker.exists()  # slow was cut off before it could finish


def test_keep_going_lets_an_in_flight_sibling_finish(tmp_path):
    import sys

    from footman.context import run
    from footman.schedule import run_plan

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(0.4)"]

    def tasks(reg):
        @reg.task
        def slow():
            run(sleep)
            marker.write_text("done")

        @reg.task
        def boom():
            raise SystemExit(1)

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    run_plan(reg, segs, sequential=False, keep_going=True)
    assert marker.exists()  # keep-going: the sibling ran to completion, not killed


def test_atomic_task_is_not_killed_by_fail_fast(tmp_path):
    import sys

    from footman.context import run
    from footman.schedule import run_plan

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(0.4)"]

    def tasks(reg):
        @reg.task(atomic=True)
        def protected():
            run(sleep)
            marker.write_text("done")  # its subprocess must not be cut off

        @reg.task
        def boom():
            raise SystemExit(1)

    reg, tree = _tree(tasks)
    segs = _segs(tree, "protected boom")
    run_plan(reg, segs, sequential=False)  # fail-fast, but `protected` is atomic
    assert marker.exists()  # ran to completion despite the failing sibling
