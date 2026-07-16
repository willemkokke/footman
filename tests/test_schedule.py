"""The DAG scheduler: parallelism, pre/post deps, dedup, fail/skip, parallel()."""

from __future__ import annotations

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
    assert results[0].ok is False  # a times out at the barrier (b runs after)


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
