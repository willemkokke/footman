"""The process-globals routers: environ, Popen injection, and the os guards."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from footman import _globals, manifest
from footman.context import chdir, run
from footman.executor import run_chain
from footman.registry import Group
from footman.split import split_chain


def drive(build, line, **cfg):
    reg = Group("root")
    build(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments, ctx_config=cfg)


def test_reads_see_snapshot_plus_overlay(monkeypatch):
    monkeypatch.setenv("BASE", "base")
    monkeypatch.delenv("EXTRA", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["EXTRA"] = "mine"  # scoped to this task
            seen["extra"] = os.environ["EXTRA"]
            seen["base"] = os.environ.get("BASE")
            seen["contains"] = "EXTRA" in os.environ
            seen["getenv"] = os.getenv("EXTRA")
            seen["iter"] = "EXTRA" in set(os.environ)
            seen["copy"] = dict(os.environ).get("EXTRA")

    results = drive(tasks, "go")
    assert results[0].ok
    assert seen == {
        "extra": "mine",
        "base": "base",
        "contains": True,
        "getenv": "mine",
        "iter": True,
        "copy": "mine",
    }
    assert "EXTRA" not in os.environ  # the real environment never mutated


def test_scoped_write_is_invisible_to_the_next_task(monkeypatch):
    monkeypatch.delenv("SCOPED", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def first():
            os.environ["SCOPED"] = "x"

        @reg.task
        def second():
            seen["visible"] = "SCOPED" in os.environ

    results = drive(tasks, "first second")
    assert all(r.ok for r in results)
    assert seen["visible"] is False


def test_scoped_write_reaches_the_child_spawn(monkeypatch):
    # The spawn env is snapshot + overlay, so an os.environ write in the
    # body rides into the child exactly like ctx.env would.
    monkeypatch.delenv("SCOPED", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            run([sys.executable, "-c", "import os; print(os.environ['SCOPED'])"])

    results = drive(tasks, "go")
    assert results[0].ok
    assert results[0].steps[0].output.strip() == "rides"


def test_delete_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            del os.environ["PATH"]

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "additive" in str(results[0].error)
    assert "PATH" in os.environ  # and the real thing survived


def test_set_then_delete_round_trips_scoped(monkeypatch):
    # pytest's own dance: main() sets PYTEST_VERSION and deletes it on the
    # way out. A key the task itself set scoped comes back out of the
    # overlay — additive both ways, invisible to siblings throughout.
    monkeypatch.delenv("ROUND_TRIP", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["ROUND_TRIP"] = "up"
            del os.environ["ROUND_TRIP"]
            seen["after"] = "ROUND_TRIP" in os.environ

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error
    assert seen["after"] is False
    assert "ROUND_TRIP" not in os.environ


def test_delete_of_an_overridden_key_restores_the_base(monkeypatch):
    # Overriding an existing var then deleting the override is still the
    # additive round trip: the task falls back to the run-start value; the
    # base environment itself is never subtracted from.
    monkeypatch.setenv("BASE_VAR", "original")
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["BASE_VAR"] = "override"
            del os.environ["BASE_VAR"]
            seen["after"] = os.environ.get("BASE_VAR")

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error
    assert seen["after"] == "original"
    assert os.environ["BASE_VAR"] == "original"


def test_delete_of_an_absent_key_is_a_key_error():
    def tasks(reg):
        @reg.task
        def go():
            del os.environ["NEVER_WAS_SET"]

    results = drive(tasks, "go")
    assert not results[0].ok
    assert isinstance(results[0].error, KeyError)


def test_write_note_is_taught_once_per_task(capfd, monkeypatch):
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.delenv("B_KEY", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["A_KEY"] = "1"
            os.environ["B_KEY"] = "2"  # second write: no second note

    assert drive(tasks, "go")[0].ok
    err = capfd.readouterr().err
    assert err.count("scoped it to this task") == 1


def test_setdefault_scopes_like_a_write(monkeypatch):
    monkeypatch.setenv("PRESENT", "kept")
    monkeypatch.delenv("ABSENT", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            seen["hit"] = os.environ.setdefault("PRESENT", "ignored")
            seen["miss"] = os.environ.setdefault("ABSENT", "scoped")
            seen["readback"] = os.environ["ABSENT"]

    assert drive(tasks, "go")[0].ok
    assert seen == {"hit": "kept", "miss": "scoped", "readback": "scoped"}
    assert "ABSENT" not in os.environ


def test_outside_a_run_environ_is_untouched(monkeypatch):
    monkeypatch.delenv("LOOSE", raising=False)
    os.environ["LOOSE"] = "real"
    assert os.environ["LOOSE"] == "real"
    del os.environ["LOOSE"]
    assert "LOOSE" not in os.environ


def test_install_is_refcounted():
    assert not _globals.active()
    _globals.install()
    _globals.install()
    try:
        _globals.uninstall()
        assert _globals.active()  # the outer install still holds
    finally:
        _globals.uninstall()
    assert not _globals.active()


def test_in_process_callable_reads_the_call_overlay(monkeypatch):
    # run(callable, env=…) reads land on snapshot + ctx.env + the call's
    # env — served by the router, no process-global patch, no lock.
    monkeypatch.setenv("BASE", "base")
    monkeypatch.delenv("EXTRA", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["pair"] = (os.environ.get("BASE"), os.environ.get("EXTRA"))
                return 0

            run(tool, env={"EXTRA": "call"})
            seen["after"] = os.environ.get("EXTRA")

    assert drive(tasks, "go")[0].ok
    assert seen["pair"] == ("base", "call")
    assert seen["after"] is None  # the call overlay ended with the call


# --- the Popen injection ------------------------------------------------------

_PRINT_CWD_AND_SCOPED = "import os; print(os.getcwd()); print(os.environ.get('SCOPED'))"


def test_popen_injects_cwd_and_env(tmp_path, monkeypatch, capfd):
    monkeypatch.delenv("SCOPED", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            proc = subprocess.Popen(  # raw spawn: no cwd=, no env=
                [sys.executable, "-c", _PRINT_CWD_AND_SCOPED],
                stdout=subprocess.PIPE,
                text=True,
            )
            out, _ = proc.communicate()
            seen["lines"] = out.splitlines()
            subprocess.Popen([sys.executable, "-c", "pass"]).wait()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["lines"] == [str(tmp_path), "rides"]
    err = capfd.readouterr().err
    assert err.count("raw subprocess") == 1  # taught once, not per spawn


def test_popen_explicit_args_win(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOPED", raising=False)
    other = tmp_path / "other"
    other.mkdir()
    seen = {}
    # A deliberately clean env must stay clean (env={} is not env=None) —
    # keep the one variable Windows needs to boot a child at all.
    base = (
        {"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}
    )

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            proc = subprocess.Popen(
                [sys.executable, "-c", _PRINT_CWD_AND_SCOPED],
                stdout=subprocess.PIPE,
                text=True,
                cwd=str(other),
                env={**base, "MARKER": "explicit"},
            )
            out, _ = proc.communicate()
            seen["lines"] = out.splitlines()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["lines"] == [str(other), "None"]  # explicit cwd/env untouched


# --- the os guards ------------------------------------------------------------


def test_chdir_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            os.chdir("/")

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "footman.cwd()" in str(results[0].error)


def test_chdir_to_the_current_directory_is_a_noop_not_an_error():
    # The defensive restore pattern: pytest's wrap_session re-chdirs to its
    # startpath on the way out even when nothing moved. Where the process
    # already is, nothing changes for any sibling — let it through.
    def tasks(reg):
        @reg.task
        def go():
            os.chdir(_globals.real_getcwd())

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error


def test_chdir_allowed_under_unmanaged(tmp_path):
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            before = _globals.real_getcwd()
            os.chdir(tmp_path)
            seen["moved"] = _globals.real_getcwd()
            os.chdir(before)

    results = drive(tasks, "go", cwd=tmp_path, cwd_unmanaged=True)
    assert results[0].ok, results[0].error
    assert seen["moved"] == str(tmp_path)


def test_getcwd_warns_once_toward_footman_cwd(capfd):
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            seen["cwd"] = os.getcwd()
            os.getcwd()  # second read: no second note

    assert drive(tasks, "go")[0].ok
    assert seen["cwd"] == _globals.real_getcwd()  # the value is still real
    err = capfd.readouterr().err
    assert err.count("footman.cwd()") == 1


def test_putenv_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            os.putenv("X", "y")

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "bypasses env scoping" in str(results[0].error)


@pytest.mark.skipif(sys.platform == "win32", reason="fork is POSIX-only")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # the taught unsafety
def test_fork_notes_the_serial_lane(capfd):
    def tasks(reg):
        @reg.task
        def go():
            pid = os.fork()
            if pid == 0:  # the child: touch nothing, leave immediately
                os._exit(0)
            os.waitpid(pid, 0)

    assert drive(tasks, "go")[0].ok
    assert "forking a threaded process is unsafe" in capfd.readouterr().err


def _mp_noop():
    pass


def test_multiprocessing_start_notes_the_serial_lane(capfd):
    import multiprocessing

    def tasks(reg):
        @reg.task
        def go():
            proc = multiprocessing.get_context("spawn").Process(target=_mp_noop)
            proc.start()
            proc.join()

    assert drive(tasks, "go")[0].ok
    assert "worker processes in-process" in capfd.readouterr().err


# --- the arbiter lanes --------------------------------------------------------


def _hold(policy, name, entered, release, inherited=False):
    def body():
        with _globals.lane(policy, name=name, inherited=inherited):
            entered.set()
            release.wait(timeout=10)

    t = threading.Thread(target=body, daemon=True)
    t.start()
    return t


def test_serial_lane_is_mutually_exclusive():
    _globals.install()
    try:
        e1, r1 = threading.Event(), threading.Event()
        e2, r2 = threading.Event(), threading.Event()
        t1 = _hold("serial", "a", e1, r1)
        assert e1.wait(5)
        t2 = _hold("serial", "b", e2, r2)
        time.sleep(0.15)
        assert not e2.is_set()  # one lane, one owner
        r1.set()
        assert e2.wait(5)  # b takes the lane once a leaves
        r2.set()
        t1.join(5)
        t2.join(5)
    finally:
        _globals.uninstall()


def test_exclusive_drains_and_bars_new_starts():
    _globals.install()
    try:
        en, rn = threading.Event(), threading.Event()
        ee, re_ = threading.Event(), threading.Event()
        e2, r2 = threading.Event(), threading.Event()
        tn = _hold(None, "normal", en, rn)
        assert en.wait(5)
        te = _hold("exclusive", "big", ee, re_)
        time.sleep(0.15)
        assert not ee.is_set()  # a running body blocks the drain
        t2 = _hold(None, "late", e2, r2)
        time.sleep(0.15)
        assert not e2.is_set()  # new starts bar while exclusive waits
        rn.set()
        assert ee.wait(5)  # drained: exclusive enters
        assert not e2.is_set()  # and still owns the world
        re_.set()
        assert e2.wait(5)  # the barred start proceeds after
        r2.set()
        for t in (tn, te, t2):
            t.join(5)
    finally:
        _globals.uninstall()


def test_parked_bodies_are_exempt_from_the_drain():
    _globals.install()
    try:
        ep, rp = threading.Event(), threading.Event()
        ee, re_ = threading.Event(), threading.Event()

        def parked_body():
            with _globals.lane(None, name="parent"), _globals.parked():
                ep.set()
                rp.wait(timeout=10)

        tp = threading.Thread(target=parked_body, daemon=True)
        tp.start()
        assert ep.wait(5)
        te = _hold("exclusive", "big", ee, re_)
        assert ee.wait(5)  # the parked parent does not block the drain
        re_.set()
        rp.set()
        tp.join(5)
        te.join(5)
    finally:
        _globals.uninstall()


def test_inherited_lineage_bypasses_the_bars():
    _globals.install()
    try:
        ee, re_ = threading.Event(), threading.Event()
        ei, ri = threading.Event(), threading.Event()
        te = _hold("exclusive", "holder", ee, re_)
        assert ee.wait(5)
        ti = _hold(None, "child", ei, ri, inherited=True)
        assert ei.wait(5)  # a lineage child extends the hold, never contends
        ri.set()
        re_.set()
        ti.join(5)
        te.join(5)
    finally:
        _globals.uninstall()


# --- serial/exclusive tasks own the real globals ------------------------------


def test_serial_task_owns_the_real_globals(tmp_path, monkeypatch):
    monkeypatch.delenv("SERIAL_ENV", raising=False)
    (tmp_path / "sub").mkdir()
    before = _globals.real_getcwd()
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def own():
            seen["cwd"] = _globals.real_getcwd()  # really chdir-ed
            os.environ["SERIAL_ENV"] = "real"  # passthrough, no scoping
            seen["visible"] = os.environ["SERIAL_ENV"]
            os.chdir(tmp_path / "sub")  # the guards stand down
            seen["moved"] = _globals.real_getcwd()

    results = drive(tasks, "own", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["cwd"] == str(tmp_path)
    assert seen["visible"] == "real"
    assert seen["moved"] == str(tmp_path / "sub")
    assert _globals.real_getcwd() == before  # both restored after the body
    assert "SERIAL_ENV" not in os.environ


def test_exclusive_task_owns_the_real_globals(tmp_path):
    seen = {}

    def tasks(reg):
        @reg.task(exclusive=True)
        def big():
            seen["cwd"] = _globals.real_getcwd()

    results = drive(tasks, "big", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["cwd"] == str(tmp_path)


# --- footman.chdir() ----------------------------------------------------------


def test_chdir_cm_errors_in_a_parallel_task(tmp_path):
    def tasks(reg):
        @reg.task
        def go():
            with chdir(tmp_path):
                pass

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "footman.chdir()" in str(results[0].error)


def test_chdir_cm_in_a_serial_task(tmp_path):
    (tmp_path / "sub").mkdir()
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def go():
            with chdir(rel="sub"):
                seen["inside"] = _globals.real_getcwd()
            seen["after"] = _globals.real_getcwd()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["inside"] == str(tmp_path / "sub")
    assert seen["after"] == str(tmp_path)  # restored to the task's own cwd


def test_chdir_cm_outside_a_run(tmp_path):
    before = _globals.real_getcwd()
    with chdir(tmp_path):
        assert _globals.real_getcwd() == str(tmp_path)
    assert _globals.real_getcwd() == before


def test_chdir_cm_relative_target_is_a_taught_error(tmp_path):
    with pytest.raises(TypeError, match="rel="), chdir("somewhere/relative"):
        pass  # pragma: no cover


# --- the stdin router ---------------------------------------------------------


def test_stdin_read_is_a_taught_error_in_a_parallel_task():
    def tasks(reg):
        @reg.task
        def go():
            input()

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "ask()" in str(results[0].error)


def test_stdin_passes_through_for_an_interactive_task(monkeypatch):
    import io as _io

    monkeypatch.setattr(sys, "stdin", _io.StringIO("typed answer\n"))
    seen = {}

    def tasks(reg):
        @reg.task(interactive=True)
        def wizard():
            seen["line"] = sys.stdin.readline().strip()

    results = drive(tasks, "wizard")
    assert results[0].ok, results[0].error
    assert seen["line"] == "typed answer"


def test_stdin_passes_through_for_a_serial_task(monkeypatch):
    import io as _io

    monkeypatch.setattr(sys, "stdin", _io.StringIO("serial line\n"))
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def own():
            seen["line"] = sys.stdin.readline().strip()

    results = drive(tasks, "own")
    assert results[0].ok, results[0].error
    assert seen["line"] == "serial line"


def test_stdin_untouched_outside_a_run():
    before = sys.stdin
    _globals.install()
    try:
        assert sys.stdin is not before  # wrapped for the run
    finally:
        _globals.uninstall()
    assert sys.stdin is before  # and restored


# --- the console lane (interactive overlaps the pool) -------------------------


def test_interactive_overlaps_the_parallel_pool():
    # A cross-handshake that only completes when the two nodes truly run
    # concurrently: the old model (interactive forces the whole run
    # sequential) would deadlock both waits and fail loudly.
    from footman import schedule

    e_wizard, e_sibling = threading.Event(), threading.Event()
    reg = Group("root")

    @reg.task(interactive=True)
    def wizard():
        e_wizard.set()
        assert e_sibling.wait(5), "sibling never ran while the wizard held"

    @reg.task
    def sibling():
        assert e_wizard.wait(5), "wizard never started alongside"
        e_sibling.set()

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["wizard", "sibling"])
    results = schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]


def test_console_lane_has_one_owner_at_a_time():
    from footman import schedule

    holds: list[tuple[str, float]] = []
    guard = threading.Lock()

    def _mark(tag):
        with guard:
            holds.append((tag, time.monotonic()))

    reg = Group("root")

    @reg.task(interactive=True)
    def first():
        _mark("first-in")
        time.sleep(0.2)
        _mark("first-out")

    @reg.task(interactive=True)
    def second():
        _mark("second-in")
        time.sleep(0.2)
        _mark("second-out")

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["first", "second"])
    results = schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]
    stamps = dict(holds)
    windows = sorted(
        [
            (stamps["first-in"], stamps["first-out"]),
            (stamps["second-in"], stamps["second-out"]),
        ]
    )
    assert windows[0][1] <= windows[1][0]  # the terminal has one owner


def test_console_gate_queues_until_the_console_frees():
    _globals.install()
    try:
        entered, release = threading.Event(), threading.Event()
        passed = threading.Event()
        holder = _hold(None, "wizard", entered, release)
        assert entered.wait(5)

        def flusher():
            with _globals.console_gate():
                passed.set()

        # No console owner yet from _hold (console=False): the gate is open.
        t = threading.Thread(target=flusher, daemon=True)
        t.start()
        assert passed.wait(5)
        release.set()
        holder.join(5)

        # Now with a real console owner: the gate queues.
        entered2, release2 = threading.Event(), threading.Event()

        def console_holder():
            with _globals.lane(None, name="wizard", console=True):
                entered2.set()
                release2.wait(timeout=10)

        h = threading.Thread(target=console_holder, daemon=True)
        h.start()
        assert entered2.wait(5)
        passed2 = threading.Event()

        def flusher2():
            with _globals.console_gate():
                passed2.set()

        t2 = threading.Thread(target=flusher2, daemon=True)
        t2.start()
        time.sleep(0.15)
        assert not passed2.is_set()  # queued behind the wizard
        release2.set()
        assert passed2.wait(5)  # and lands when the terminal frees
        h.join(5)
        t2.join(5)
    finally:
        _globals.uninstall()


# --- the abort latch is run-scoped --------------------------------------------


def test_abort_latch_clears_after_a_failed_run():
    # A run that ends in a failed task latches fail-fast; the latch must die
    # with the run — a *later* bare run() (no scheduler, so no start-of-run
    # reset) must not have its freshly registered child reaped at birth.
    from footman.context import Context, use_context

    def tasks(reg):
        @reg.task
        def boom():
            raise RuntimeError("x")

    results = drive(tasks, "boom")
    assert not results[0].ok
    with use_context(Context()):
        r = run([sys.executable, "-c", "print('alive')"], step=False)
    assert r == 0
    assert r.stdout.strip() == "alive"


# --- the status line suspends for a console owner -----------------------------


def test_console_lane_suspends_the_status_line():
    from footman import context

    calls = []

    class _FakeStatus:
        def suspend(self):
            calls.append("suspend")

        def resume(self):
            calls.append("resume")

    _globals.install()
    context.set_status(_FakeStatus())
    try:
        with _globals.lane(None, name="wizard", console=True):
            assert calls == ["suspend"]  # paused for exactly the ownership
        assert calls == ["suspend", "resume"]
    finally:
        context.set_status(None)
        _globals.uninstall()


def test_status_line_suspend_stops_painting():
    import io as _io

    from footman._progress import StatusLine

    err = _io.StringIO()
    line = StatusLine(err, None, color=False)
    line.unit_added(1)
    line.unit_started("wizard")  # paints
    assert err.getvalue()
    line.suspend()
    before = err.getvalue()
    line.paint()  # a tick while a wizard owns the terminal: no repaint
    assert err.getvalue() == before
    line.resume()  # repaints immediately, and truthfully
    assert len(err.getvalue()) > len(before)


# --- the argv router ----------------------------------------------------------


def test_argv_router_gives_each_thread_its_own_view():
    import sys as _s

    _globals.install()
    try:
        alias = _s.argv  # a `from sys import argv`-style alias: same object
        seen = {}
        barrier = threading.Barrier(2, timeout=5)

        def worker(name, args):
            with _globals.argv_override(args):
                barrier.wait()  # both overrides live at once
                seen[name] = (list(_s.argv), alias[0], len(_s.argv))

        a = threading.Thread(target=worker, args=("a", ["tool-a", "--x"]))
        b = threading.Thread(target=worker, args=("b", ["tool-b"]))
        a.start()
        b.start()
        a.join(5)
        b.join(5)
        assert seen["a"] == (["tool-a", "--x"], "tool-a", 2)
        assert seen["b"] == (["tool-b"], "tool-b", 1)
    finally:
        _globals.uninstall()
    assert not isinstance(_s.argv, _globals._ArgvProxy)  # restored


def test_argv_override_mutations_stay_in_the_view(monkeypatch):
    import sys as _s

    _globals.install()
    try:
        real_before = list(_globals._argv_saved)
        with _globals.argv_override(["legacy", "one", "two"]):
            _s.argv.pop(0)  # the classic legacy-main idiom
            _s.argv.append("three")
            assert list(_s.argv) == ["one", "two", "three"]
        assert list(_globals._argv_saved) == real_before  # the real argv untouched
    finally:
        _globals.uninstall()


def test_zero_arg_entry_parallelises_via_the_router(monkeypatch):
    # Two legacy zero-arg mains overlapping, each reading its own argv —
    # the cross-handshake deadlocks-and-fails if they serialise on a lock.
    import sys as _s

    from footman import tools as _tools

    e1, e2 = threading.Event(), threading.Event()
    seen = {}

    def make_entry(name, wait_for, then_set):
        def entry():  # zero-arg: reads sys.argv like an old argparse main
            then_set.set()
            assert wait_for.wait(5), "the sibling never ran alongside"
            seen[name] = list(_s.argv)
            return 0

        return entry

    entries = {
        "tool-a": make_entry("a", e2, e1),
        "tool-b": make_entry("b", e1, e2),
    }

    class _EP:
        def __init__(self, target):
            self._t = target

        def load(self):
            return self._t

    monkeypatch.setattr(
        _tools, "_console_entrypoint", lambda name: _EP(entries.get(name))
    )

    def tasks(reg):
        @reg.task
        def one():
            _tools.Tool("tool-a", in_process=True)("--x")

        @reg.task
        def two():
            _tools.Tool("tool-b", in_process=True)()

    from footman import schedule

    reg = Group("root")
    tasks(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["one", "two"])
    results = {r.task: r for r in schedule.run_plan(reg, segments)}
    assert all(r.ok for r in results.values()), [str(r.error) for r in results.values()]
    assert seen["a"] == ["tool-a", "--x"]
    assert seen["b"] == ["tool-b"]


def _mp_read_force_color(path):
    import os as _os

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(_os.environ.get("FORCE_COLOR")))


def test_multiprocessing_workers_inherit_the_run_wide_colour(tmp_path, monkeypatch):
    # A spawn worker bypasses the env router and reads the *real*
    # environment — which carries the run-wide colour decision, because
    # color_environment publishes into it before any task runs. The note's
    # warning is only about the task overlay.
    import multiprocessing

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    out = tmp_path / "fc.txt"

    def tasks(reg):
        @reg.task
        def spawn_worker():
            proc = multiprocessing.get_context("spawn").Process(
                target=_mp_read_force_color, args=(str(out),)
            )
            proc.start()
            proc.join()

    results = drive(tasks, "spawn-worker", force_color=True)
    assert results[0].ok, results[0].error
    assert out.read_text(encoding="utf-8") == "1"  # colour rode the real env


def test_argv_in_place_mutation_stays_in_the_view():
    """`sys.argv += [...]` is a plausible thing for a legacy `main()` to do —
    append a default, then read it back. Without `__iadd__` it falls through
    to `list.__iadd__`, which mutates the proxy's *base* storage: the append
    vanishes from the caller's own view (reads consult the override) and
    leaks into every call that has none."""
    import sys as _s

    _globals.install()
    try:
        base_before = list(_s.argv)
        with _globals.argv_override(["tool", "--x"]):
            _s.argv += ["--added"]
            assert list(_s.argv) == ["tool", "--x", "--added"]  # the caller sees it
        assert list(_s.argv) == base_before  # and nothing leaked into the base
    finally:
        _globals.uninstall()


def test_argv_reordering_stays_in_the_view():
    """`sort`/`reverse` are the same shape as `__iadd__`: unoverridden, they
    reorder the base list while the caller's view is untouched."""
    import sys as _s

    _globals.install()
    try:
        base_before = list(_s.argv)
        with _globals.argv_override(["tool", "b", "a"]):
            _s.argv.reverse()
            assert list(_s.argv) == ["a", "b", "tool"]
            _s.argv.sort()
            assert list(_s.argv) == ["a", "b", "tool"]
            assert list(reversed(_s.argv)) == ["tool", "b", "a"]
        assert list(_s.argv) == base_before
    finally:
        _globals.uninstall()
