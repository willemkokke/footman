"""Signals: a supervisor's stop, and the stack dump that is not one."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import IO

import pytest

from footman import _app, _paths, _signals

# A task that asks its *own* process to stop, the way `kill` or `docker stop`
# would. The handler check is the seatbelt: an unhandled SIGTERM would kill
# the pytest worker outright, so a missing install has to read as a failed
# assertion rather than a dead process that reports nothing.
STOPPER = '''
import signal

from footman import task

@task
def stop(sig: str = "SIGTERM"):
    """Ask this process to stop."""
    number = getattr(signal, sig)
    if signal.getsignal(number) in (signal.SIG_DFL, signal.SIG_IGN):
        raise AssertionError(f"{sig} reached the run with no handler installed")
    signal.raise_signal(number)
'''


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tasks: str) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(tasks)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")


def _runner(
    tmp_path: Path, *args: str, stderr: int | IO[str], env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """`fm <args>` as a real process in its own session.

    Its own session because these tests are entirely about which signal
    reaches whom: the runner must be signalled by hand, never as a bystander
    of whatever the test process is in.
    """
    environ = {
        **os.environ,
        "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache"),
        **(env or {}),
    }
    environ.pop("VIRTUAL_ENV", None)
    return subprocess.Popen(
        [sys.executable, "-m", "footman", *args],
        cwd=tmp_path,
        env=environ,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )


def _await(condition, what: str, seconds: float = 30.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.05)
    pytest.fail(f"{what} never happened; the test proves nothing")


# --- a stop asked for by signal ----------------------------------------------


def test_sigterm_exits_143_and_sighup_129(tmp_path, monkeypatch, capsys):
    if sys.platform == "win32":
        # In the body rather than a decorator, so the checkers narrow the
        # POSIX-only names below the same way `_signals._bindings` does.
        pytest.skip("SIGTERM and SIGHUP are POSIX")
    _project(tmp_path, monkeypatch, STOPPER)

    assert _app.run(["stop"]) == 143
    assert "terminated" in capsys.readouterr().err
    assert _app.run(["--sequential", "stop"]) == 143
    assert "terminated" in capsys.readouterr().err
    assert _app.run(["stop", "--sig=SIGHUP"]) == 129
    assert "hung up" in capsys.readouterr().err


def test_the_json_envelope_survives_a_stop(tmp_path, monkeypatch, capsys):
    # The same promise 130 already keeps: task output is buffered, so nothing
    # has reached stdout yet and a machine consumer still gets one document.
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    _project(tmp_path, monkeypatch, STOPPER)

    assert _app.run(["--json", "stop"]) == 143
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == {"code": 143, "message": "terminated"}
    assert payload["items"] == []


def test_only_the_first_stop_raises():
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    with _signals.installed():
        assert callable(signal.getsignal(signal.SIGTERM))
        with pytest.raises(_signals.Stop) as caught:
            signal.raise_signal(signal.SIGTERM)
        assert (caught.value.word, caught.value.code) == ("terminated", 143)
        # A cancelled GitHub job sends SIGINT and then SIGTERM: a second raise
        # would land inside the reap and abandon it half done.
        signal.raise_signal(signal.SIGTERM)


def test_the_handlers_that_were_there_come_back():
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    before = signal.getsignal(signal.SIGTERM)
    with _signals.installed():
        assert signal.getsignal(signal.SIGTERM) is not before
    assert signal.getsignal(signal.SIGTERM) is before


def test_sigterm_reaps_the_child_a_task_was_waiting_on(tmp_path):
    """A supervisor's stop left `fm` dead and its whole tree running.

    SIGTERM at its default disposition kills footman where it stands, and
    the children are in their own process groups on purpose, so `kill`,
    `docker stop` or a cancelled CI job reached the runner and nothing else.
    Driven as a real process, signalled by pid alone — the way a supervisor
    does it, never the group, which is exactly what makes the child survive.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX process groups and SIGTERM")

    pid_file = tmp_path / "child.pid"
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(f"""
        import sys
        from footman import run, task

        @task
        def slow():
            run([sys.executable, "-c",
                 "import os, time;"
                 "open({str(pid_file)!r}, 'w').write(str(os.getpid()));"
                 "time.sleep(120)"])
        """)
    )
    runner = _runner(tmp_path, "slow", stderr=subprocess.PIPE)
    try:
        _await(pid_file.exists, "the child started")
        child = int(pid_file.read_text())

        os.kill(runner.pid, signal.SIGTERM)  # what a supervisor does
        assert runner.wait(timeout=30) == 143
        assert "terminated" in (runner.stderr.read() if runner.stderr else "")

        gone_by = time.time() + 15
        while time.time() < gone_by:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)  # do not leak it into the suite
            pytest.fail("the child outlived the stop")
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()


