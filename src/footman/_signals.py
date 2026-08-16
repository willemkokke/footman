"""Stop signals — what a real `fm` run wires up.

Installed at one point: the top of `_app.run`, past the completion hot path,
which must stay stdlib-only and import-free of the framework.

A supervisor asks for a stop with a signal, and the senders are
ordinary: `timeout`, `docker stop`, `kill`, a cancelled CI job, systemd, k8s.
Left at its default disposition SIGTERM kills footman where it stands — no
receipt, no `--json` envelope, and every child orphaned, because a spawned
child leads its own process group precisely so the terminal's signals do
*not* reach it. So a stop signal raises `Stop` in the main thread, which
unwinds to the same place Ctrl-C does and inherits the reap, the receipt and
the envelope by construction; only the word and the exit code differ.

The event and its binding are separate. There is one internal notion, "stop
requested", and only the binding is per-platform:

    POSIX     SIGTERM -> 143, SIGHUP -> 129
    Windows   SIGBREAK -> 143 (CTRL_BREAK_EVENT, what a cancelled CI job
              sends; the SIGTERM constant there only ever arrives through
              TerminateProcess, which no program in any language can catch)

The codes are footman's contract rather than the OS's, so they are the same
number everywhere — 130 for Ctrl-C already is, despite Windows having no
128+N convention, and special-casing a platform would be the leak. SIGKILL
and `taskkill /F` stay uncatchable; nothing here pretends otherwise.
"""

from __future__ import annotations

import contextlib
import signal
import sys
from collections.abc import Callable, Generator
from types import FrameType
from typing import Any

# 128 + the signal's number, which is what a shell prints for a process the
# OS killed — the same number, reached by exiting rather than by dying, so a
# `timeout`-wrapped `fm` reads as it always did while the run still gets to
# clean up on the way out.
TERMINATED = 143
HUNG_UP = 129


class Stop(BaseException):
    """A stop was asked for by signal.

    A `BaseException`, like `KeyboardInterrupt`: the `except BaseException`
    arms in the scheduler and the fan-out are what reap the in-flight
    process trees, and a task body's `except Exception` must no more swallow
    a supervisor's stop than it swallows Ctrl-C.
    """

    def __init__(self, word: str, code: int) -> None:
        super().__init__(word)
        self.word = word
        self.code = code


def _bindings() -> tuple[tuple[int, str, int], ...]:
    """Which signals mean "stop" here, and what each one exits as."""
    if sys.platform == "win32":
        # Only SIGBREAK: binding the Windows SIGTERM constant would install a
        # handler for something that never arrives.
        return ((signal.SIGBREAK, "terminated", TERMINATED),)
    return (
        (signal.SIGTERM, "terminated", TERMINATED),
        (signal.SIGHUP, "hung up", HUNG_UP),
    )


# Latched for the run: the first stop wins and later ones are ignored.
_stopping = False


def _handler(word: str, code: int) -> Callable[[int, FrameType | None], None]:
    def stop(_signum: int, _frame: FrameType | None) -> None:
        global _stopping
        # A supervisor often sends more than one — a cancelled GitHub job
        # sends SIGINT and then SIGTERM — and a second raise landing inside
        # the reap would abandon it half done, orphaning exactly the trees
        # this exists to collect. The shutdown is bounded by the kill grace,
        # and the supervisor's SIGKILL is the real backstop.
        if _stopping:
            return
        _stopping = True
        raise Stop(word, code)

    return stop


@contextlib.contextmanager
def installed() -> Generator[None]:
    """Wire the signals for one run, and put back what was there before.

    Restoring matters because `run()` is a library call as much as an entry
    point: an embedding program keeps its own handlers, and a suite that ran
    a hundred invocations leaves the process as it found it. A nested run
    (a task body driving a `Runner`) is on a worker thread, where
    `signal.signal` refuses outright — it installs nothing and the outer
    run's wiring keeps standing, which is the right answer anyway.
    """
    global _stopping
    _stopping = False
    previous: list[tuple[int, Any]] = []
    try:
        for signum, word, code in _bindings():
            with contextlib.suppress(ValueError, OSError):
                previous.append((signum, signal.signal(signum, _handler(word, code))))
        yield
    finally:
        for signum, handler in reversed(previous):
            # `None` is what Python reports for a handler installed from C,
            # which it cannot put back — better left alone than crashed on.
            if handler is not None:
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(signum, handler)
