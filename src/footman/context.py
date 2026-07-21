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
import functools
import inspect
import io
import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO


@dataclass
class StepResult:
    """The outcome of one `run()` call, recorded on the context."""

    command: str
    """The command line that ran, normalised for reading — options in
    separated form, values shell-quoted. What `recording()` asserts against,
    and what the terminal shows."""
    code: int
    """The exit code (0 is success)."""
    output: str
    """Captured combined output; empty when the step streamed instead."""
    duration: float
    """Wall-clock seconds the step took."""
    raw: str = ""
    """The exact command line executed, shell-quoted — the bytes footman
    handed the tool, which may spell an option `--flag=value` where
    `command` shows `--flag value`. What `--verbose` prints. Equal to
    `command` when there is nothing to normalise."""


@dataclass
class Context:
    """State for one running task: environment, flags, passthrough, output."""

    env: dict[str, str] = field(default_factory=dict)
    """Extra environment variables overlaid on every `run()` subprocess."""
    cwd: Path | None = None
    """Where `run()` executes: the folder that defined the task; `None`
    means the process cwd (plain calls outside a footman run)."""
    dry_run: bool = False
    """`--dry-run`: `run()` prints and records the command, executes
    nothing, and reports success."""
    quiet: bool = False
    """`--quiet`: suppress step lines and the per-task summary."""
    verbose: bool = False
    """`--verbose`: replay captured `run()` output even on success."""
    no_color: bool = False
    """`--no-color` (or `NO_COLOR`): never emit ANSI styling."""
    prog: str = "fm"
    """The invoking CLI's command name — a branded CLI's own `prog`, so
    tasks (the taskdocs plugin, say) can speak the brand's name."""
    sequential: bool = False
    """The *user asked* for one-at-a-time (`-s` or config) — `parallel()`
    honours it too. Deliberately not set by the scheduler's own
    single-node routing, which is presentation, not a request to
    serialise task bodies."""
    assume_yes: bool = False
    """`--yes`: every `confirm()` gate auto-answers yes, for CI and scripts."""
    no_input: bool = False
    """`--no-input`: never prompt — a required prompt errors instead of
    asking, so an unattended run fails loudly rather than hanging."""
    fetch_backend: str = ""
    """`[fetch] backend` from the config ladder — which engine `fetch()`
    downloads with. Empty means the default (stdlib urllib)."""
    jobs: int = 0
    """The effective parallel width (`-j/--jobs`, config `jobs`, or the
    cores-minus-one default) — caps `parallel()` pools in task bodies.
    `0` means unset (plain calls outside a run): no cap."""
    task: str = ""
    """Who is running, for the step lines' name column: the scheduler
    sets the dotted task name, `parallel()` its child's name. Empty
    outside runs."""
    fn: Any = None
    """The running task's own function — what `inherited()` reads to find
    the task this one shadows. `None` outside a run."""
    name_width: int = 0
    """The widest sibling task name, so step-line columns align."""
    passthrough: list[str] = field(default_factory=list)
    """Everything after `--` on the command line, verbatim."""
    tty: bool = False
    """Output dresses for a terminal (colour, marks). Live in-place
    rewrites additionally require output to be uncaptured."""
    sink: TextIO | None = None
    """Where this task's output goes: a capture buffer in buffered
    (parallel) mode, `None` for the real stdout (live mode)."""
    interactive: bool = False
    """`@task(interactive=True)`: the task owns the real terminal — output is
    not captured and it holds sole stdio, so its body may prompt or run a
    REPL. Mid-body `prompt()`/`confirm()`/`select()` are allowed only here."""
    in_task: bool = False
    """True while a task *body* runs (the scheduler sets it around the call),
    so the interactive primitives tell a guarded mid-body call from the
    framework's own up-front `ask()` resolution."""
    steps: list[StepResult] = field(default_factory=list)
    """Every `run()` this task made, in order — what `recording()` and
    the `--json` envelope read."""


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


