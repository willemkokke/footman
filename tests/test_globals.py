"""The environ router: virtual reads, scoped writes, snapshot spawns."""

from __future__ import annotations

import os
import sys

from footman import _globals, manifest
from footman.context import run
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
