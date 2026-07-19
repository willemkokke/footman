"""The run context, the `run()` helper, and `parallel()`.

A task never *needs* a context parameter: `run()` reads the current context
from a contextvar footman sets around each running task, so a task body can just
call `run("ruff check src")`. A task MAY declare a first parameter named
`ctx` (or annotated `Context`) to get the object explicitly.

Output is routed through the context so parallel tasks don't interleave: a global
`sys.stdout` proxy dispatches every write to the running task's `sink`. In
sequential mode a task's sink is the real stdout (live); in parallel mode it is a
per-task buffer, flushed atomically when the task finishes.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO


@dataclass
class StepResult:
    """The outcome of one `run()` call, recorded on the context."""

    command: str
    code: int
    output: str
    duration: float


@dataclass
class Context:
    """State for one running task: environment, flags, passthrough, output."""

    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    dry_run: bool = False
    quiet: bool = False
    verbose: bool = False
    no_color: bool = False
    prog: str = "fm"  # the invoking CLI's command name (the brand's prog)
    # The *user asked* for one-at-a-time (-s or config) — parallel() honours
    # it too. Deliberately not set by the scheduler's own single-node
    # routing, which is presentation, not a request to serialise bodies.
    sequential: bool = False
    # The effective parallel width (-j/--jobs, config `jobs`, or the
    # cores-minus-one default) — caps parallel() pools in task bodies.
    jobs: int = 0  # 0: unset (plain calls outside a run) → no cap
    passthrough: list[str] = field(default_factory=list)
    tty: bool = False  # use live rewrite/colour (sequential live only)
    sink: TextIO | None = None  # where output goes; None -> real stdout
    steps: list[StepResult] = field(default_factory=list)


_current: ContextVar[Context | None] = ContextVar("footman_context", default=None)


def current() -> Context:
    """The context of the running task (a fresh default one outside a run)."""
    ctx = _current.get()
    return ctx if ctx is not None else Context()


@contextlib.contextmanager
def use_context(ctx: Context | None = None) -> Iterator[Context]:
    """Install *ctx* as the current run context for the duration of the block.

    The public seam for calling tasks from other Python code — tests included:
    `run()` and `tools.*` inside the block read this context instead of a
    fresh default. `footman.testing.recording` builds on it.

    ```python
    with use_context(Context(env={"CI": "1"})) as ctx:
        deploy()
    assert ctx.steps[0].code == 0
    ```
    """
    installed = ctx if ctx is not None else Context()
    token = _current.set(installed)
    try:
        yield installed
    finally:
        _current.reset(token)


def passthrough() -> list[str]:
    """Arguments after `--` on the command line, for the running task."""
    return list(current().passthrough)


class RunFailed(Exception):
    """A `run()` command exited non-zero (and `nofail` was not set)."""

    def __init__(self, result: StepResult) -> None:
        self.result = result
        super().__init__(f"`{result.command}` exited with code {result.code}")


def context_param_name(sig: inspect.Signature) -> str | None:
    """Name of the task's context parameter (first param `ctx` / `Context`)."""
    params = list(sig.parameters.values())
    if not params:
        return None
    first = params[0]
    if first.name == "ctx" or first.annotation is Context:
        return first.name
    return None


# --- output routing ----------------------------------------------------------


# The run's live status line (duck-typed — a `_progress.StatusLine`),
# registered by the scheduler for the duration of a run. context stays
# ignorant of _progress on purpose; outside a run there is none.
_status: Any = None


def set_status(status: Any) -> None:
    global _status
    _status = status


def active_status() -> Any:
    return _status


class _Router:
    """A `sys.stdout` proxy that sends each write to the current task's sink."""

    def __init__(self, real: TextIO) -> None:
        self.real = real
        try:
            self._tty = real.isatty()
        except Exception:
            self._tty = False

    def write(self, s: str) -> int:
        sink = current().sink
        if sink is not None:
            return sink.write(s)
        # A real-terminal write: the live status line (if any) must clear
        # itself first and learn whether the cursor now sits at column 0.
        if self._tty and _status is not None:
            _status.notify(s)
        return self.real.write(s)

    def flush(self) -> None:
        (current().sink or self.real).flush()

    def isatty(self) -> bool:
        return self.real.isatty()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


_router: _Router | None = None
_err_router: _Router | None = None


def real_stdout() -> TextIO:
    """The underlying stdout, bypassing the routing proxy."""
    return _router.real if _router is not None else sys.stdout


def real_stderr() -> TextIO:
    """The underlying stderr, bypassing the routing proxy."""
    return _err_router.real if _err_router is not None else sys.stderr


