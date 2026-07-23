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
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO


class Result(int):
    """The outcome of one `run()` call — and the value `run()` returns.

    A `Result` *is* the exit code: it subclasses `int`, so `code = run(...)`,
    `if run(...)`, and `run(...) == 0` all keep working. It also carries the
    captured output, split by stream, and the command that produced it — so
    `run("git rev-parse HEAD").stdout.strip()` reads the hash without the
    stderr noise glued on. `stdout`/`stderr` are separated for both subprocess
    and in-process runs; a streamed run (`capture=False`) leaves them empty.
    """

    command: str
    """The command line that ran, normalised for reading — options in
    separated form, values shell-quoted. What `recording()` asserts against,
    and what the terminal shows."""
    stdout: str
    """Captured standard output; empty when the step streamed instead."""
    stderr: str
    """Captured standard error; empty when the step streamed instead."""
    duration: float
    """Wall-clock seconds the step took."""
    raw: str
    """The exact command line executed, shell-quoted — the bytes footman
    handed the tool, which may spell an option `--flag=value` where
    `command` shows `--flag value`. What `--verbose` prints. Equal to
    `command` when there is nothing to normalise."""

    def __new__(
        cls,
        code: int,
        *,
        command: str = "",
        stdout: str = "",
        stderr: str = "",
        duration: float = 0.0,
        raw: str = "",
    ) -> Result:
        self = super().__new__(cls, code)
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.raw = raw or command
        return self

    @property
    def code(self) -> int:
        """The exit code (0 is success) — the same value the `Result` itself is."""
        return int(self)

    @property
    def ok(self) -> bool:
        """Whether the command succeeded (exit code 0)."""
        return self == 0

    @property
    def output(self) -> str:
        """`stdout` then `stderr`, concatenated — a convenience for "show me
        everything". NOT interleaved in real time (each stream is captured
        whole); when the order *across* the two streams matters, read `stdout`
        and `stderr` separately."""
        return self.stdout + self.stderr


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
    shell_default: str = ""
    """`[shell] default` from the config ladder — what `run(shell=True)` resolves
    to. Empty means `posix` (a POSIX shell everywhere: bash, then sh)."""
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
    """Where this task's stdout goes: a capture buffer in buffered
    (parallel) mode, `None` for the real stdout (live mode)."""
    err_sink: TextIO | None = None
    """Where this task's stderr goes. At task level it is the *same* buffer as
    `sink` (so the atomic parallel flush keeps stdout/stderr in order); a
    `run()` capturing an in-process callable temporarily points the two at
    separate buffers to split the step's streams for its `Result`."""
    interactive: bool = False
    """`@task(interactive=True)`: the task owns the real terminal — output is
    not captured and it holds sole stdio, so its body may prompt or run a
    REPL. Mid-body `prompt()`/`confirm()`/`select()` are allowed only here."""
    atomic: bool = False
    """`@task(atomic=True)`: this task's subprocesses opt out of fail-fast's
    kill — they run to completion so a mid-write can't be truncated."""
    keep_going: bool = False
    """This task's resolved (per-subtree) failure policy, tagged onto the
    subprocesses it spawns so a fail-fast failure elsewhere reaps only the
    fail-fast trees in a mixed run, sparing a keep-going task's."""
    in_task: bool = False
    """True while a task *body* runs (the scheduler sets it around the call),
    so the interactive primitives tell a guarded mid-body call from the
    framework's own up-front `ask()` resolution."""
    steps: list[Result] = field(default_factory=list)
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

    def __init__(self, result: Result) -> None:
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
    """A `sys.stdout`/`sys.stderr` proxy that sends each write to the current
    task's sink — `err_sink` for the stderr router, `sink` for stdout. At task
    level the two point at one buffer (combined, order-preserving); a `run()`
    capturing an in-process callable splits them to record the step's streams."""

    def __init__(self, real: TextIO, *, err: bool = False) -> None:
        self.real = real
        self._err = err
        try:
            self._tty = real.isatty()
        except Exception:
            self._tty = False

    def _sink(self) -> TextIO | None:
        ctx = current()
        return ctx.err_sink if self._err else ctx.sink

    def write(self, s: str) -> int:
        sink = self._sink()
        if sink is not None:
            return sink.write(s)
        # A real-terminal write: the live status line (if any) must clear
        # itself first and learn whether the cursor now sits at column 0.
        if self._tty and _status is not None:
            _status.notify(s)
        return self.real.write(s)

    def flush(self) -> None:
        (self._sink() or self.real).flush()

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
    _router, _err_router = _Router(real_out), _Router(real_err, err=True)
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
    """Quote one token so the shown command line pastes back into a real shell.

    Per-platform, so a Windows `.raw`/`--verbose` line actually round-trips:
    POSIX uses `shlex.quote`; Windows uses stdlib `subprocess.list2cmdline` (the
    exact inverse of the parsing `CreateProcess` does), never `shlex` — which
    emits POSIX single-quotes that cmd/PowerShell can't read. `list2cmdline`
    handles spaces/quotes/backslashes, not cmd metacharacters (`& | ^`), which
    is fine for a display line that already ran."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([text])
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


def _exit_code(exc: SystemExit) -> int:
    """A `SystemExit`'s exit code: its int code, 0 for `None`, else 1 (a message).

    `sys.exit()` / `raise SystemExit(...)` is a common "fail this step" idiom, and
    `SystemExit` is a `BaseException` — so every place that treats a call's
    outcome as a code must normalise it the same way, or it escapes uncaught."""
    return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)


def _call_for_code(cmd: Callable[..., Any], args: tuple[Any, ...]) -> int:
    try:
        returned = cmd(*args)
    except SystemExit as exc:
        return _exit_code(exc)
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
) -> tuple[int, str, str]:
    """Run a callable — parallel-safe under the router, honoring env/cwd.

    With the router installed, every write this thread makes already
    dispatches through `current().sink`/`err_sink`, so capture is a
    thread-confined swap of the two to fresh buffers — concurrent in-process
    tools never touch each other's output, and the callable's stdout and stderr
    land in separate buffers for the step's `Result`. Outside a routed run
    (bare calls in scripts/tests) there is no router to lean on, so fall back to
    the classic global redirect (still split, into two buffers).

    `capture=False` skips the buffers entirely (live output, returns `('', '')`
    like the subprocess branch) — for serve-style tasks that must not buffer
    unboundedly. The env overlay and cwd are applied process-globally via
    `_process_state`; the `capture=False` short-circuit runs *inside* it so
    uncaptured callables keep cwd/env too.
    """
    ctx = current()
    overlay = {**ctx.env, **(env or {})}
    target_cwd = Path(cwd) if cwd is not None else ctx.cwd
    with _process_state(overlay, target_cwd):
        if not capture:
            return _call_for_code(cmd, args), "", ""
        out_buf, err_buf = io.StringIO(), io.StringIO()
        if _router is not None:
            saved_out, saved_err = ctx.sink, ctx.err_sink
            ctx.sink, ctx.err_sink = out_buf, err_buf
            try:
                code = _call_for_code(cmd, args)
            finally:
                ctx.sink, ctx.err_sink = saved_out, saved_err
            return code, out_buf.getvalue(), err_buf.getvalue()
        with (
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
        ):
            code = _call_for_code(cmd, args)
        return code, out_buf.getvalue(), err_buf.getvalue()


# Live subprocesses footman has spawned, so fail-fast can terminate the ones
# still running when a sibling fails. A run in-process (a `tools` entry point,
# a callable) registers nothing — there is no child to kill, and it finishes.
# Each child records its task's keep-going policy, so a fail-fast failure can
# reap the fail-fast trees in a mixed run while a keep-going tree runs on.
_live_children: dict[subprocess.Popen[str], bool] = {}  # proc -> keep_going
_children_lock = threading.Lock()
_aborting = threading.Event()  # set once *any* termination (fail-fast/Ctrl-C) fired
_abort_full = threading.Event()  # set when the abort spares nothing (Ctrl-C, error)


def _kill_tree(proc: subprocess.Popen[str], *, force: bool) -> None:
    """Signal a spawned child *and its descendants*, not just the child itself.

    A killable child leads its own process group (POSIX) / group (Windows), and
    its grandchildren inherit it — so a group-wide signal reaps the workers a
    bare `terminate()` would orphan: pytest-xdist, `make -j`, a shell script's
    background jobs. POSIX sends SIGTERM, or SIGKILL once *force*. Windows has no
    SIGTERM/SIGKILL split, so `taskkill /T` (walk the tree by PID) `/F` (force)
    is one forceful shot for both — the escalation below is then a harmless
    re-run against a tree that is already gone.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        # Kill the whole group only when this child actually *leads* one (spawned
        # with start_new_session, so pgid == pid) — that is how the group reaches
        # its grandchildren. A child that shares footman's group — an interactive
        # task's child, or one a caller spawned without isolation — is signalled
        # alone: never killpg a group we don't own, or fail-fast could take out
        # the runner itself.
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, sig)
        else:
            os.kill(proc.pid, sig)


