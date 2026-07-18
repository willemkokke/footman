"""The DAG scheduler: parallelism, pre/post deps, dedup, fail/skip, parallel()."""

from __future__ import annotations

import io
import sys
import threading

import pytest

from footman import manifest, parallel, run, schedule
from footman.registry import Group
from footman.split import ChainError, split_chain


def drive(build, line, **kw):
    reg = Group("root")
    build(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return schedule.run_plan(reg, segments, **kw)


def test_chain_runs_concurrently_by_default():
    barrier = threading.Barrier(2, timeout=3)
    reached = []

    def tasks(reg):
        @reg.task
        def a():
            barrier.wait()
            reached.append("a")

        @reg.task
        def b():
            barrier.wait()
            reached.append("b")

    results = drive(tasks, "a b")  # both must reach the barrier -> concurrent
    assert set(reached) == {"a", "b"}
    assert all(r.ok for r in results)


def test_sequential_flag_does_not_run_concurrently():
    barrier = threading.Barrier(2, timeout=0.3)

    def tasks(reg):
        @reg.task
        def a():
            barrier.wait()

        @reg.task
        def b():
            barrier.wait()

    results = drive(tasks, "a b", sequential=True)
    # Load-independent: true sequential runs a alone (it times out at the
    # barrier), then skips b — one result. A regressed parallel path would
    # submit both up front and yield two, however loaded the runner is.
    assert len(results) == 1
    assert results[0].ok is False  # a timed out at the barrier by itself


def test_duplicate_explicit_segments_each_run():
    calls = []

    def tasks(reg):
        @reg.task
        def build(target: str):
            calls.append(target)

    results = drive(tasks, "build web build api", sequential=True)
    assert len(results) == 2
    assert calls == ["web", "api"]  # both invocations run, in order


def test_duplicate_explicit_segments_run_in_parallel_too():
    calls = []
    lock = threading.Lock()

    def tasks(reg):
        @reg.task
        def build(target: str):
            with lock:
                calls.append(target)

    results = drive(tasks, "build web build api")  # default parallel
    assert len(results) == 2
    assert set(calls) == {"web", "api"}


def test_pre_runs_before_dependent():
    order = []

    def tasks(reg):
        @reg.task
        def fmt():
            order.append("fmt")

        @reg.task
        def lint():
            order.append("lint")

        @reg.task(pre=[fmt, lint])
        def check():
            order.append("check")

    results = drive(tasks, "check")
    assert order[-1] == "check"
    assert set(order) == {"fmt", "lint", "check"}
    assert results[-1].task == "check"


def test_post_runs_after():
    order = []

    def tasks(reg):
        @reg.task
        def notify():
            order.append("notify")

        @reg.task(post=[notify])
        def deploy():
            order.append("deploy")

    drive(tasks, "deploy")
    assert order == ["deploy", "notify"]


def test_shared_dependency_runs_once():
    calls = []

    def tasks(reg):
        @reg.task
        def setup():
            calls.append(1)

        @reg.task(pre=[setup])
        def a(): ...

        @reg.task(pre=[setup])
        def b(): ...

    drive(tasks, "a b")
    assert calls == [1]  # deduped despite two dependents


def test_failed_pre_skips_dependent():
    def tasks(reg):
        @reg.task
        def bad():
            raise RuntimeError("boom")

        @reg.task(pre=[bad])
        def check():
            raise AssertionError("must not run")

    results = drive(tasks, "check")
    assert [r.task for r in results] == ["bad"]  # check skipped
    assert results[0].ok is False


def test_keep_going_runs_independent_branches():
    ran = []

    def tasks(reg):
        @reg.task
        def bad():
            ran.append("bad")
            raise RuntimeError("x")

        @reg.task
        def good():
            ran.append("good")

    drive(tasks, "bad good", keep_going=True)
    assert set(ran) == {"bad", "good"}


def test_parallel_helper_runs_concurrently():
    barrier = threading.Barrier(3, timeout=3)

    def hit():
        barrier.wait()

    def tasks(reg):
        @reg.task
        def build():
            parallel(hit, hit, hit)

    results = drive(tasks, "build")
    assert results[0].ok


def test_parallel_helper_propagates_failure():
    def tasks(reg):
        @reg.task
        def build():
            parallel(lambda: run("false"), lambda: run("true"))

    results = drive(tasks, "build")
    assert results[0].ok is False


def test_parallel_fails_on_nonzero_return():
    # F13: a thunk that *returns* a non-zero code fails the run, same as a raise.
    def tasks(reg):
        @reg.task
        def build():
            parallel(lambda: 1, lambda: 0)

    results = drive(tasks, "build")
    assert results[0].ok is False


def test_parallel_keep_going_collects_all_codes():
    # F42: first coverage of the keep_going branch — codes returned, no raise.
    codes = {}

    def tasks(reg):
        @reg.task
        def build():
            codes["got"] = parallel(lambda: 1, lambda: 0, keep_going=True)

    results = drive(tasks, "build")
    assert results[0].ok is True
    assert codes["got"] == [1, 0]  # pool.map preserves call order


def test_parallel_failure_exit_code_is_the_thunks_code():
    # D16: with 1.1 + 6.2 both in, a failing parallel() thunk exits with its own
    # code (not a flat 1).
    def tasks(reg):
        @reg.task
        def build():
            parallel(lambda: run([sys.executable, "-c", "import sys; sys.exit(7)"]))

    results = drive(tasks, "build")
    assert results[0].ok is False
    assert results[0].code == 7


def test_parallel_child_steps_surface_on_parent():
    # F12: run()s inside parallel() used to vanish from --json/recording; they
    # now land on the parent task's steps (completion order — assert as a set).
    def tasks(reg):
        @reg.task
        def build():
            parallel(lambda: run("echo one"), lambda: run("echo two"))

    results = drive(tasks, "build")
    commands = {s.command for s in results[0].steps}
    assert commands == {"echo one", "echo two"}


def test_parallel_output_is_grouped_not_interleaved(capsys):
    def tasks(reg):
        @reg.task
        def a():
            print("A1")
            print("A2")

        @reg.task
        def b():
            print("B1")
            print("B2")

    drive(tasks, "a b")
    out = capsys.readouterr().out
    assert "A1\nA2\n" in out  # each task's lines stay contiguous
    assert "B1\nB2\n" in out


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_parallel_progress_line_on_a_tty(monkeypatch):
    fake = _Tty()
    monkeypatch.setattr(sys, "stdout", fake)

    def tasks(reg):
        @reg.task
        def a():
            print("A-OUT")

        @reg.task
        def b():
            print("B-OUT")

    results = drive(tasks, "a b")
    out = fake.getvalue()
    assert all(r.ok for r in results)
    assert "\r\x1b[K" in out  # the status line rendered and cleared
    assert "running:" in out
    assert "A-OUT" in out and "B-OUT" in out  # task blocks land intact
    assert out.endswith("\r\x1b[K")  # the line never outlives the run


def test_progress_absent_without_a_tty(capsys):
    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b")
    assert "\r" not in capsys.readouterr().out  # buffers aren't TTYs: no spinner


def test_progress_absent_when_quiet(monkeypatch):
    fake = _Tty()
    monkeypatch.setattr(sys, "stdout", fake)

    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b", ctx_config={"quiet": True})
    assert "\r" not in fake.getvalue()


def test_dependency_cycle_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def a(): ...

        # pre=[a] makes b depend on a; post=[a] makes a depend on b: a cycle.
        @reg.task(pre=[a], post=[a])
        def b(): ...

    with pytest.raises(ChainError, match="dependency cycle"):
        drive(tasks, "b")
    with pytest.raises(ChainError, match="dependency cycle"):
        drive(tasks, "b", sequential=True)