@contextlib.contextmanager
def routing():
    """Install stdout/stderr routers for the duration of a run.

    Both streams proxy through the running task's sink, so an in-process tool's
    stderr is captured alongside its stdout (matching the merged subprocess
    capture) instead of leaking to the terminal. The routers are *stacked*, not
    reset to None: a nested run — e.g. `tools.pytest(in_process=True)` driving
    the shipped `fm` fixture — restores the outer routers on exit, so the outer
    run's capture keeps working afterwards.
    """
    global _router, _err_router
    prev_out, prev_err = _router, _err_router
    real_out, real_err = sys.stdout, sys.stderr
    # A tool (or footman's own status line) may emit non-ASCII on a
    # locale-encoded pipe (cp1252 on Windows CI, errors='strict' by default);
    # degrade unencodable glyphs to '?' instead of crashing the run.
    for stream in (real_out, real_err):
        with contextlib.suppress(Exception):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    _router, _err_router = _Router(real_out), _Router(real_err)
    sys.stdout, sys.stderr = _router, _err_router  # type: ignore[assignment]
    try:
        # (real stdout, real stderr): task blocks land on the first, the live
        # status line on the second — stdout is the answer, stderr commentary.
        yield real_out, real_err
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        _router, _err_router = prev_out, prev_err


# --- run() -------------------------------------------------------------------


def _is_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _label(cmd: Any, args: tuple[Any, ...]) -> str:
    if callable(cmd):
        name = getattr(cmd, "__qualname__", getattr(cmd, "__name__", repr(cmd)))
        return " ".join([f"{name}()", *map(str, args)]).strip()
    return cmd if isinstance(cmd, str) else " ".join(map(str, cmd))


def _call_for_code(cmd: Callable[..., Any], args: tuple[Any, ...]) -> int:
    try:
        returned = cmd(*args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    if isinstance(returned, int) and not isinstance(returned, bool):
        return returned
    return 0


_state_lock = threading.RLock()


@contextlib.contextmanager
def _process_state(env: dict[str, str], cwd: Path | None) -> Iterator[None]:
    """Patch `os.environ` / the process cwd around an in-process callable.

    In-process tools must honor the same env overlay and run-from-defining-
    folder contract the subprocess branch of the *same* call already obeys.
    `os.chdir` and `os.environ` are process-global, so any change is guarded by a
    re-entrant lock (a callable may itself call `run()`) and restored on exit —
    calls that need a patch therefore serialize. The common case (no overlay, no
    cwd — in-memory Group tasks have no defining dir) takes the lock-free fast
    path, so barrier-overlap parallelism stays fully concurrent.
    """
    if not env and cwd is None:
        yield
        return
    with _state_lock:
        saved_env = os.environ.copy()
        saved_cwd = os.getcwd() if cwd is not None else None
        try:
            os.environ.update(env)
            if cwd is not None:
                os.chdir(cwd)
            yield
        finally:
            if saved_cwd is not None:
                os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)