def _register_child(proc: subprocess.Popen[str], keep_going: bool = False) -> None:
    # Under the one lock: recording the child and reading the abort flags can't
    # interleave with `terminate_live_children` setting them and snapshotting — so
    # a child is killed either by that sweep or by this check, never missed.
    with _children_lock:
        _live_children[proc] = keep_going
        aborting = _aborting.is_set()
        full = _abort_full.is_set()
    # A child spawned after an abort fired self-terminates, so the doomed run
    # can't outrun the kill — but a keep-going child spawned after a *fail-fast*
    # abort (not a full Ctrl-C) is spared, matching the per-subtree policy.
    if aborting and (full or not keep_going):
        _kill_tree(proc, force=False)


def _forget_child(proc: subprocess.Popen[str]) -> None:
    with _children_lock:
        _live_children.pop(proc, None)


def reset_abort() -> None:
    """Clear the abort flags at the start of a run."""
    _aborting.clear()
    _abort_full.clear()


def terminate_live_children(grace: float = 2.0, *, failfast_only: bool = False) -> None:
    """Terminate still-running spawned subprocess *trees* — fail-fast's teeth.

    With *failfast_only* (a per-node fail-fast failure) only fail-fast children
    are reaped, so a keep-going task in a mixed run keeps running; the default
    (a full abort — Ctrl-C, an internal error) reaps everything.

    Each killable child was spawned in its own process group, so the SIGTERM
    (POSIX) / `taskkill /T` (Windows) here reaches its grandchildren too; the
    `communicate()` blocking each task's thread then returns and the task
    unwinds. The abort is *latched*, so a subprocess a still-running task spawns
    *after* this fires self-terminates on registration (the doomed run can't
    outrun the kill; `_register_child` applies the same failfast_only sparing). A
    group that ignores SIGTERM is SIGKILLed after *grace* seconds by a daemon
    watcher — a hung tool can't wedge the run. In-process runs register nothing,
    so they finish on their own — un-killable for free, which is the intended
    behaviour. This is also the Ctrl-C reaper: a group-isolated child no longer
    receives the terminal's SIGINT, so the abort paths call this by hand.
    """
    with _children_lock:
        _aborting.set()
        if not failfast_only:
            _abort_full.set()
        procs = [p for p, kg in _live_children.items() if not (failfast_only and kg)]
    for proc in procs:
        _kill_tree(proc, force=False)
    if not procs:
        return

    def _escalate() -> None:
        time.sleep(grace)
        for proc in procs:
            if proc.poll() is None:  # still alive → it ignored SIGTERM, force it
                _kill_tree(proc, force=True)

    threading.Thread(target=_escalate, daemon=True, name="fm-fail-fast-kill").start()