def progress(done: int, total: int = 0) -> None:
    """Report this task's own progress: *done* of *total* units.

    Some work knows exactly how far along it is — 23 of 150 migrations,
    bytes of a download — and that is better evidence than any duration
    history. A reporting task's counts drive the live bar directly
    (counted beats estimated), so the bar is honest on the very first
    run, where the estimator would still be guessing.

    ```python
    @task
    def migrate():
        for i, record in enumerate(records, 1):
            apply(record)
            progress(i, len(records))
    ```

    A `total` of 0 (or less) clears the report, returning the run to its
    estimate. Outside a run, or with no live status line, this is a
    no-op — plain calls and captured runs cost nothing.
    """
    status = active_status()
    if status is None:
        return
    ctx = current()
    name = ctx.task or "task"
    if total > 0:
        status.unit_counted(name, max(done, 0), total)
    else:  # a cleared report: back to the estimate
        with contextlib.suppress(Exception):
            status.counted.pop(name, None)


def track(iterable: Any, total: int | None = None) -> Any:
    """Iterate *iterable*, reporting progress as it goes.

    The ergonomic form of `progress()`: the total comes from `len()` when
    the iterable has one, or from *total* when you know it for a
    generator. Without either, iteration still works — the run simply
    keeps whatever progress it had.

    ```python
    @task
    def migrate():
        for record in track(load_records()):
            apply(record)
    ```
    """
    if total is None:
        try:
            total = len(iterable)
        except TypeError:
            total = 0
    done = 0
    try:
        for item in iterable:
            yield item
            done += 1
            if total:
                progress(done, total)
    finally:
        if total:  # leaving early (a break, an exception) resets the report
            progress(0, 0)


def inherited() -> Any:
    """The task this one shadows in the cascade — footman's `super()`.

    A nearer `tasks.py` overriding a task by name usually wants to *extend*
    it, not replace it. Call this inside the overriding task's body to get
    the task it shadows, then call that like the plain function it is:

    ```python
    # svc/api/tasks.py — the root also defines `check`
    @task
    def check(fix: bool = False, contracts: bool = True):
        inherited()(fix=fix)          # arguments are forwarded explicitly
        if contracts:
            run("./verify-contracts.sh")
    ```

    Forwarding is deliberately manual: the two signatures are independent
    (a leaf usually adds a parameter), so automatic forwarding could only
    drop arguments silently or fail at run time — where spelling the call
    out shows you the mismatch as you type it. Being an ordinary call,
    it also runs to completion
    before the next statement — and composes with `parallel(inherited(),
    extra)` when you want otherwise.

    `fm --where <task>` lists the whole shadow chain; `fm --help <task>`
    shows the inherited task's options, so you can read the forwarding
    call straight off it.
    """
    from footman import discover

    fn = current().fn
    if fn is None:
        raise RuntimeError(
            "inherited() works inside a running task — footman resolves the "
            "task being shadowed from the one currently running"
        )
    previous = discover.shadowed(fn)
    if previous is None:
        name = current().task or getattr(fn, "__name__", "this task")
        raise RuntimeError(
            f"{name} does not shadow an inherited task — nothing above it in "
            f"the cascade defines that name (fm --where {name} lists the chain)"
        )

    @functools.wraps(previous)
    def call_inherited(*args: Any, **kwargs: Any) -> Any:
        # Point the context at the task being called, so an `inherited()`
        # inside *it* walks one level further up instead of resolving to
        # itself — a three-deep cascade would otherwise recurse forever.
        ctx = current()
        saved = ctx.fn
        ctx.fn = previous
        try:
            return previous(*args, **kwargs)
        finally:
            ctx.fn = saved

    return call_inherited


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

# The widest command label seen, for aligning the step lines' time column.
# Seeded from the previous run's history (so alignment is right from the
# first line on a warm run) and grown as a running max on a cold one.
_cmd_width: int = 0


def seed_cmd_width(width: int) -> None:
    global _cmd_width
    _cmd_width = max(0, width)


def cmd_width() -> int:
    return _cmd_width


def _observe_cmd(label: str) -> int:
    """Return the padding width for *label*, learning as labels stream by."""
    global _cmd_width
    if len(label) > _cmd_width:
        _cmd_width = len(label)
    return _cmd_width


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


_UNSET: Any = object()  # "no default given" — None is a valid default/value

_prompt_lock = threading.Lock()


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _scrub(text: str) -> str:
    """Drop control characters (ESC included) from text echoed to the terminal,
    so an untrusted `select()` label or message can't inject ANSI escapes — the
    terminal-injection class that has bitten other CLIs."""
    return "".join(c for c in text if c.isprintable() or c == "\t")


