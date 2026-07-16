"""The run context and the ``run()`` helper — how tasks execute tools.

A task never *needs* a context parameter: ``run()`` reads the current context
from a contextvar footman sets around each running task, so a task body can just
call ``run("ruff check src")``. A task MAY declare a first parameter named
``ctx`` (or annotated :class:`Context`) to get the object explicitly — footman
recognises it and leaves it out of the CLI mapping.

``run()`` executes either a **command** (string or argv list -> subprocess) or a
**callable** (a Python entry point -> in-process, no spawn). It captures output
and stays quiet on success, replaying it only on failure (the CI-quiet model),
unless ``--verbose``. Under ``--dry-run`` it prints the command instead of
running it; under ``--json`` it records structured results without printing.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepResult:
    """The outcome of one ``run()`` call, recorded on the context."""

    command: str
    code: int
    output: str
    duration: float


@dataclass
class Context:
    """State for one running task: environment, flags, passthrough, steps."""

    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    dry_run: bool = False
    quiet: bool = False
    verbose: bool = False
    no_color: bool = False
    passthrough: list[str] = field(default_factory=list)
    report: bool = True  # print human progress (False under --json)
    steps: list[StepResult] = field(default_factory=list)


_current: ContextVar[Context | None] = ContextVar("footman_context", default=None)


def current() -> Context:
    """The context of the running task (a fresh default one outside a run)."""
    ctx = _current.get()
    return ctx if ctx is not None else Context()


def passthrough() -> list[str]:
    """Arguments after ``--`` on the command line, for the running task."""
    return list(current().passthrough)


class RunFailed(Exception):
    """A ``run()`` command exited non-zero (and ``nofail`` was not set)."""

    def __init__(self, result: StepResult) -> None:
        self.result = result
        super().__init__(f"`{result.command}` exited with code {result.code}")


def context_param_name(sig: inspect.Signature) -> str | None:
    """Name of the task's context parameter (first param ``ctx`` / ``Context``)."""
    params = list(sig.parameters.values())
    if not params:
        return None
    first = params[0]
    if first.name == "ctx" or first.annotation is Context:
        return first.name
    return None


# --- execution ---------------------------------------------------------------


def _label(cmd: Any, args: tuple[Any, ...]) -> str:
    if callable(cmd):
        name = getattr(cmd, "__qualname__", getattr(cmd, "__name__", repr(cmd)))
        return " ".join([f"{name}()", *map(str, args)]).strip()
    return cmd if isinstance(cmd, str) else " ".join(map(str, cmd))


def _run_callable(cmd: Callable[..., Any], args: tuple[Any, ...]) -> tuple[int, str]:
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            returned = cmd(*args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, buffer.getvalue()
    if isinstance(returned, int) and not isinstance(returned, bool):
        return returned, buffer.getvalue()
    return 0, buffer.getvalue()


def _run_subprocess(
    argv: list[str], env: dict[str, str], cwd: Path | None, capture: bool
) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        capture_output=capture,
        text=True,
    )
    output = "" if not capture else (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def _color(ctx: Context) -> bool:
    return sys.stdout.isatty() and not ctx.no_color and "NO_COLOR" not in os.environ


def run(
    cmd: str | list[str] | Callable[..., Any],
    *args: Any,
    nofail: bool = False,
    silent: bool = False,
    capture: bool = True,
    title: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> int:
    """Run a command or a Python callable in the current task's context."""
    ctx = current()
    label = title or _label(cmd, args)

    if ctx.dry_run:
        if ctx.report:
            print(f"$ {label}")
        return 0

    show = ctx.report and not silent and not ctx.quiet
    tty = _color(ctx)
    if show:
        sys.stdout.write(f"→ {label}" if tty else f"→ {label}\n")
        sys.stdout.flush()

    start = time.perf_counter()
    if callable(cmd):
        code, output = _run_callable(cmd, args)
    else:
        argv = shlex.split(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
        run_env = {**os.environ, **ctx.env, **(env or {})}
        cwd_path = Path(cwd) if cwd is not None else ctx.cwd
        code, output = _run_subprocess(argv, run_env, cwd_path, capture)
    duration = time.perf_counter() - start

    ctx.steps.append(StepResult(label, code, output, duration))

    if show:
        ok = code == 0
        if tty:
            mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
            sys.stdout.write(f"\r\033[K{mark} {label}  ({duration:.1f}s)\n")
        else:
            sys.stdout.write(f"{'ok' if ok else 'FAIL'}: {label}  ({duration:.1f}s)\n")
        if capture and output and (not ok or ctx.verbose):
            sys.stdout.write(output if output.endswith("\n") else output + "\n")
        sys.stdout.flush()

    if code != 0 and not nofail:
        raise RunFailed(ctx.steps[-1])
    return code