def _run_subprocess(
    argv: list[str] | str,
    env: dict[str, str],
    cwd: Path | None,
    capture: bool,
    encoding: str | None = "utf-8",
    killable: bool = True,
    isolate: bool = True,
    keep_going: bool = False,
) -> tuple[int, str, str]:
    # Dev tools (pytest, ruff, git, uv) emit UTF-8 regardless of the OS code
    # page, so decode as UTF-8 by default rather than the locale encoding
    # (cp1252 on Windows would mojibake the capture). `encoding=None` restores
    # locale behavior. `errors="replace"` is the never-crash net either way.
    #
    # Popen + a live registry (not subprocess.run) so a concurrent fail-fast can
    # terminate this child while its thread is blocked in communicate().
    #
    # An isolated child leads its own process group (POSIX session / Windows
    # group) so `terminate_live_children` can kill the whole tree, not just the
    # child — a tool's own workers (pytest-xdist, `make -j`) die with it. The
    # cost: it no longer receives the terminal's Ctrl-C, so the scheduler's abort
    # paths reap it by hand. Two children opt out and stay in footman's group:
    # `atomic` (fail-fast never kills it, so in-group keeps its Ctrl-C behaviour
    # unchanged) and `interactive` (it owns the real terminal — setsid would strip
    # its controlling tty and a full-screen program would misbehave). The kill
    # guard signals such an in-group child alone, never the shared group.
    group: dict[str, Any] = {}
    if isolate:
        if sys.platform == "win32":
            group["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            group["start_new_session"] = True
    proc = subprocess.Popen(
        argv,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding=encoding,
        errors="replace",
        **group,
    )
    # An `@task(atomic=True)` opts its child out of the registry: fail-fast
    # never kills it, so a mid-write (a formatter rewriting a file) can't be
    # truncated. It runs to completion; the run waits for it.
    if killable:
        _register_child(proc, keep_going)
    try:
        out, err = proc.communicate()
    finally:
        if killable:
            _forget_child(proc)
    if not capture:
        return proc.returncode, "", ""
    return proc.returncode, out or "", err or ""


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


# The tokens that mean "shell" and nothing else. `run(str)` splits and execs
# directly — no shell — so any of these would ride along as a *literal* argument,
# silently breaking a pipeline or redirect (a `tar … | ssh …` that never pipes).
_SHELL_OPERATORS = frozenset(
    {
        "|",
        "||",
        "|&",
        "&",
        "&&",
        ";",
        ";;",
        ">",
        ">>",
        "<",
        "<<",
        "<<<",
        "<>",
        "2>",
        "2>>",
        "&>",
    }
)


def _shell_operator(cmd: str) -> str | None:
    """The first bare shell-operator token in *cmd*, or `None`.

    Only an operator standing as *its own token* counts — the spaced form a
    shell would honour (`… | …`, `… > out`). A glued `a>b` stays one token and
    is left alone, as is any operator inside quotes. Split failures (unbalanced
    quotes) defer to the exec path, which surfaces them.
    """
    try:
        # posix=False on Windows: keep backslash paths intact (they'd otherwise
        # be eaten), so a real path token never looks like an operator.
        tokens = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return None
    return next((t for t in tokens if t in _SHELL_OPERATORS), None)


# A modern bash where PATH alone might miss it (a GUI/cron launch, or Windows,
# where git's bash is not on PATH). Checked before `shutil.which("bash")`.
_BASH_HINTS = (
    "/opt/homebrew/bin/bash",  # macOS Apple Silicon Homebrew
    "/usr/local/bin/bash",  # macOS Intel Homebrew
    r"C:\Program Files\Git\bin\bash.exe",  # Windows git bash
    r"C:\Program Files\Git\usr\bin\bash.exe",
)


def _find_exe(name: str, hints: tuple[str, ...] = ()) -> str | None:
    """A concrete path for *name*: a known *hints* location first, then PATH."""
    for hint in hints:
        if os.path.isfile(hint):
            return hint
    return shutil.which(name)


def _resolve_shell(kind: bool | str, policy: str = "posix") -> list[str]:
    """The interpreter argv prefix — `[executable, run-a-string-flag]` — for a
    `run(shell=…)` request.

    `True` follows *policy* (POSIX-everywhere by default: bash, then plain sh,
    with git bash on Windows). A string is a concrete shell (`bash`/`zsh`/`sh`/
    `fish`/`nu`/`pwsh`/`cmd`) or a strategy (`posix`/`native`). Raises a taught
    `ValueError` when the shell can't be found or does not fit the platform —
    never a silent wrong-shell.
    """
    strat = policy if kind is True else str(kind)
    win = sys.platform == "win32"
    if strat == "posix":
        # bash first (pipefail + POSIX word-splitting, and everywhere incl. git
        # bash on Windows), then plain sh. zsh is excluded — its default word
        # splitting is not POSIX, so ask for it by name if you want it.
        exe = _find_exe("bash", _BASH_HINTS) or _find_exe("sh")
        if exe is None:
            raise ValueError(
                "shell=True needs a POSIX shell and none was found. Install one "
                "(git bash on Windows), or use shell='pwsh' / shell='cmd'."
            )
        return [exe, "-c"]
    if strat == "native":
        return (
            [os.environ.get("COMSPEC", "cmd.exe"), "/c"] if win else ["/bin/sh", "-c"]
        )
    if strat == "cmd":
        if not win:
            raise ValueError("shell='cmd' is Windows-only; use 'bash' or 'pwsh'.")
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
    if strat in ("bash", "sh", "zsh", "fish", "nu"):
        exe = _find_exe(strat, _BASH_HINTS if strat == "bash" else ())
        if exe is None:
            raise ValueError(f"shell={strat!r}: {strat!r} was not found on PATH.")
        return [exe, "-c"]
    if strat in ("pwsh", "powershell"):
        exe = _find_exe(strat)
        if exe is None:
            raise ValueError(f"shell={strat!r}: {strat!r} was not found on PATH.")
        return [exe, "-Command"]  # pwsh's own run-a-string flag (accepts -c too)
    raise ValueError(
        f"shell={kind!r} is not a known shell. Use True (the policy), a strategy "
        f"('posix' / 'native'), or a shell name "
        f"('bash', 'zsh', 'sh', 'fish', 'nu', 'pwsh', 'cmd')."
    )


# `clean=True`: run the interpreter without the user's startup files, so a task's
# shell behaves the same on every machine. For `-c` most POSIX shells already
# skip their rc, but pwsh/cmd load a profile and bash honours $BASH_ENV — so it
# is both these flags and (POSIX) dropping BASH_ENV/ENV from the child env.
_CLEAN_FLAGS = {
    "bash": ("--norc", "--noprofile"),
    "zsh": ("-f",),
    "fish": ("--no-config",),
    "nu": ("-n",),
    "pwsh": ("-NoProfile",),
    "powershell": ("-NoProfile",),
    "cmd": ("/d",),
}

# `strict=True`: fail on the first error and on a failing pipe stage. Well-defined
# only for POSIX shells and PowerShell — bash/zsh get pipefail, plain sh cannot
# (dash has none) so it degrades to errexit-only with a one-time note; fish/nu/
# cmd have no errexit at all, so strict there is a taught error, not a silent no-op.
_STRICT_PROLOGUE = {
    "bash": "set -eo pipefail\n",
    "zsh": "set -eo pipefail\n",
    "sh": "set -e\n",
    "pwsh": (
        "$ErrorActionPreference = 'Stop'\n"
        "$PSNativeCommandUseErrorActionPreference = $true\n"
    ),
    "powershell": "$ErrorActionPreference = 'Stop'\n",
}

_strict_sh_noted = False


def _shell_kind_of(exe: str) -> str:
    """The shell family from its executable path — `/usr/bin/bash` → `bash`."""
    return os.path.basename(exe).lower().removesuffix(".exe")


def _shell_prep(
    kind: str, script: str, *, strict: bool, clean: bool
) -> tuple[list[str], str]:
    """Interpreter flags (from *clean*) and the script (from *strict*) for a shell
    run. Raises a taught error when *strict* can't be honoured (fish/nu/cmd have
    no errexit/pipefail)."""
    flags = list(_CLEAN_FLAGS.get(kind, ())) if clean else []
    if strict:
        prologue = _STRICT_PROLOGUE.get(kind)
        if prologue is None:
            raise ValueError(
                f"strict=True is not supported for the {kind!r} shell — it has no "
                f"errexit/pipefail. Use bash, zsh, sh, or pwsh, or drop strict."
            )
        if kind == "sh":
            global _strict_sh_noted
            if not _strict_sh_noted:
                _strict_sh_noted = True
                real_stderr().write(
                    "note: strict under sh has no pipefail; using `set -e` only "
                    "(install bash for errexit + pipefail).\n"
                )
        script = prologue + script
    return flags, script


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
    shell: bool | str = False,
    strict: bool = False,
    clean: bool = False,
    _show: Invocation | None = None,
) -> Result:
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
        result = Result(0, command=label, raw=raw)
        ctx.steps.append(result)
        if not ctx.quiet:
            out.write(f"$ {shown if color else shown_plain}\n")
        return result

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
        code, out_s, err_s = _run_callable(cmd, args, capture=capture, env=env, cwd=cwd)
    else:
        argv: list[str] | str
        shell_kind = ""
        if shell:
            # An explicit shell: run the whole string through the resolved
            # interpreter — `[bash, -c, "<cmd>"]` — so pipes/redirects/globs
            # work. A list is the shell-free form; it can't be a shell script.
            if not isinstance(cmd, str):
                raise ValueError(
                    "run(shell=…) runs a command *string* through a shell; pass a "
                    "str, not a list (a list is the shell-free form)."
                )
            exe, run_flag = _resolve_shell(shell, ctx.shell_default or "posix")
            shell_kind = _shell_kind_of(exe)
            clean_flags, script = _shell_prep(
                shell_kind, cmd, strict=strict, clean=clean
            )
            argv = [exe, *clean_flags, run_flag, script]
        elif isinstance(cmd, str):
            if (op := _shell_operator(cmd)) is not None:
                raise ValueError(
                    f"run({cmd!r}): {op!r} is a shell operator, but run() does not "
                    f"use a shell, so it would be passed as a literal argument (the "
                    f"pipeline/redirect would silently not happen). Ask for a shell "
                    f"— run(..., shell=True) or shell='bash' — split into separate "
                    f"run() steps, or pass a list to use {op!r} as a literal argument."
                )
            # POSIX shells split on shlex rules; Windows command lines are a
            # single string (CreateProcess) and shlex would mangle backslash
            # paths — hand the string straight to subprocess there.
            argv = cmd if sys.platform == "win32" else shlex.split(cmd)
        else:
            argv = [str(a) for a in cmd]
        run_env = {**os.environ, **ctx.env, **(env or {})}
        # A clean POSIX shell means no startup files: `--norc`/`--noprofile`
        # cover interactive/login rc, but bash/sh also source $BASH_ENV/$ENV for
        # a non-interactive `-c`, so drop those from the child env too.
        if clean and shell_kind in ("bash", "sh", "zsh"):
            run_env = {k: v for k, v in run_env.items() if k not in ("BASH_ENV", "ENV")}
        cwd_path = Path(cwd) if cwd is not None else ctx.cwd
        code, out_s, err_s = _run_subprocess(
            argv,
            run_env,
            cwd_path,
            capture,
            encoding,
            killable=not ctx.atomic,
            # An interactive task owns the real terminal: keep its child in
            # footman's group so it keeps its controlling tty (and its Ctrl-C).
            isolate=not ctx.atomic and not ctx.interactive,
            # Tag the child with its task's policy: a fail-fast failure elsewhere
            # reaps this tree only if the task is fail-fast, not keep-going.
            keep_going=ctx.keep_going,
        )
    duration = time.perf_counter() - start
    result = Result(
        code, command=label, stdout=out_s, stderr=err_s, duration=duration, raw=raw
    )
    ctx.steps.append(result)

    if show:
        ok = code == 0
        prefix = "\r\033[K" if ctx.tty and live else ""
        out.write(f"{prefix}{_step_line(ctx, ok, label, duration)}")
        # Join the two streams only to *display* them (stdout then stderr);
        # nothing merged is stored — the Result keeps them apart.
        combined = out_s + err_s
        if capture and combined and (not ok or ctx.verbose):
            out.write(combined if combined.endswith("\n") else combined + "\n")
        out.flush()

    if code != 0 and not nofail:
        raise RunFailed(result)
    return result


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
        # One buffer for both streams at task level, so the atomic flush keeps
        # this child's stdout/stderr in order; a run() inside it still splits the
        # step's streams via a temporary swap.
        buf = io.StringIO()
        child = replace(
            parent, sink=buf, err_sink=buf, steps=[], task=name, name_width=width
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
                error = RunFailed(Result(code, command=thunk, raw=thunk))
        except RunFailed as exc:
            code, error = exc.result.code or 1, exc
        except SystemExit as exc:
            # `sys.exit()` / `raise SystemExit(...)` is a common "fail this task"
            # idiom, but a BaseException — without this it escapes the pool
            # instead of being collected. Treat its code like a returned one:
            # 0 succeeds, non-zero is a synthesized failure the gate below raises.
            code = _exit_code(exc)
            error = None
            if code != 0:
                thunk = _label(call, ())
                error = RunFailed(Result(code, command=thunk, raw=thunk))
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