def _prompt_core(
    message: str = "", *, default: str | None = None, secret: bool = False
) -> str:
    """The prompt mechanics, unguarded. Writes to the real terminal on stderr
    (never captured, never in `--json` stdout), serialises on `_prompt_lock`,
    clears the live status line, and degrades off a tty (returns `default`, or
    raises). The framework's `ask()` resolution calls this directly; user code
    goes through the guarded `prompt()`."""
    if not _stdin_is_tty():
        if default is not None:
            return default
        raise RuntimeError(
            "no terminal is attached, so there is no one to ask. Pass a "
            "default for unattended runs, or take the value as a task "
            "parameter (a CLI flag) instead."
        )
    err = real_stderr()
    status = active_status()
    with _prompt_lock:
        if status is not None:
            status.notify(message or " ")  # clear the live status line
        if secret:
            import getpass

            value = getpass.getpass(message, stream=err).rstrip("\n")
        else:
            err.write(message)
            err.flush()
            value = sys.stdin.readline().rstrip("\n")
        if status is not None:
            status.notify("\n")  # Enter returned the cursor to column 0
    return default if value == "" and default is not None else value


def _guard_interactive(what: str) -> Context:
    """Refuse a mid-body interactive call in a non-interactive task.

    Inside an ordinary (captured, possibly parallel) task body the prompt
    would be swallowed by the capture buffer or race a sibling for the
    terminal, so it is a loud, taught error rather than a silent hang. The
    framework's own up-front `ask()` resolution runs with `in_task` unset and
    is never caught here. Returns the active context (for `--no-input`/`--yes`)."""
    ctx = current()
    if ctx.in_task and not ctx.interactive:
        raise RuntimeError(
            f"{what} was called inside task {ctx.task or '?'!r}, which is not "
            f"interactive. Either mark it `@task(interactive=True)` so it owns "
            f"the terminal, or declare the value as a parameter with `ask()` so "
            f"footman asks before the task runs."
        )
    return ctx


def prompt(
    message: str = "", *, default: str | None = None, secret: bool = False
) -> str:
    """Ask the person running the task for a line of input.

    A bare `input()` doesn't work in a task: its prompt goes to stdout, which
    footman buffers per task so parallel output never interleaves (and `--json`
    stays one envelope), so the prompt is swallowed and the task looks hung.
    `prompt()` writes to the real terminal on stderr instead — never captured —
    and serialises concurrent prompts.

    Usable only inside an `@task(interactive=True)` task; called in an ordinary
    task body it raises a taught error naming the two fixes. Off a terminal,
    under `--no-input`, or when it would otherwise block, it returns `default`
    if given, else raises — an unattended run fails loudly. For a value a
    script must supply, take it as a task parameter (a CLI flag) instead.
    """
    ctx = _guard_interactive("prompt()")
    if ctx.no_input:
        if default is not None:
            return default
        raise RuntimeError(
            "prompt(): --no-input is set, so nothing can be asked. Pass a "
            "default, or supply the value as a task parameter (a CLI flag)."
        )
    return _prompt_core(message, default=default, secret=secret)


def confirm(message: str, *, default: bool = False) -> bool:
    """Ask a yes/no question. `--yes` auto-answers yes; Enter alone takes
    `default`; off a terminal or under `--no-input` the answer is `default`.
    Guarded like `prompt()` — interactive tasks only."""
    ctx = _guard_interactive("confirm()")
    if ctx.assume_yes:
        return True
    if ctx.no_input:
        return default
    reply = _prompt_core(
        f"{message} {'[Y/n]' if default else '[y/N]'} ",
        default="y" if default else "n",
    )
    return reply.strip().lower() in ("y", "yes")


