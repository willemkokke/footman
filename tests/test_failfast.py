"""Failure policy: the tri-state keep-going resolution and `--fail-fast`."""

from __future__ import annotations

import sys

import pytest

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


def test_a_killed_task_reports_cancelled_not_failed():
    from footman.context import run
    from footman.schedule import run_plan

    def tasks(reg):
        @reg.task
        def slow():
            run([sys.executable, "-c", "import time; time.sleep(30)"])

        @reg.task
        def boom():
            raise SystemExit(7)  # a genuine failure, code 7

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    results = {r.task: r for r in run_plan(reg, segs, sequential=False)}
    assert results["boom"].cancelled is False and results["boom"].code == 7
    assert results["slow"].cancelled is True  # cut off by fail-fast, not a failure


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process groups; Windows uses taskkill /T"
)
def test_fail_fast_kills_grandchildren_not_just_the_direct_child(tmp_path):
    # A task's subprocess spawns its OWN child (a tool's worker: pytest-xdist,
    # `make -j`). fail-fast must reap the whole tree, not orphan the grandchild.
    # The direct child leads its own process group and the group-wide SIGTERM
    # reaches the grandchild too. (Windows gets the same via taskkill /T, which
    # the non-skipped sibling-kill test already drives on that platform.)
    import os
    import signal
    import time

    from footman.context import run
    from footman.schedule import run_plan

    pidfile = tmp_path / "grandchild.pid"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"  # long: outlives the run unless the group kill reaps it
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]]).wait()\n"
    )

    def tasks(reg):
        @reg.task
        def slow():
            run([sys.executable, str(child), str(grandchild), str(pidfile)])

        @reg.task
        def boom():
            for _ in range(200):  # let slow's grandchild come fully up first,
                if pidfile.exists():  # so the kill targets an established tree
                    break
                time.sleep(0.02)
            raise SystemExit(1)  # then fail fast → slow's whole tree is killed

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    run_plan(reg, segs, sequential=False)

    for _ in range(200):  # the grandchild records its pid as it starts
        if pidfile.exists():
            break
        time.sleep(0.02)
    assert pidfile.exists(), "grandchild never started — the test isn't exercising it"
    gc_pid = int(pidfile.read_text())

    def alive() -> bool:
        try:
            os.kill(gc_pid, 0)  # signal 0 just probes; ESRCH once it's gone
            return True
        except ProcessLookupError:
            return False

    for _ in range(200):  # let the group signal propagate to the grandchild
        if not alive():
            break
        time.sleep(0.02)
    leaked = alive()
    if leaked:  # don't strand a 30s sleeper if the assertion is about to fail
        os.kill(gc_pid, signal.SIGKILL)
    assert not leaked, "grandchild survived fail-fast — the group kill missed it"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process-group ownership guard"
)
def test_kill_signals_a_shared_group_child_alone_never_the_group():
    # A child that shares footman's process group (spawned WITHOUT its own
    # session) must be signalled individually — a killpg here would take out the
    # runner. That this test's own process survives to make its assertion is the
    # proof the group was spared; the child dies from a plain, individual SIGTERM.
    import signal
    import subprocess

    from footman import context as ctx

    proc = subprocess.Popen(  # no start_new_session → shares pytest's group
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    ctx.reset_abort()
    try:
        ctx._register_child(proc)
        ctx.terminate_live_children(grace=5)  # individual SIGTERM, not group-wide
        proc.wait(timeout=5)
        assert proc.returncode == -signal.SIGTERM  # the child alone, not the group
    finally:
        ctx._forget_child(proc)
        ctx.reset_abort()
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is unignorable on Windows")
def test_fail_fast_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    import signal
    import subprocess
    import time

    from footman import context as ctx

    ready = tmp_path / "ready"
    src = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r}, 'w').close(); "
        "time.sleep(30)"
    )
    # start_new_session so it leads its own group, exactly like a real killable
    # child — the group SIGTERM/SIGKILL escalation is what's under test, and it
    # keeps the kill off pytest's own process group.
    proc = subprocess.Popen(
        [sys.executable, "-c", src], text=True, start_new_session=True
    )  # Popen[str]
    ctx.reset_abort()
    try:
        for _ in range(200):  # wait until the child has installed its SIG_IGN
            if ready.exists():
                break
            time.sleep(0.02)
        ctx._register_child(proc)
        ctx.terminate_live_children(grace=0.2)  # SIGTERM ignored → SIGKILL follows
        proc.wait(timeout=5)
        assert proc.returncode == -signal.SIGKILL  # forced, not the ignored SIGTERM
    finally:
        ctx._forget_child(proc)
        ctx.reset_abort()
        if proc.poll() is None:
            proc.kill()