# --- the dump, which is diagnostic and not a stop -----------------------------


DUMPEE = """
import time
from pathlib import Path

from footman import task

@task
def wait():
    Path({ready!r}).write_text("here")
    deadline = time.time() + 30
    while time.time() < deadline and not Path({go!r}).exists():
        time.sleep(0.05)
    Path({done!r}).write_text("finished")
"""


def test_sigquit_dumps_every_thread_and_the_run_carries_on(tmp_path):
    # Hitting it twice and watching whether the frames moved is how a hang
    # tells itself apart from slow progress, so the dump must not shut
    # anything down.
    if sys.platform == "win32":
        pytest.skip("SIGQUIT is POSIX")

    ready, go, done = (tmp_path / n for n in ("ready", "go", "done"))
    (tmp_path / "tasks.py").write_text(
        DUMPEE.format(ready=str(ready), go=str(go), done=str(done))
    )
    err_file = tmp_path / "stderr.txt"
    with err_file.open("w") as sink:
        runner = _runner(tmp_path, "wait", stderr=sink)
        try:
            _await(ready.exists, "the task started")
            os.kill(runner.pid, signal.SIGQUIT)
            _await(
                lambda: "Current thread" in err_file.read_text(),
                "the stacks were dumped",
            )
            dump = err_file.read_text()
            assert "in wait" in dump  # the task's own frame, not just footman's
            go.write_text("go")
            assert runner.wait(timeout=30) == 0
        finally:
            if runner.poll() is None:  # pragma: no cover - only on a regression
                runner.kill()
    assert done.exists()  # it kept running after the dump, and finished


def test_the_timer_dumps_where_there_is_no_key_to_press(tmp_path):
    # Nobody is at a keyboard when CI hangs, and Windows has no SIGQUIT, so
    # the same dump is reachable without a signal at all.
    ready, go, done = (tmp_path / n for n in ("ready", "go", "done"))
    (tmp_path / "tasks.py").write_text(
        DUMPEE.format(ready=str(ready), go=str(go), done=str(done))
    )
    err_file = tmp_path / "stderr.txt"
    with err_file.open("w") as sink:
        runner = _runner(
            tmp_path, "wait", stderr=sink, env={"FOOTMAN_STACKS_AFTER": "0.25"}
        )
        try:
            _await(lambda: "Timeout" in err_file.read_text(), "the timer dumped stacks")
            assert "in wait" in err_file.read_text()
            go.write_text("go")
            assert runner.wait(timeout=30) == 0
        finally:
            if runner.poll() is None:  # pragma: no cover - only on a regression
                runner.kill()
    assert done.exists()


def test_a_typo_in_the_stacks_variable_says_so(monkeypatch, capsys):
    name = _paths.env_var(_signals.STACKS_AFTER)
    monkeypatch.setenv(name, "30")
    assert _signals._seconds_from_env() == 30.0
    monkeypatch.setenv(name, "soon")
    assert _signals._seconds_from_env() is None
    assert f"{name}=soon" in capsys.readouterr().err
    monkeypatch.setenv(name, "0")
    assert _signals._seconds_from_env() is None
    assert f"{name}=0" in capsys.readouterr().err
    monkeypatch.delenv(name)
    assert _signals._seconds_from_env() is None
    assert capsys.readouterr().err == ""