def select(
    message: str,
    options: Sequence[Any],
    *,
    multiple: bool = False,
    default: Any = _UNSET,
) -> Any:
    """Let the person pick from a runtime-computed list — the one interactive
    case a flag can't cover, because the options aren't known until the task
    runs (which changed packages to release, which stale branches to delete).

    `options` are strings, or `(label, value)` pairs to show one thing and
    return another. `multiple=True` returns the chosen subset as a list;
    otherwise one value is returned. Guarded like `prompt()` (interactive tasks
    only), and off a terminal or under `--no-input` it returns `default`, or
    raises if none was given.
    """
    ctx = _guard_interactive("select()")
    opts = list(options)
    if not opts:
        raise ValueError("select(): no options to choose from")
    labels = [o[0] if isinstance(o, tuple) and len(o) == 2 else str(o) for o in opts]
    values = [o[1] if isinstance(o, tuple) and len(o) == 2 else o for o in opts]
    if ctx.no_input or not _stdin_is_tty():
        if default is not _UNSET:
            return default
        raise RuntimeError(
            "select(): nothing can be asked (no terminal, or --no-input). Pass "
            "default=…, or take the choice as a task parameter."
        )
    err = real_stderr()
    status = active_status()
    with _prompt_lock:
        if status is not None:
            status.notify(" ")
        err.write(_scrub(message.rstrip()) + "\n")
        for i, label in enumerate(labels, 1):
            err.write(f"  {i}) {_scrub(label)}\n")
        hint = "numbers, comma-separated; 'all'; 'none'" if multiple else "a number"
        err.write(f"select ({hint}): ")
        err.flush()
        line = sys.stdin.readline().strip()
        if status is not None:
            status.notify("\n")
    if line == "" and default is not _UNSET:
        return default
    if multiple:
        return [values[i] for i in _parse_multi(line, len(values))]
    return values[_parse_one(line, len(values))]


def _parse_one(line: str, n: int) -> int:
    try:
        i = int(line)
    except ValueError:
        raise RuntimeError(f"select(): {line!r} is not a number 1-{n}.") from None
    if not 1 <= i <= n:
        raise RuntimeError(f"select(): {i} is out of range 1-{n}.")
    return i - 1


def _parse_multi(line: str, n: int) -> list[int]:
    low = line.lower()
    if low in ("all", "*"):
        return list(range(n))
    if low == "none":
        return []
    return sorted({_parse_one(tok, n) for tok in line.replace(",", " ").split()})


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


@dataclass(frozen=True)
class Invocation:
    """What a `run()` call *is*, apart from how it's spelled to execute.

    The `tools.*` bridge builds one of these so `run()` can show a readable,
    syntax-highlighted command line — options in separated form, tagged by
    role — while executing whatever the tool actually needs (attached flags,
    or an in-process callable). `parts` is the normalised, human form;
    `exact` is the literal argv, shown under `--verbose` and always
    copy-pasteable. Passing it is how the two are kept from drifting: both
    come from one translation of one call.
    """

    parts: tuple[tuple[str, str], ...]
    exact: tuple[str, ...]

    def text(self, *, exact: bool) -> str:
        """The plain command line — the width-measured, non-colour form."""
        if exact:
            return " ".join(_shell_quote(a) for a in self.exact)
        return " ".join(text for _, text in self.parts)

    def painted(self, *, color: bool, exact: bool) -> str:
        """The shown command line, role-coloured when the stream wants it."""
        if exact or not color:
            return self.text(exact=exact)
        from footman._describe import paint_cli

        return paint_cli(list(self.parts), color)


def _shell_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


def _exact(cmd: Any, args: tuple[Any, ...]) -> str:
    """The exact executed command line for a direct (non-bridge) `run()`.

    A string is already a command line; a list is shell-quoted so it pastes;
    a callable has no command line, so its label stands in.
    """
    if callable(cmd):
        return _label(cmd, args)
    if isinstance(cmd, str):
        return cmd
    return " ".join(_shell_quote(str(a)) for a in cmd)


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


def _dim(text: str, color: bool) -> str:
    return f"\033[2m{text}\033[0m" if color else text


def _colored(ctx: Context) -> bool:
    return ctx.tty and not ctx.no_color and "NO_COLOR" not in os.environ


def _name_col(ctx: Context) -> str:
    """The step line's task-name column, padded so siblings align.

    Bold on colour terminals; empty (no column at all) outside a run, so a
    plain `run()` call keeps its old shape.
    """
    if not ctx.task:
        return ""
    padded = f"{ctx.task:<{max(ctx.name_width, len(ctx.task))}}"
    return (f"\033[1m{padded}\033[0m" if _colored(ctx) else padded) + "  "