def _run_callable(
    cmd: Callable[..., Any],
    args: tuple[Any, ...],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> tuple[int, str]:
    """Run a callable — parallel-safe under the router, honoring env/cwd.

    With the router installed, every write this thread makes already
    dispatches through `current().sink`, so capture is a thread-confined
    sink swap — concurrent in-process tools never touch each other's
    output. Outside a routed run (bare calls in scripts/tests) there is no
    router to lean on, so fall back to the classic global redirect.

    `capture=False` skips the buffer entirely (live output, returns `''` like
    the subprocess branch) — for serve-style tasks that must not buffer
    unboundedly. The env overlay and cwd are applied process-globally via
    `_process_state`; the `capture=False` short-circuit runs *inside* it so
    uncaptured callables keep cwd/env too.
    """
    ctx = current()
    overlay = {**ctx.env, **(env or {})}
    target_cwd = Path(cwd) if cwd is not None else ctx.cwd
    with _process_state(overlay, target_cwd):
        if not capture:
            return _call_for_code(cmd, args), ""
        buffer = io.StringIO()
        if _router is not None:
            saved = ctx.sink
            ctx.sink = buffer
            try:
                code = _call_for_code(cmd, args)
            finally:
                ctx.sink = saved
            return code, buffer.getvalue()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = _call_for_code(cmd, args)
        return code, buffer.getvalue()


def _run_subprocess(
    argv: list[str] | str,
    env: dict[str, str],
    cwd: Path | None,
    capture: bool,
    encoding: str | None = "utf-8",
) -> tuple[int, str]:
    # Dev tools (pytest, ruff, git, uv) emit UTF-8 regardless of the OS code
    # page, so decode as UTF-8 by default rather than the locale encoding
    # (cp1252 on Windows would mojibake the capture). `encoding=None` restores
    # locale behavior. `errors="replace"` is the never-crash net either way.
    proc = subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding=encoding,
        errors="replace",
    )
    output = "" if not capture else (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def run(
    cmd: str | list[str] | Callable[..., Any],
    *args: Any,
    nofail: bool = False,
    silent: bool = False,
    capture: bool = True,
    title: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    encoding: str | None = "utf-8",
) -> int:
    """Run a command or a Python callable in the current task's context.

    Subprocess output is decoded as UTF-8 by default; pass `encoding=` for a
    tool that speaks another code page, or `encoding=None` for the locale
    default. Ignored for callables (in-process, no bytes boundary).
    """
    ctx = current()
    out = sys.stdout
    label = title or _label(cmd, args)

    if ctx.dry_run:
        # Record the step even when not executing: `dry_run` + `quiet` is the
        # silent-capture mode `footman.testing` builds on.
        ctx.steps.append(StepResult(label, 0, "", 0.0))
        if not ctx.quiet:
            out.write(f"$ {label}\n")
        return 0

    show = not silent and not ctx.quiet
    color = ctx.tty and not ctx.no_color and "NO_COLOR" not in os.environ
    if show:
        out.write(f"→ {label}" if ctx.tty else f"→ {label}\n")
        out.flush()

    start = time.perf_counter()
    if callable(cmd):
        code, output = _run_callable(cmd, args, capture=capture, env=env, cwd=cwd)
    else:
        if isinstance(cmd, str):
            # POSIX shells split on shlex rules; Windows command lines are a
            # single string (CreateProcess) and shlex would mangle backslash
            # paths — hand the string straight to subprocess there.
            argv: list[str] | str = cmd if sys.platform == "win32" else shlex.split(cmd)
        else:
            argv = [str(a) for a in cmd]
        run_env = {**os.environ, **ctx.env, **(env or {})}
        cwd_path = Path(cwd) if cwd is not None else ctx.cwd
        code, output = _run_subprocess(argv, run_env, cwd_path, capture, encoding)
    duration = time.perf_counter() - start
    ctx.steps.append(StepResult(label, code, output, duration))

    if show:
        ok = code == 0
        if ctx.tty:
            mark = (
                ("\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m")
                if color
                else ("ok" if ok else "FAIL")
            )
            out.write(f"\r\033[K{mark} {label}  ({duration:.1f}s)\n")
        else:
            out.write(f"{'ok' if ok else 'FAIL'}: {label}  ({duration:.1f}s)\n")
        if capture and output and (not ok or ctx.verbose):
            out.write(output if output.endswith("\n") else output + "\n")
        out.flush()

    if code != 0 and not nofail:
        raise RunFailed(ctx.steps[-1])
    return code


# --- parallel() --------------------------------------------------------------


def parallel(*calls: Callable[[], Any], keep_going: bool = False) -> list[int]:
    """Run task calls / thunks concurrently; wait; fail if any fail.

    Each call runs in a child of the current context with its own output buffer,
    flushed atomically on completion so concurrent output never interleaves.
    Pass task functions directly (`parallel(lint, typecheck)`) or thunks for
    arguments (`parallel(lambda: build("web"), lambda: build("api"))`).
    """
    from concurrent.futures import ThreadPoolExecutor

    parent = current()
    dest = parent.sink or real_stdout()
    dest_is_real = parent.sink is None
    lock = threading.Lock()
    # parallel() children are units on the live status line, exactly like
    # scheduler nodes — a chain and a task-body fan-out present identically.
    status = _status
    if status is not None:
        status.unit_added(len(calls))

    def invoke(call: Callable[[], Any]) -> tuple[int, BaseException | None]:
        name = getattr(call, "__name__", "task")
        if name == "<lambda>":
            name = "…"  # an anonymous thunk has no honest name to show
        if status is not None:
            status.unit_started(name)
        child = replace(parent, sink=io.StringIO(), steps=[])
        token = _current.set(child)
        try:
            returned = call()
            code = returned if _is_code(returned) else 0
            error: BaseException | None = None
            # A thunk that *returns* a non-zero code failed just as surely as one
            # that raised RunFailed. Synthesize the failure here so the gate below
            # treats both uniformly; `keep_going` still collects the code.
            if code != 0:
                error = RunFailed(StepResult(_label(call, ()), code, "", 0.0))
        except RunFailed as exc:
            code, error = exc.result.code or 1, exc
        except Exception as exc:  # a failed call must not crash the pool
            code, error = 1, exc
        finally:
            _current.reset(token)
        with lock:
            blob = child.sink.getvalue()  # type: ignore[union-attr]
            if status is not None and dest_is_real:
                # This write bypasses the routers (dest is the raw stream):
                # tell the status line to get out of the way itself.
                status.notify(blob)
            dest.write(blob)
            dest.flush()
            # Surface the child's run() steps on the parent, in completion order,
            # so they appear in `--json` and `recording()` (F12).
            parent.steps.extend(child.steps)
        if status is not None:
            status.unit_finished(name, error is None)
        return code, error

    # -s reaches inside tasks (one worker serialises the calls in
    # submission order), and -j caps the width; same code path either way.
    if parent.sequential:
        workers = 1
    elif parent.jobs > 0:
        workers = max(1, min(parent.jobs, len(calls)))
    else:
        workers = max(1, len(calls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(invoke, calls))

    if not keep_going:
        for _code, error in outcomes:
            if error is not None:
                raise error
    return [code for code, _ in outcomes]