def _step_line(ctx: Context, ok: bool, label: str, duration: float) -> str:
    """One completed step: mark · name · dimmed command · aligned time."""
    from footman._progress import fmt_secs

    color = _colored(ctx)
    time_text = f"({fmt_secs(duration)})"
    name = _name_col(ctx)
    # Times align to the widest command — remembered from the previous run
    # of this chain (a warm run aligns from its first line), learned as a
    # running max on a cold one. Never the terminal edge: that reads absurd
    # on wide terminals.
    label = f"{label:<{_observe_cmd(label)}}"

    if not ctx.tty:
        return f"{'ok' if ok else 'FAIL':<4} {name}{label}  {time_text}\n"

    mark = (
        ("\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m")
        if color
        else ("ok" if ok else "FAIL")
    )
    shown = f"\033[36m{time_text}\033[0m" if color else time_text
    # The time sits right after the command — a right-aligned column reads
    # absurd on wide terminals, with the time a screen away from its line.
    return f"{mark} {name}{_dim(label, color)}  {shown}\n"


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
    _show: Invocation | None = None,
) -> int:
    """Run a command or a Python callable in the current task's context.

    Subprocess output is decoded as UTF-8 by default; pass `encoding=` for a
    tool that speaks another code page, or `encoding=None` for the locale
    default. Ignored for callables (in-process, no bytes boundary).

    `_show` is an internal channel from the `tools.*` bridge: a structured
    view of the call, so the shown command line can be normalised and
    role-coloured while execution runs whatever the tool needs. An explicit
    `title` still wins; a direct `run([...])` is unaffected.
    """
    ctx = current()
    out = sys.stdout
    color = ctx.tty and not ctx.no_color and "NO_COLOR" not in os.environ
    if _show is not None and title is None:
        # `label` (recorded as .command, and the step-line receipt) is always
        # the normalised form, so a recording() assertion never depends on
        # --verbose. Only the live "about to / now running" line switches to
        # the exact spelling under --verbose; .raw always carries it.
        label = _show.text(exact=False)
        raw = _show.text(exact=True)
        shown = _show.painted(color=color, exact=ctx.verbose)
        shown_plain = _show.text(exact=ctx.verbose)
    else:
        label = title or _label(cmd, args)
        raw = _exact(cmd, args)
        shown = _dim(label, color)
        shown_plain = label

    if ctx.dry_run:
        # Record the step even when not executing: `dry_run` + `quiet` is the
        # silent-capture mode `footman.testing` builds on. The recorded label
        # is normalised; only the shown line colours or (under -v) goes exact.
        ctx.steps.append(StepResult(label, 0, "", 0.0, raw=raw))
        if not ctx.quiet:
            out.write(f"$ {shown if color else shown_plain}\n")
        return 0

    show = not silent and not ctx.quiet
    # `ctx.tty` means "this output dresses for a terminal" (colour, marks);
    # liveness is `sink is None`. A captured block styles for the terminal
    # it will replay onto, but in-place rewrites and the announce line stay
    # live-only: control bytes must never land in a capture buffer.
    live = ctx.sink is None
    if show:
        # The arrow announces what is *running now* — worth a line only
        # while output is live (a TTY rewrites it in place; a streamed CI
        # log may wait minutes under it). A captured block flushes when
        # the task is already done, where "starting X" directly above
        # "finished X" says nothing — the completion line carries it all.
        if ctx.tty and live:
            out.write(f"→ {_name_col(ctx)}{shown}")
            out.flush()
        elif live:
            out.write(f"→ {_name_col(ctx)}{shown_plain}\n")
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
    ctx.steps.append(StepResult(label, code, output, duration, raw=raw))

    if show:
        ok = code == 0
        prefix = "\r\033[K" if ctx.tty and live else ""
        out.write(f"{prefix}{_step_line(ctx, ok, label, duration)}")
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

    def _call_name(call: Callable[[], Any]) -> str:
        if isinstance(call, functools.partial):  # partial(fmt, check=True)
            call = call.func
        name = getattr(call, "__name__", "task")
        return "…" if name == "<lambda>" else name  # anonymous: no honest name

    # Sibling names are known up front, so their step lines can align.
    width = max((len(_call_name(c)) for c in calls), default=0)

    def invoke(call: Callable[[], Any]) -> tuple[int, BaseException | None]:
        name = _call_name(call)
        if status is not None:
            status.unit_started(name)
        child = replace(
            parent, sink=io.StringIO(), steps=[], task=name, name_width=width
        )
        token = _current.set(child)
        try:
            returned = call()
            code = returned if _is_code(returned) else 0
            error: BaseException | None = None
            # A thunk that *returns* a non-zero code failed just as surely as one
            # that raised RunFailed. Synthesize the failure here so the gate below
            # treats both uniformly; `keep_going` still collects the code.
            if code != 0:
                thunk = _label(call, ())
                error = RunFailed(StepResult(thunk, code, "", 0.0, raw=thunk))
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
