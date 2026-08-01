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
from collections.abc import Callable, Iterable, Iterator, Sequence, Sized
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    NamedTuple,
    NoReturn,
    Protocol,
    TextIO,
    TypeAlias,
    TypeVar,
    overload,
)

from footman import _globals

if TYPE_CHECKING:
    from footman import _step
    from footman import registry as _registry_t


class AuditEntry(NamedTuple):
    """One entry of a record's audit: a lifecycle moment that acted on the
    verdict — who acted, and what they set.

    The body entry is always present and carries what the work itself
    produced; a review entry names the reviewer, with `code` None when it
    was involved without writing the verdict (a reviewer that only set the
    title). Entries are in execution order.
    """

    moment: str
    """Where in the record's lifecycle this happened: `"body"` for the work
    itself, `"review"` for a `pre_record` reviewer."""
    actor: str
    """Who acted: the command line for the body, the hook's name for a
    reviewer."""
    code: int | None
    """The code this moment left, or None for involvement without a verdict
    write."""


_Moment: TypeAlias = Literal["bind", "enter", "body", "review", "observe"]
"""The lifecycle vocabulary, closed at the PRODUCER side only: the public
fields stay open strings (the `state` precedent — readers tolerate values
they don't know), while footman's own write sites go through
`_audit_entry`, so a misspelt moment in framework code is a type error
before it is a test failure."""


def _audit_entry(moment: _Moment, actor: str, code: int | None) -> AuditEntry:
    """The one door framework code writes audit entries through."""
    return AuditEntry(moment, actor, code)


class Result(int):
    """The outcome of one `run()` call — and the value `run()` returns.

    A `Result` *is* the exit code: it subclasses `int`, so `code = run(...)`,
    `if run(...)`, and `run(...) == 0` all keep working. It also carries the
    captured output, split by stream, and the command that produced it — so
    `run("git rev-parse HEAD").stdout.strip()` reads the hash without the
    stderr noise glued on. `stdout`/`stderr` are separated for both subprocess
    and in-process runs; a streamed run (`capture=False`) leaves them empty.
    """

    _command: str
    _stdout: str
    _stderr: str
    _duration: float
    _raw: str
    _timed_out: bool
    _address: str
    _audit: tuple[AuditEntry, ...]

    def __new__(
        cls,
        code: int,
        *,
        command: str = "",
        stdout: str = "",
        stderr: str = "",
        duration: float = 0.0,
        raw: str = "",
        timed_out: bool = False,
        address: str = "",
        audit: tuple[AuditEntry, ...] = (),
    ) -> Result:
        self = super().__new__(cls, code)
        object.__setattr__(self, "_timed_out", timed_out)
        object.__setattr__(self, "_address", address)
        object.__setattr__(self, "_command", command)
        object.__setattr__(self, "_stdout", stdout)
        object.__setattr__(self, "_stderr", stderr)
        object.__setattr__(self, "_duration", duration)
        object.__setattr__(self, "_raw", raw or command)
        object.__setattr__(self, "_audit", audit)
        return self

    @property
    def command(self) -> str:
        """The command line that ran, normalised for reading — options in
        separated form, values shell-quoted. What `recording()` asserts
        against, and what the terminal shows."""
        return self._command

    @property
    def stdout(self) -> str:
        """Captured standard output; empty when the step streamed instead."""
        return self._stdout

    @property
    def stderr(self) -> str:
        """Captured standard error; empty when the step streamed instead."""
        return self._stderr

    @property
    def duration(self) -> float:
        """Wall-clock seconds the step took."""
        return self._duration

    @property
    def raw(self) -> str:
        """The exact command line executed, shell-quoted — the bytes footman
        handed the tool, which may spell an option `--flag=value` where
        `command` shows `--flag value`. What `--verbose` prints. Equal to
        `command` when there is nothing to normalise."""
        return self._raw

    @property
    def timed_out(self) -> bool:
        """Whether `timeout=` expired and footman killed the tree. The code
        is 124 — the shell convention — and `stdout`/`stderr` hold whatever
        the command managed to say first, which on a hang is the only clue
        there is."""
        return self._timed_out

    @property
    def address(self) -> str:
        """The record's tree-derived name: the requester's path, this
        record's label, and an ordinal once a label repeats among siblings —
        counted in request order as written, so it is deterministic across
        runs and hosts. Empty outside a managed context."""
        return self._address

    @property
    def audit(self) -> tuple[AuditEntry, ...]:
        """The verdict's provenance: every lifecycle moment that acted on
        it, in execution order — the body entry with what the work itself
        produced, a review entry per `pre_record` reviewer. Empty for a
        record that executed nothing (a dry-run plan line)."""
        return self._audit

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Result.{name} is sealed — a committed record is immutable. "
            f"Amend verdicts in the review window (pre_record), or veto "
            f"with fail(reason, code) from an observer."
        )

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

    @property
    def failed_at(self) -> str | None:
        """The lifecycle moment the failure came from, None on success — a
        derived reading of the audit: the last moment that wrote the verdict.
        A red tool reviewed green reads None (the record succeeded); a green
        tool failed by its reviewer reads `"review"`."""
        return _failed_moment(self.code, self.audit)

    @property
    def work_code(self) -> int | None:
        """The code the record carried when the failing moment began — a
        derived reading of the audit, None on success or when nothing came
        before the failure. A green build failed in review keeps its 0 here,
        visible rather than inferred."""
        return _earned_code(self.code, self.audit)


def _failed_moment(code: int, audit: Sequence[AuditEntry]) -> str | None:
    """The moment the final non-zero verdict came from — the derivation both
    record shapes (step `Result`, task row) share."""
    if code == 0 or not audit:
        return None
    for entry in reversed(audit):
        if entry.code is not None:
            return entry.moment
    return None


def _earned_code(code: int, audit: Sequence[AuditEntry]) -> int | None:
    """The code carried when the failing moment began; None on success or
    when nothing code-bearing came before the failure."""
    if _failed_moment(code, audit) is None:
        return None
    coded = [entry for entry in audit if entry.code is not None]
    return coded[-2].code if len(coded) >= 2 else None


class ResultView:
    """A step's record, in the review window: the draft a `pre_record`
    reviewer receives after the work ran and before the record is sealed.

    The verdict is still open — `title` and `code` are plain writable
    attributes, and the raise-on-nonzero decision reads the code the review
    leaves behind, so "fail by this tool's definition of failure" needs no
    `nofail=` at the call site. Everything the run *captured* is read-only:
    review sees what the run kept, never edits it — an uncaptured
    (`capture=False`) call reviews the code alone. `ok` derives from `code`,
    so the two can never disagree.
    """

    __slots__ = (
        "_code",
        "_command",
        "_duration",
        "_raw",
        "_returned",
        "_stderr",
        "_stdout",
        "_title",
        "_touched",
    )

    def __init__(
        self,
        *,
        title: str,
        code: int,
        stdout: str,
        stderr: str,
        duration: float,
        raw: str,
        command: str,
        returned: Any = None,
    ) -> None:
        self._title = title
        self._code = code
        self._stdout = stdout
        self._stderr = stderr
        self._duration = duration
        self._raw = raw
        self._command = command
        self._returned = returned
        self._touched: set[str] = set()

    @property
    def title(self) -> str:
        """The receipt's label. Starts as the shown command (or the call's
        `title=`); what the review leaves here is what the report shows."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value
        self._touched.add("title")

    @property
    def code(self) -> int:
        """The verdict. What the review leaves here is the step's exit code —
        the raise decision and the receipt both read it. The audit records
        whether a reviewer wrote it or left it alone."""
        return self._code

    @code.setter
    def code(self, value: int) -> None:
        self._code = value
        self._touched.add("code")

    @property
    def ok(self) -> bool:
        """Whether the code, as it stands, is success — derives from `code`."""
        return self.code == 0

    @property
    def stdout(self) -> str:
        """Captured standard output; empty when the call streamed instead."""
        return self._stdout

    @property
    def stderr(self) -> str:
        """Captured standard error; empty when the call streamed instead."""
        return self._stderr

    @property
    def output(self) -> str:
        """`stdout` then `stderr`, concatenated — the same convenience the
        sealed `Result` offers."""
        return self._stdout + self._stderr

    @property
    def duration(self) -> float:
        """Wall-clock seconds the work took — machinery-owned, read-only."""
        return self._duration

    @property
    def raw(self) -> str:
        """The exact command line executed — the review can read what really
        ran while retitling what the report shows."""
        return self._raw

    @property
    def command(self) -> str:
        """The normalised command line as it stood before review."""
        return self._command

    @property
    def returned(self) -> Any:
        """What the body returned, when this draft is a task's row — None by
        circumstance elsewhere (a subprocess step has no return value)."""
        return self._returned

    def set_returned(self, value: Any) -> None:
        """Rewrite the *reported* value — the summary line and the `--json`
        envelope — never what a dependent or a body caller received: that was
        snapshotted the moment the body handed it over. The review window is
        this write's home: reviewed, attributed in the audit, before the
        record seals."""
        self._returned = value
        self._touched.add("returned")

    def _fill(self, stdout: str, stderr: str, duration: float) -> None:
        """Machinery-side: fill the captured streams and timing once the
        work concluded — a generator item held its draft *during* the work,
        before these were knowable. Never a review write."""
        self._stdout = stdout
        self._stderr = stderr
        self._duration = duration


@dataclass
class Context:
    """State for one running task: environment, flags, passthrough, output."""

    address: str = ""
    """This context's place in the run's tree — the path of requests that
    led here. Every record made under it derives its own address from this
    one."""
    _labels: dict[str, int] = field(default_factory=dict)
    """Per-parent label counts, so same-labelled children get ordinals in
    request order as written (the first is bare, the second is `#2`)."""

    env: dict[str, str] = field(default_factory=lambda: _globals.base_env())
    """**This task's environment** — a complete one, not a diff.

    Starts as a copy of the run's pinned environment, and is what `os.environ`
    answers from inside the task, what every child footman spawns receives,
    and what `run(env=…)` replaces. Because it is a whole value rather than an
    overlay, `del os.environ["FOO"]` is ordinary: it removes the key from this
    task's environment and from the children it goes on to spawn, while a
    sibling's copy is untouched."""
    cwd: Path | None = None
    """Where `run()` executes — resolved once per task by the policy ladder
    (`.opts(cwd=)` / `@task(cwd=)` / config `cwd`, default `taskfile`), so it
    is concrete inside a run. A preset value (tests, `use_context`) wins.
    `None` means unresolved (plain calls outside a footman run)."""
    cwd_policy: str = ""
    """`[tool.footman] cwd` — the run-wide default policy token (or absolute
    path) the ladder bottoms out on. Empty means `taskfile`."""
    root_dir: str = ""
    """The `root` token's target: the highest cascade task file's directory
    (`files[0].parent`), pinned by discovery. Empty outside a real run."""
    invoked_dir: str = ""
    """The `asinvoked` token's target: the process cwd where `fm` was
    launched, pinned as a snapshot at startup."""
    cwd_unmanaged: bool = False
    """`cwd="unmanaged"`: footman stays out — subprocesses spawn with
    `cwd=None` (inherit the live process cwd) even though `ctx.cwd` records
    the process cwd at task start for the body to read."""
    serial_active: bool = False
    """This task holds (or inherited, through a fan-out) the serial or
    exclusive lane: it owns the real process globals — the environ router,
    the Popen injection, and the os guards all pass through. Set by the
    executor around a `serial=`/`exclusive=` body; `parallel()` children
    inherit it, because a lineage extends a hold."""
    dry_run: bool = False
    """`--dry-run`: `run()` prints and records the command, executes
    nothing, and reports success."""
    quiet: bool = False
    """`--quiet`: suppress step lines and the per-task summary."""
    verbose: bool = False
    """`--verbose`: replay captured `run()` output even on success."""
    no_color: bool = False
    """`--no-color` / `--color=never` (or `NO_COLOR`): never emit ANSI styling."""
    force_color: bool = False
    """`--color=always` (or `FORCE_COLOR`): colour even when output is not a
    terminal — so `run()` forces the tools it spawns to colour and the shown
    command line paints, for a pipe into `less -R`. Gated off under capture
    (`--json`), where ANSI would corrupt the envelope. Never sets the live
    cursor affordances `tty` governs — those still need a real terminal."""
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
    shared: bool = True
    """Whether an execution the run has already performed may satisfy this
    request. `False` means it gets its own run, and so does everything it asks
    for, unless that task declares its own answer. Resolved per node by the
    scheduler and per call by the cell layer — the sharing twin of
    `keep_going`'s per-subtree policy."""
    in_task: bool = False
    """True while a task *body* runs (the scheduler sets it around the call),
    so the interactive primitives tell a guarded mid-body call from the
    framework's own up-front `ask()` resolution."""
    unit_pending: bool = False
    """A live-progress unit is already counted for this call, and unclaimed:
    `parallel()` counts every child it is handed, then the first task request
    inside that child *claims* the unit instead of counting a second one. A
    thunk that runs no task keeps it; a thunk that is only a wrapper around
    one call (the `lambda: build("web")` spelling) shows as the single piece
    of work it is. Cleared on claim, so the requests after it — and anything
    the callee itself asks for — count their own."""
    steps: list[Result] = field(default_factory=list)
    """Every `run()` this task made, in order — what `recording()` and
    the `--json` envelope read."""


_current: ContextVar[Context | None] = ContextVar("footman_context", default=None)


def current() -> Context:
    """The context of the running task (a fresh default one outside a run)."""
    ctx = _current.get()
    return ctx if ctx is not None else Context()


@contextlib.contextmanager
def chdir(
    target: str | Path | None = None, *, rel: str | Path | None = None
) -> Iterator[None]:
    """Really change the process directory — inside a serial/exclusive task.

    The sugar for bodies that own the globals: the default target is the
    task's own `ctx.cwd`; arguments follow the marker grammar exactly — a
    policy token (`root`, `taskfile`, `asinvoked`) or an absolute path, with
    `rel=` for a relative suffix; a bare relative path is the same taught
    error the markers give. Performs a real `os.chdir`, keeps `ctx.cwd` in
    sync (so a nested `run()` roots where the block does), restores both on
    exit. In a parallel task it is a taught error — the cwd belongs to no
    one there.
    """
    ctx = current()
    if ctx.in_task and not ctx.serial_active:
        raise RuntimeError(
            f"task {ctx.task or '?'} calls footman.chdir() in a parallel "
            f"task — the process directory belongs to no one there. Mark "
            f"the task serial (or exclusive), or build paths from "
            f"footman.cwd()."
        )
    base: Path
    if target is None:
        base = ctx.cwd if ctx.cwd is not None else Path(_globals.real_getcwd())
    elif isinstance(target, str) and target == "root" and ctx.root_dir:
        base = Path(ctx.root_dir)
    elif isinstance(target, str) and target == "asinvoked" and ctx.invoked_dir:
        base = Path(ctx.invoked_dir)
    elif isinstance(target, str) and target == "taskfile":
        from footman._discover import defining_dir

        home = defining_dir(ctx.fn) if ctx.fn is not None else None
        base = Path(home) if home else Path(_globals.real_getcwd())
    elif isinstance(target, str) and target == "unmanaged":
        raise TypeError("chdir(cwd='unmanaged') has no directory to change to")
    else:
        base = Path(target)
        if not base.is_absolute():
            raise TypeError(
                f"chdir({str(target)!r}) is relative — chdir takes a policy "
                f"token or an absolute path; a relative suffix goes in rel=…"
            )
    if rel is not None:
        rel_suffix = Path(rel)
        if rel_suffix.is_absolute() or rel_suffix.anchor:
            raise TypeError(
                f"rel={str(rel)!r} is absolute — rel is a suffix appended "
                f"to the resolved base; an absolute directory goes first."
            )
        base = base / rel
    saved_fs, saved_ctx = _globals.real_getcwd(), ctx.cwd
    _globals.real_chdir(base)
    ctx.cwd = base
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            _globals.real_chdir(saved_fs)
        ctx.cwd = saved_ctx


def cwd() -> Path:
    """The current task's working directory, always concrete.

    `ctx.cwd` as the policy ladder resolved it — the blessed base for a task
    body's own path arithmetic (`footman.cwd() / "dist"`), instead of
    relative paths against the process cwd, which belongs to no one in a
    parallel run. Outside a run it is simply the process cwd.
    """
    resolved = current().cwd
    return resolved if resolved is not None else Path.cwd()


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


def track(iterable: Iterable[_T], total: int | None = None) -> Iterator[_T]:
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
        total = len(iterable) if isinstance(iterable, Sized) else 0
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


def inherited() -> Callable[..., Any]:
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
    from footman import _discover

    fn = current().fn
    if fn is None:
        raise RuntimeError(
            "inherited() works inside a running task — footman resolves the "
            "task being shadowed from the one currently running"
        )
    previous = _discover.shadowed(fn)
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
        from footman import registry

        ctx = current()
        saved = ctx.fn
        ctx.fn = previous
        try:
            # The shadowed *body*, run inside this task: an override chain, the
            # way `super()` is — not a second task. It shares this task's
            # context, result, and reported duration, and never becomes a unit
            # of the run in its own right.
            return registry.task_body(previous)(*args, **kwargs)
        finally:
            ctx.fn = saved

    return call_inherited


class RunFailed(Exception):
    """A `run()` command exited non-zero (and `nofail` was not set)."""

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(f"`{result.command}` exited with code {result.code}")


class RunTimeout(RunFailed):
    """A `run(timeout=…)` expired and the process tree was killed.

    A `RunFailed`, so every `except RunFailed` keeps catching it; catch this
    to tell a hang from an ordinary non-zero exit — the distinction a version
    or help probe branches on."""

    def __init__(self, result: Result, timeout: float) -> None:
        self.result = result
        self.timeout = timeout
        Exception.__init__(
            self, f"`{result.command}` timed out after {timeout:g}s and was killed"
        )


class Failed(Exception):
    """A task chose to fail — the exception `footman.fail()` raises.

    A *deliberate* stop with a reason (and optional exit code): the user-facing
    sibling of `RunFailed` (which is a *command*'s failure). Carries `.reason`
    and `.code`; footman renders the reason verbatim — no type prefix — in the
    failure line and the `--json` `error` field. Exported so a task can
    `except footman.Failed:`, but `fail()` is the blessed way to raise it.
    """

    def __init__(self, reason: str = "", *, code: int = 1) -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


def fail(reason: str = "", *, code: int = 1) -> NoReturn:
    """Fail the current task with a *reason* (and exit *code*, default 1).

    The blessed way to stop a task deliberately: `fail("no open PR to act on")`,
    or `fail("reserved branch", code=3)` to pick the exit code too. A *function*,
    not a `raise`, on purpose — a task lives in your repo under your linter, and
    `raise SomeError("a literal")` trips flake8-errmsg (EM101) and tryceratops
    (TRY003) at the call site, every failure. A call trips neither, the same
    reason `sys.exit()` and `pytest.fail()` are functions. `return N` still
    spells a bare code; `sys.exit(...)` still works — `fail()` is just the
    footman-native one, with a reason and a code together.

    `fail()` means failure, so `code=0` — success — is a contradiction it
    refuses rather than a corner it interprets: `return 0` (or a plain
    `return`) spells stopping early with success.
    """
    if code == 0:
        raise ValueError(
            "fail() means failure and exit code 0 is success — return 0 "
            "(or just return) spells stopping early with success"
        )
    raise Failed(reason, code=code)


def _is_deliberate_stop(err: BaseException) -> bool:
    """Whether *err* is a chosen stop with a message (`fail()`/`sys.exit("…")`)
    rather than a crash — so its reason renders verbatim, no type prefix."""
    return isinstance(err, (SystemExit, Failed))


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


class Status(Protocol):
    """What context asks of a live status line — the duck-typed face of
    `_progress.StatusLine`. context stays ignorant of _progress on purpose;
    this Protocol is the contract, satisfied structurally."""

    counted: dict[str, tuple[int, int]]

    def unit_added(self, count: int = 1) -> None: ...
    def unit_started(self, name: str) -> None: ...
    def unit_counted(self, name: str, done: int, total: int) -> None: ...
    def unit_finished(self, name: str, ok: bool) -> None: ...
    def unit_skipped(self, name: str) -> None: ...
    def notify(self, s: str) -> None: ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...


# The run's live status line, registered by the scheduler for the duration
# of a run; outside a run there is none.
_status: Status | None = None

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


def set_status(status: Status | None) -> None:
    global _status
    _status = status


def active_status() -> Status | None:
    """The run's live status line, or `None` outside a run."""
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


def real_stdin() -> TextIO:
    """The underlying stdin, bypassing the guard proxy.

    The framework's own prompts read here — they already write through
    `real_stderr()` — because the guard exists for *task-body* reads, and the
    managed window now opens before binding, where `ask()` legitimately asks.
    """
    real = getattr(sys.stdin, "_real", None)
    return real if real is not None else sys.stdin


_T = TypeVar("_T")
_V = TypeVar("_V")

_UNSET: Any = object()  # "no default given" — None is a valid default/value

_prompt_lock = threading.Lock()

# --- the boundary's one read of stdin ----------------------------------------
#
# stdin is the fourth process global: task bodies never read it (the guard in
# _globals refuses), and parameters marked `stdin` are filled here, at the
# boundary. The stream is read once, fully, into memory, and the same payload
# serves every parameter in the run that asks — stdin is consumable, so two
# tasks in a chain could never each read it. `None` means "not provided": a
# terminal, a missing stream, or an embedded `Runner.invoke` that injected
# nothing. The read is lazy — only a bind that meets a `stdin` parameter
# calls `stdin_payload()`, so a run without one never touches the stream.

_STDIN_UNREAD: Any = object()
_stdin_payload: Any = _STDIN_UNREAD


def stdin_payload() -> bytes | None:
    """The process's piped stdin as bytes, read once and cached; `None` when
    stdin is a terminal (interactive input is `ask()`'s job, and a pipe
    target must never block on a terminal read)."""
    global _stdin_payload
    if _stdin_payload is _STDIN_UNREAD:
        if _stdin_is_tty() or sys.stdin is None:
            _stdin_payload = None
        else:
            try:
                # `.buffer` reaches the raw stream through the guard proxy —
                # this is the boundary, exactly who may read.
                _stdin_payload = sys.stdin.buffer.read()
            except (AttributeError, OSError, ValueError):
                # No byte buffer (an embedded/captured stream) or a stream
                # that refuses to read: nothing was provided.
                _stdin_payload = None
    payload: bytes | None = _stdin_payload  # the sentinel is gone by here
    return payload


def _inject_stdin(payload: bytes | None) -> Any:
    """Set the boundary payload directly (the testing seam) and return the
    previous cache state for `_restore_stdin_payload`. `Runner.invoke` always
    injects — under a test runner the real stream is the harness's, never the
    test's — so `stdin=None` means "a terminal", not "read the harness"."""
    global _stdin_payload
    previous = _stdin_payload
    _stdin_payload = payload
    return previous


def _restore_stdin_payload(previous: Any) -> None:
    global _stdin_payload
    _stdin_payload = previous


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
        closed = (
            "stdin closed mid-prompt (Ctrl-D, or piped input ran out) — "
            "nothing left to ask. Pass the value as a flag, or rerun with "
            "a terminal attached."
        )
        if secret:
            import getpass

            try:
                value = getpass.getpass(message, stream=err).rstrip("\n")
            except EOFError:
                raise RuntimeError(closed) from None
        else:
            err.write(message)
            err.flush()
            line = real_stdin().readline()
            if line == "":  # EOF: a re-ask loop would spin on it forever
                raise RuntimeError(closed)
            value = line.rstrip("\n")
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

    `secret=True` hides the typing (getpass) *and* returns a `Secret`, so the
    answer redacts in tracebacks and structured output the same way
    `ask(secret=True)` does — hiding a value while it is typed and then
    printing it in the first traceback would be a strange kind of secret.
    A default returned unattended is wrapped too: where the value came from
    doesn't change what it is.
    """
    from footman.params import Secret

    ctx = _guard_interactive("prompt()")
    if ctx.no_input:
        if default is not None:
            return Secret(default) if secret else default
        raise RuntimeError(
            "prompt(): --no-input is set, so nothing can be asked. Pass a "
            "default, or supply the value as a task parameter (a CLI flag)."
        )
    answer = _prompt_core(message, default=default, secret=secret)
    return Secret(answer) if secret else answer


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


@overload
def select(
    message: str,
    options: Sequence[str],
    *,
    multiple: Literal[True],
    default: list[str] = ...,
) -> list[str]: ...
@overload
def select(
    message: str,
    options: Sequence[str],
    *,
    multiple: Literal[False] = False,
    default: str = ...,
) -> str: ...
@overload
def select(
    message: str,
    options: Sequence[str | tuple[str, _V]],
    *,
    multiple: Literal[True],
    default: list[str | _V] = ...,
) -> list[str | _V]: ...
@overload
def select(
    message: str,
    options: Sequence[str | tuple[str, _V]],
    *,
    multiple: Literal[False] = False,
    default: str | _V = ...,
) -> str | _V: ...


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
    return _select_core(
        message, options, multiple=multiple, default=default, no_input=ctx.no_input
    )


def _select_core(
    message: str,
    options: Sequence[Any],
    *,
    multiple: bool = False,
    default: Any = _UNSET,
    no_input: bool = False,
) -> Any:
    """The menu mechanics, unguarded — the framework's `ask()` menus call this
    directly; user code goes through the guarded `select()`. Reads the real
    stream, like every framework prompt."""
    opts = list(options)
    if not opts:
        raise ValueError("select(): no options to choose from")
    labels = [o[0] if isinstance(o, tuple) and len(o) == 2 else str(o) for o in opts]
    values = [o[1] if isinstance(o, tuple) and len(o) == 2 else o for o in opts]
    if no_input or not _stdin_is_tty():
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
        line = real_stdin().readline().strip()
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
def routing() -> Iterator[tuple[TextIO, TextIO]]:
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
            # getattr, not hasattr-then-call: hasattr narrowing is not
            # portable across checkers, the getattr is.
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="replace")
    _router, _err_router = _Router(real_out), _Router(real_err, err=True)
    sys.stdout, sys.stderr = _router, _err_router
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
def _process_state(env: dict[str, str]) -> Iterator[None]:
    """Patch `os.environ` around an in-process callable — the *bare-call
    fallback only*.

    Inside a run the environ router serves reads from the task's overlay
    (`_env_overlay` below: thread-confined, lock-free). Outside a routed run
    (bare calls in scripts/tests) there is no router to lean on, so fall
    back to the classic global patch, guarded by a re-entrant lock and
    restored on exit — exactly the shape of the output router's own
    fallback. The common case (no overlay) is lock-free either way.

    The process **cwd** is never touched: footman does not chdir. A call
    that needs a different directory runs as a subprocess (explicit `cwd=`,
    fully parallel) — `_run_callable` raises the taught error for the
    in-process case.
    """
    if not env:
        yield
        return
    with _state_lock:
        saved_env = os.environ.copy()
        try:
            os.environ.update(env)
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved_env)


@contextlib.contextmanager
def _env_overlay(ctx: Context, overlay: dict[str, str]) -> Iterator[None]:
    """Thread-confined env for an in-process call inside a run: swap
    `ctx.env` for the call's merged overlay — the environ router serves the
    callable's reads from it, and any child it spawns inherits it. No
    process global is touched, so concurrent calls never serialise."""
    saved = ctx.env
    ctx.env = overlay
    try:
        yield
    finally:
        ctx.env = saved


def _run_callable(
    cmd: Callable[..., Any],
    args: tuple[Any, ...],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
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
    unboundedly. The env overlay is applied process-globally via
    `_process_state`; the `capture=False` short-circuit runs *inside* it so
    uncaptured callables keep env too.

    cwd is **checked, never applied**: footman does not chdir in a parallel
    task. Equal target and live cwd (the common single-package case) runs
    untouched; a *foreign* target is a taught error naming the exits —
    exactly the case the old chdir silently serialised the run for.
    """
    ctx = current()
    # Colour is decided once for the whole run and published into os.environ at
    # the run boundary (`color_environment`), so an in-process tool reads it
    # straight from the environment — no per-call patch here, so the lock-free
    # fast path in `_process_state` is kept in every colour mode.
    # Same rule as the subprocess branch: `env=` replaces, absent inherits.
    overlay = dict(env) if env is not None else dict(ctx.env)
    # `cwd` arrives resolved by `_target_cwd`: the task's managed directory
    # or an explicit per-call target. None means footman stays out (the
    # `unmanaged` token, task-level or per-call): no cwd opinion for the
    # callable, exactly as the subprocess branch spawns with cwd=None.
    if cwd is not None:
        live = Path(_globals.real_getcwd())
        if cwd.resolve() != live:
            raise ValueError(
                f"this in-process call needs cwd {cwd} but the process "
                f"is at {live} — footman no longer chdirs in a parallel task. "
                f"Run it as a subprocess (which gets cwd= for free), build "
                f"paths from footman.cwd(), declare @task(cwd='unmanaged'), "
                f"or pass cwd='unmanaged' on this call if it genuinely "
                f"doesn't care."
            )
    state = _env_overlay(ctx, overlay) if _globals.active() else _process_state(overlay)
    with state:
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


def _leaf(text: str) -> str:
    """An address leaf, made parse-safe: the address grammar reserves `/`
    (tree levels) and `#` (ordinals), and leaves are minted from
    user-influenced text — command tokens, titles. Anything outside the
    safe alphabet maps to `-`, runs collapse, leading `.`/`-` strip (so
    `./ship` names `ship`), and a leaf is never empty. Names only: the
    record's `command`/title still show the original verbatim."""
    out = []
    last_dash = False
    for ch in text:
        if ch.isalnum() or ch in "_.-":
            out.append(ch)
            last_dash = ch == "-"
        elif not last_dash:
            out.append("-")
            last_dash = True
    leaf = "".join(out).strip(".-")
    return leaf or "step"


def _next_label(labels: dict[str, int], label: str) -> str:
    """The next same-labelled sibling's spelling: bare first, `#2` after."""
    n = labels.get(label, 0) + 1
    labels[label] = n
    return label if n == 1 else f"{label}#{n}"


def _child_address(parent: Context, label: str) -> str:
    """The tree-derived name of the next child with this label under
    *parent* — deterministic, because a parent's requests are made from its
    own control flow in written order."""
    leaf = _next_label(parent._labels, _leaf(label))
    return f"{parent.address}/{leaf}" if parent.address else leaf


@contextlib.contextmanager
def _captured_streams(out_buf: io.StringIO, err_buf: io.StringIO) -> Iterator[None]:
    """Capture this thread's stdout/stderr into the two buffers — the same
    dual strategy `_run_callable` uses: a thread-confined sink swap under
    the router (parallel-safe), the classic global redirect outside a
    routed run. The step pump drives generator items through this."""
    ctx = current()
    if _router is not None:
        saved_out, saved_err = ctx.sink, ctx.err_sink
        ctx.sink, ctx.err_sink = out_buf, err_buf
        try:
            yield
        finally:
            ctx.sink, ctx.err_sink = saved_out, saved_err
        return
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        yield


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


# How long a terminated child gets to exit before the kill is forced —
# shared by fail-fast and by a timeout, so "ask, then insist" means the
# same interval whichever one asked.
_KILL_GRACE = 2.0


def terminate_live_children(
    grace: float = _KILL_GRACE, *, failfast_only: bool = False
) -> None:
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
    timeout: float | None = None,
    no_window: bool = False,
) -> tuple[int, str, str, bool]:
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
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if isolate else 0
        # A *captured* read has no business owning a console. Without this,
        # Windows Terminal hands each spawn a visible window, and a tool that
        # interrogates the terminal at start-up blocks forever waiting for a
        # reply no pipe will carry — so whether a read hung depended on which
        # window the run was launched from. CREATE_NO_WINDOW gives it a fresh
        # *hidden* console, which is determinism rather than a cure: a
        # determined interrogator still queries the hidden one. Deliberately
        # not DETACHED_PROCESS, which leaves console-hosted runtimes with no
        # console at all — pwsh dies at start-up, git-bash goes mute.
        #
        # Streaming and interactive runs are exempt: they mean to reach the
        # caller's terminal, which is the thing this takes away.
        if no_window:
            flags |= subprocess.CREATE_NO_WINDOW
        if flags:
            group["creationflags"] = flags
    elif isolate:
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
    timed_out = False
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The whole tree, not just the child: a hung tool's own workers
            # would otherwise outlive the call that bounded it. Same escalation
            # fail-fast uses — ask, then insist — and `atomic` does not spare
            # it, because a timeout is this call's own declared bound rather
            # than a sibling's failure reaching across.
            timed_out = True
            _kill_tree(proc, force=False)
            try:
                out, err = proc.communicate(timeout=_KILL_GRACE)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, force=True)
                out, err = proc.communicate()
    finally:
        if killable:
            _forget_child(proc)
    if not capture:
        return proc.returncode, "", "", timed_out
    # Whatever it managed to say before the kill: on a hang that partial
    # output is usually the only diagnostic there is.
    return proc.returncode, out or "", err or "", timed_out


def _dim(text: str, color: bool) -> str:
    return f"\033[2m{text}\033[0m" if color else text


def _colored(ctx: Context) -> bool:
    """The one colour predicate: does footman dress this run's output — its own
    chrome and the tools it spawns — for colour?

    `never` (`no_color`) always wins; `always` (`force_color`) forces colour on
    even off a terminal (a pipe into `less -R`); otherwise `auto` follows the
    run's tty-ness (`NO_COLOR` in the environment still bows out)."""
    if ctx.no_color:
        return False
    if ctx.force_color:
        return True
    return ctx.tty and "NO_COLOR" not in os.environ


def color_on() -> bool:
    """Whether the current run emits colour — the tools bridge asks this to
    decide whether to force a tool's own `--color` flag (for the few that
    ignore the environment). A plain call outside a run reads a default
    (monochrome) context, so a bare `tools.git.diff()` in a script adds
    nothing."""
    return _colored(current())


# Every colour variable footman speaks. `color_environment` clears the whole set
# before setting a direction, so forcing colour *off* is the *absence* of
# FORCE_COLOR, not `FORCE_COLOR=0` — some tools (ruff) read the mere presence of
# FORCE_COLOR as "force on", ignoring `NO_COLOR`, so `"0"` would fail to silence
# them. footman emits by presence/absence; it consumes `NO_COLOR` by presence and
# `FORCE_COLOR` by truthiness (`_resolve_color`).
_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR")


def color_env(on: bool) -> dict[str, str]:
    """The colour variables to *set* to force colour on (or off) for a child.

    Without a PTY a child's stdout is a pipe, so `isatty()` is false and a
    well-behaved tool auto-disables colour — footman captures the bytes and
    replays them onto its own terminal, so it wants the colour back. `on` sets
    `FORCE_COLOR`/`CLICOLOR_FORCE`; off sets only `NO_COLOR`. Forcing off is
    completed by *removing* any inherited `FORCE_COLOR` (see `_COLOR_VARS` /
    `color_environment`), never by setting it to `"0"`."""
    if on:
        return {"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "CLICOLOR": "1"}
    return {"NO_COLOR": "1"}


def run_colour_on(
    *, no_color: bool, force_color: bool, capture: bool, isatty: bool
) -> bool:
    """The run-wide colour decision, computed once from its run-wide inputs.

    Colour cannot change during a run, so this is `_colored` for every task at
    once — letting the environment be set once at the run boundary rather than
    per call. Capture (a `--json` run) is byte-clean and gates `force_color` off,
    exactly as the scheduler does per task; the rest defers to `_colored`."""
    if capture:
        return False
    tty = isatty and os.environ.get("TERM") != "dumb"
    return _colored(Context(no_color=no_color, force_color=force_color, tty=tty))


@contextlib.contextmanager
def color_environment(on: bool) -> Iterator[None]:
    """Publish the run-wide colour decision into `os.environ` for the run.

    One decision, set once: a subprocess inherits it, an in-process tool reads
    it — so no `run()` call has to patch the environment itself (and none takes
    the `_process_state` lock for colour). Every colour variable is cleared
    first, then this direction's are set — so *off* leaves no `FORCE_COLOR` for a
    presence-checking tool to honour, and *on* leaves no stray `NO_COLOR`.
    Restored on exit; nested runs stack, each restoring the value it found."""
    saved = {key: os.environ.get(key) for key in _COLOR_VARS}
    for key in _COLOR_VARS:
        os.environ.pop(key, None)
    os.environ.update(color_env(on))
    try:
        yield
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


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


def _target_cwd(
    ctx: Context, cwd: str | Path | None, rel: str | Path | None
) -> Path | None:
    """The effective directory for one call. Explicit `cwd=` wins; otherwise
    the task's resolved `ctx.cwd` (or none at all under the `unmanaged`
    policy). The literal `cwd="unmanaged"` opts this one call out — the
    same word, the same meaning as the task-level token: no base at all, so
    a child inherits the live process cwd and an in-process callable runs
    under it. `rel=` is a relative suffix on whatever base is in force at
    this call — `ctx.cwd` in the common case."""
    if cwd == "unmanaged":
        if rel is not None:
            raise ValueError(
                "rel=… needs a managed base and cwd='unmanaged' has none — "
                "pass cwd=… explicitly, or use the asinvoked policy"
            )
        return None
    base = Path(cwd) if cwd is not None else (None if ctx.cwd_unmanaged else ctx.cwd)
    if rel is None:
        return base
    rel_path = Path(rel)
    if rel_path.is_absolute() or rel_path.anchor:  # anchored = absolute on win
        raise ValueError(
            f"rel={str(rel)!r} is absolute — rel is a suffix on the call's cwd "
            f"base; pass an absolute directory as cwd=…"
        )
    if base is None and ctx.cwd_unmanaged:
        raise ValueError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "pass cwd=… explicitly, or use the asinvoked policy"
        )
    return (base if base is not None else Path.cwd()) / rel_path


def run(
    cmd: str | list[str] | Callable[..., Any],
    *args: Any,
    nofail: bool = False,
    recorded: bool = True,
    capture: bool = True,
    timeout: float | None = None,
    title: str | None = None,
    pre_record: Callable[[ResultView], None] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    rel: str | Path | None = None,
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

    `cwd=` roots this one call somewhere other than the task's directory;
    `rel=` appends a relative suffix to the base in force (`ctx.cwd`, or the
    explicit `cwd=`), so `run("npm run build", rel="web")` is the ergonomic
    spelling of `cwd=ctx.cwd / "web"`. `cwd="unmanaged"` opts this one call
    out instead — the task-level token, per call: a subprocess spawns
    inheriting the live process cwd, an in-process callable runs under it —
    for a call that touches no paths while its task keeps `ctx.cwd`
    everywhere else.

    **`recorded=False` runs this call off the record.** A call is a *step*
    by default: it earns a receipt line, a row in `--json`, a `recording()`
    entry, and its output joins the task's block. Some calls aren't part of
    the task's story, though — they are how a task *knows* something (`git
    rev-parse HEAD` in a release task). Those return their `Result` and
    report nothing: no record, no receipt, no output replayed.

    Everything else still applies, because the call is not unmanaged — only
    unreported. It runs in `ctx.cwd` with `ctx.env`, inside the task's lane,
    with colour resolved the same way, it is terminated with the rest on a
    fail-fast, and it still raises unless `nofail=True`. It also *executes*
    under `recording()`, where a step would be faked: a value read is not the
    story being recorded, and faking it would corrupt the story that is —
    the real steps downstream would record whatever a blank answer produced.

    **`pre_record=` reviews the record before it is sealed.** Some tools
    speak exit codes that need interpreting — `djlint --reformat` exits 1
    when it changed files, which is success for a formatting gate. The
    reviewer receives the draft (`ResultView`) after the work ran: it reads
    what was captured and may set `title` and `code`; the receipt, the
    record, and the raise-on-nonzero decision all read what the review
    leaves behind, so the call site writes no `nofail=`. A reviewer that
    raises fails the call with its own error — a broken reviewer is a
    broken gate — and the record keeps what the work honestly produced.
    Dry-run never reviews (nothing ran, nothing captured), and a
    `recorded=False` call has no record to review (a note, not an error).

    In-process work is a **step** now: `run()` runs commands. Lift a
    callable instead — `@step` / `step(fn, title=…)` builds an item that
    earns a receipt, and `with step("…"):` records a block where it
    stands. (The tools bridge keeps its in-process lane through its own
    private channel.)

    `_show` is an internal channel from the `tools.*` bridge: a structured
    view of the call, so the shown command line can be normalised and
    role-coloured while execution runs whatever the tool needs. An explicit
    `title` still wins; a direct `run([...])` is unaffected.
    """
    ctx = current()
    if (strict or clean) and not shell:
        # strict/clean harden a *shell* run — they mean nothing shell-free, and a
        # silent no-op would be exactly the surprise footman avoids elsewhere.
        which = "strict" if strict else "clean"
        raise ValueError(
            f"run({which}=True) only applies with a shell — it hardens a shell "
            f"run. Pass shell=True (or a shell name), or drop {which}."
        )
    if callable(cmd) and _show is None:
        raise TypeError(
            "run() runs commands; in-process work is a step. Lift the "
            "callable — @step on a def, step(fn, title='…') around one you "
            "didn't write, or `with step('…'):` over inline code — and it "
            "earns a real receipt."
        )
    out = sys.stdout
    color = _colored(ctx)
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

    if ctx.dry_run and recorded:
        # Record the step even when not executing: `dry_run` + `quiet` is the
        # silent-capture mode `footman.testing` builds on. The recorded label
        # is normalised; only the shown line colours or (under -v) goes exact.
        #
        # A non-step call runs anyway. It is not the story being recorded — it
        # is how the task learns something — and faking it would corrupt the
        # story that *is*: the real steps downstream would go on to record
        # whatever a blank answer produced (`git tag ` for a missing sha).
        result = Result(0, command=label, raw=raw)
        ctx.steps.append(result)
        if not ctx.quiet:
            out.write(f"$ {shown if color else shown_plain}\n")
        return result

    if title is not None and not recorded:
        # Not an error: `.opts()` merges along a chain, so a shared tool may
        # carry a title from where it was configured while a call site adds
        # `recorded=False` — neither author wrote the contradiction. Say it once.
        from footman import _globals as _pg_note

        _pg_note._note(
            "recorded-title",
            f"title= is ignored on a recorded=False call ({label}): there is no "
            f"receipt to label.",
        )

    if pre_record is not None and not recorded:
        # Same shape as the title note: `.opts()` merges along a chain, so a
        # shared tool may carry a reviewer while one call site goes off the
        # record — neither author wrote the contradiction. Say it once.
        from footman import _globals as _pg_note2

        _pg_note2._note(
            "pre-record-recorded",
            f"pre_record is ignored on a recorded=False call ({label}): there "
            f"is no record to review.",
        )

    show = recorded and not ctx.quiet and (ctx.verbose or not capture)
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
        if timeout is not None:
            # A Python callable cannot be interrupted safely — there is no
            # process to signal and no safe way to unwind another thread — so
            # a bound footman cannot honour is refused rather than ignored.
            # The tools bridge demotes an in-process *tool* to its subprocess
            # twin instead, exactly as it does for a foreign cwd.
            raise ValueError(
                "run(timeout=…) needs a process to bound, and this call runs "
                "in-process. Spawn it (a list/str command, or "
                ".opts(in_process=False) on a tool), or drop the timeout."
            )
        code, out_s, err_s = _run_callable(
            cmd, args, capture=capture, env=env, cwd=_target_cwd(ctx, cwd, rel)
        )
        timed_out = False
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
        # `env=` is the child's environment, exactly as `subprocess` means it —
        # what you pass is what it gets. Otherwise the task's own, which
        # already carries the run-wide colour decision published at the run
        # boundary (`color_environment`) and anything the body has set or
        # deleted. To add rather than replace, say so: `{**os.environ, …}` —
        # inside a task `os.environ` *is* this environment, so the copy is
        # exact rather than approximate.
        run_env = dict(env) if env is not None else dict(ctx.env)
        # A clean POSIX shell means no startup files: `--norc`/`--noprofile`
        # cover interactive/login rc, but bash/sh also source $BASH_ENV/$ENV for
        # a non-interactive `-c`, so drop those from the child env too.
        if clean and shell_kind in ("bash", "sh", "zsh"):
            run_env = {k: v for k, v in run_env.items() if k not in ("BASH_ENV", "ENV")}
        # `unmanaged` spawns with cwd=None (inherit the live process cwd),
        # task-level or per-call; a per-call cwd=/rel= override wins — see
        # `_target_cwd`.
        cwd_path = _target_cwd(ctx, cwd, rel)
        code, out_s, err_s, timed_out = _run_subprocess(
            argv,
            run_env,
            cwd_path,
            capture,
            encoding,
            timeout=timeout,
            killable=not ctx.atomic,
            # An interactive task owns the real terminal: keep its child in
            # footman's group so it keeps its controlling tty (and its Ctrl-C).
            isolate=not ctx.atomic and not ctx.interactive,
            # Tag the child with its task's policy: a fail-fast failure elsewhere
            # reaps this tree only if the task is fail-fast, not keep-going.
            keep_going=ctx.keep_going,
            # A captured child writes to pipes, so it needs no console of its
            # own; an uncaptured or interactive one is reaching for the real
            # terminal on purpose.
            no_window=capture and not ctx.interactive,
        )
    duration = time.perf_counter() - start
    if timed_out:
        # 124 is the shell convention for "killed by a timeout", so a code
        # travelling out through --json or a branded CLI reads as the thing it
        # is rather than a sentinel of footman's own invention. A Result *is*
        # its exit code, so this is chosen here, not assigned afterwards.
        code = 124
    # The address label is the stable identity — a title names the record,
    # the address names the node. A titled call is named whole; a raw
    # command names by its tool word plus its verb when it has one
    # (`git-push`, `uv-sync`) — descriptive enough to read and to keep
    # `git fetch`/`git push` distinct across runs, while flags stay out so
    # an option tweak never re-identifies the step.
    if title:
        addr_leaf = title
    else:
        tokens = label.split()
        addr_leaf = tokens[0] if tokens else "run"
        if len(tokens) > 1 and not tokens[1].startswith("-"):
            addr_leaf = f"{addr_leaf}-{tokens[1]}"
    addr = _child_address(ctx, addr_leaf)
    # The audit: the verdict's provenance. The body entry is always present
    # and carries what the work itself produced; review entries follow.
    audit: tuple[AuditEntry, ...] = (_audit_entry("body", label, code),)
    if pre_record is not None and recorded:
        # The review window: the work ran and the record is still a draft.
        # The reviewer reads what was captured and may amend the verdict —
        # title and code — before anything is sealed, shown, or raised. A
        # raising reviewer fails the call with the hook's own error: a broken
        # reviewer is a broken gate, not a shrug. The record keeps what the
        # work honestly produced in that case — review never finished, so
        # nothing it half-did is kept.
        hook = getattr(pre_record, "__name__", repr(pre_record))
        view = ResultView(
            title=label,
            code=code,
            stdout=out_s,
            stderr=err_s,
            duration=duration,
            raw=raw,
            command=label,
        )
        try:
            pre_record(view)
        except Exception as exc:
            ctx.steps.append(
                Result(
                    code,
                    command=label,
                    stdout=out_s,
                    stderr=err_s,
                    duration=duration,
                    raw=raw,
                    timed_out=timed_out,
                    address=addr,
                    audit=(*audit, _audit_entry("review", hook, None)),
                )
            )
            raise RuntimeError(
                f"pre_record hook {hook!r} failed reviewing {label!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        audit = (
            *audit,
            _audit_entry(
                "review", hook, view.code if "code" in view._touched else None
            ),
        )
        code = view.code
        label = view.title
    result = Result(
        code,
        command=label,
        stdout=out_s,
        stderr=err_s,
        duration=duration,
        raw=raw,
        timed_out=timed_out,
        address=addr,
        audit=audit,
    )
    if recorded:
        ctx.steps.append(result)  # what --json, the report and recording() read

    # Task grain at normal verbosity: a step's receipt shows under
    # --verbose (and for uncaptured, live steps, whose output needs its
    # label) — and ALWAYS when it failed. Green is collapsible; failure is
    # never hidden.
    if recorded and not ctx.quiet and (ctx.verbose or not capture or code != 0):
        ok = code == 0
        prefix = "\r\033[K" if ctx.tty and live else ""
        out.write(f"{prefix}{_step_line(ctx, ok, label, duration)}")
        # Join the two streams only to *display* them (stdout then stderr);
        # nothing merged is stored — the Result keeps them apart.
        combined = out_s + err_s
        if capture and combined and (not ok or ctx.verbose):
            out.write(combined if combined.endswith("\n") else combined + "\n")
        if not ok and len(result.audit) > 1:
            # The verdict's story, when anyone touched it: who acted, what
            # they set — the audit, one dim line under the failure.
            trail = " → ".join(
                f"{e.moment} {e.actor}" + (f" {e.code}" if e.code is not None else "")
                for e in result.audit
            )
            out.write(_dim(f"     audit: {trail}", _colored(ctx)) + "\n")
        out.flush()

    if code != 0 and not nofail:
        if result.timed_out:
            raise RunTimeout(result, timeout or 0.0)  # only set when one was given
        raise RunFailed(result)
    return result


# --- parallel() --------------------------------------------------------------


def _call_name(call: Callable[..., Any]) -> str:
    """The name a fan-out child shows on the status line and its step lines.

    A `functools.partial` unwraps to what it wraps; a lambda has no honest
    name of its own, so it shows as an ellipsis rather than `<lambda>`.
    """
    if isinstance(call, functools.partial):  # partial(fmt, check=True)
        call = call.func
    name = getattr(call, "__name__", "task")
    return "…" if name == "<lambda>" else name


class Pending:
    """What a task call hands back *inside* a `with parallel()` block.

    The call has been queued, not run, so there is no value yet — and a
    silent `None` here would be a bug you find much later. Every use is a
    taught error; the values arrive as `p.results` when the block ends.
    """

    __slots__ = ("_task",)

    _task: str

    def __init__(self, task: str) -> None:
        object.__setattr__(self, "_task", task)

    def _refuse(self, *_a: Any, **_k: Any) -> NoReturn:
        raise RuntimeError(
            f"{self._task} has not run yet — inside a `with parallel()` block a "
            f"call is queued, not executed, so it has no value to use there. "
            f"Read the values from the block's results after it ends."
        )

    __getattr__ = _refuse
    __bool__ = _refuse
    __iter__ = _refuse
    __len__ = _refuse
    __int__ = _refuse
    __float__ = _refuse
    __lt__ = _refuse
    __add__ = _refuse
    __hash__: ClassVar[None] = None  # type: ignore[assignment]  # unusable too

    def __str__(self) -> NoReturn:
        self._refuse()

    def __eq__(self, other: object) -> NoReturn:
        self._refuse()

    def __repr__(self) -> str:  # debuggers and tracebacks stay usable
        return f"<pending {self._task}>"


# The queue a `with parallel()` block collects into: a contextvar, so the
# calls a *queued task* makes when it later runs (on a pool thread, which
# starts from contextvar defaults) are ordinary calls, never re-collected.
_collecting: ContextVar[list[tuple[Any, tuple[Any, ...], dict[str, Any]]] | None] = (
    ContextVar("footman_parallel_block", default=None)
)


class Fanout(list[Result]):
    """The `with parallel() as p:` block — task calls inside it are queued,
    then run together when the block ends.

    A `list` underneath — of sealed records, each of which IS its exit code,
    so everything that read the block as a list of codes keeps working — and
    empty, so `parallel(*items)` with nothing to do still answers with the
    empty list it always did.
    """

    def __init__(self, keep_going: bool = False) -> None:
        super().__init__()
        self.keep_going: bool = keep_going
        self.results: list[Any] = []
        """What each queued call returned, in the order they were written."""
        self._queued: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self._token: Any = None
        self._births: list[_step.WorkItem[Any]] = []
        self._birth_token: Any = None

    def __enter__(self) -> Fanout:
        from footman import _step as _step_mod

        self._token = _collecting.set(self._queued)
        self._birth_token = _step_mod._born.set(self._births)
        return self

    def also(self, call: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Pending:
        """Retired: the block runs tasks and steps, nothing anonymous.

        Lift the code instead — a wrapper states intent, and the step it
        makes earns a real receipt:

            with parallel() as p:
                build("web")
                p(step(shutil.rmtree)(tmp, ignore_errors=True))
        """
        raise RuntimeError(
            "parallel().also(...) is gone: the block runs tasks and steps, "
            "nothing anonymous. Lift the call — p(step(fn)(args)) — and it "
            "earns a receipt too."
        )

    def __call__(self, item: _step.WorkItem[Any], /) -> Pending:
        """Queue a built step item for the block — the lifted counterpart of
        a task call, which queues itself."""
        from footman import _step as _step_mod

        if not isinstance(item, _step_mod.WorkItem):
            raise TypeError(
                f"a parallel block queues built step items — p(step(fn)(…)) "
                f"— got {type(item).__name__}. Task calls queue themselves; "
                f"write them as ordinary calls."
            )
        if _collecting.get() is not self._queued:
            raise RuntimeError(
                "p(item) queues work for a block, so call it inside the "
                "`with` — outside one there is nothing to join."
            )
        item._claimed = True
        self._queued.append((item, (), {}))
        return Pending(_call_name(item))

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        from footman import _step as _step_mod

        _collecting.reset(self._token)
        _step_mod._born.reset(self._birth_token)
        if exc_type is not None:
            return False  # the block itself failed: nothing to run
        dead = [w for w in self._births if not w._claimed]
        if dead:
            names = ", ".join(w.__name__ for w in dead)
            raise RuntimeError(
                f"built inside the block but never handed to it: {names}. "
                f"Building an item runs nothing — hand it to the block, "
                f"p(item), or call it to run it in place. Nothing ran."
            )
        values: list[Any] = [None] * len(self._queued)

        def thunk(
            index: int, task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> Callable[[], None]:
            def run() -> None:
                # The value goes to the slot, never through the return: a task
                # returning an int would read as an exit code out here.
                values[index] = task(*args, **kwargs)

            run.__name__ = _call_name(task)
            return run

        codes = _run_thunks(
            [thunk(i, t, a, k) for i, (t, a, k) in enumerate(self._queued)],
            keep_going=self.keep_going,
        )
        self.results = values
        self.extend(codes)  # the block *is* its list of records (codes included)
        return False


def _queue_call(task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Queue a call for the enclosing `with parallel()` block, or `None` when
    there is none — the one hook `_futures.call` needs."""
    queue = _collecting.get()
    if queue is None:
        return None
    queue.append((task, args, kwargs))
    from footman import registry

    return Pending(registry.cli_name(getattr(task, "__name__", "task")))


@overload
def parallel(*, keep_going: bool = False) -> Fanout: ...
@overload
def parallel(
    *calls: _step.WorkItem[Any]
    | _step.StepFn[..., Any]
    | _registry_t.TaskFn[..., Any]
    | _registry_t.Group,
    keep_going: bool = False,
) -> list[Result]: ...


def parallel(
    *calls: _step.WorkItem[Any]
    | _step.StepFn[..., Any]
    | _registry_t.TaskFn[..., Any]
    | _registry_t.Group,
    keep_going: bool = False,
) -> Fanout | list[Result]:
    """Run tasks and steps concurrently; wait; fail if any fail.

    Each one runs in a child of the current context with its own output
    buffer, flushed atomically on completion so concurrent output never
    interleaves. Pass task functions directly (`parallel(lint, typecheck)`)
    and built step items for everything else — `parallel(convert(images))`,
    or `parallel(step(fn)(args))` for a function you didn't write. A bare
    zero-argument maker is welcome too: `parallel(clean)` builds and runs
    `clean()`.

    Nothing anonymous runs: footman only schedules, records, and safely
    cancels work it owns, and a bare callable is a stranger — no name for
    the report, no place in the plan, no way to stop it cleanly. The lift
    is one word, and it buys the step a receipt.

    With no arguments it is a **block** instead, and the calls inside it are
    the fan-out — written as ordinary calls, with their values afterwards:

        with parallel() as p:
            build("web")
            p(step(shutil.rmtree)(tmp, ignore_errors=True))
        web, cleaned = p.results

    Inside the block a task call is *queued*, so it has no value there (using
    one is a taught error); a built step item joins through `p(item)`;
    everything runs when the block ends, under the same rules — sharing,
    hooks, `-s`/`-j`.
    """
    from footman import _step

    if not calls:
        # Nothing to run: the block form, which is also an empty list of exit
        # codes — so `parallel(*items)` over an empty sequence is unchanged.
        return Fanout(keep_going=keep_going)

    from footman import registry as _registry

    accepted: list[Callable[[], Any]] = []
    for c in calls:
        if isinstance(c, _step.StepFn):
            # A zero-argument maker is welcome bare: build its item here, so
            # `parallel(covered)` and `parallel(covered())` mean the same.
            c = c()
        if isinstance(c, _step.WorkItem):
            accepted.append(c)
            continue
        if getattr(c, "_footman_pre", None) is not None or isinstance(
            c, _registry.Group
        ):
            accepted.append(c)  # a task, an opted reference, a runnable group
            continue
        label = _call_name(c) if callable(c) else type(c).__name__
        raise TypeError(
            f"parallel() runs tasks and steps — {label!r} is neither. "
            f"footman only schedules, records, and safely cancels work it "
            f"owns, and a bare callable is a stranger. Lift it and it earns "
            f"a receipt too: parallel(step(fn)(…)), or step(fn, "
            f"title='…') to name a lambda."
        )
    return _run_thunks(accepted, keep_going=keep_going)


def _run_thunks(
    calls: list[Callable[[], Any]], *, keep_going: bool = False
) -> list[Result]:
    """The fan-out engine: run zero-argument callables the caller vouches
    for. `parallel()` validates and lands here; the block's `__exit__`
    drives it directly with the closures footman itself wrote."""
    from concurrent.futures import ThreadPoolExecutor

    parent = current()
    dest = parent.sink or real_stdout()
    dest_is_real = parent.sink is None
    lock = threading.Lock()

    # parallel() children are units on the live status line, exactly like
    # scheduler nodes — a chain and a task-body fan-out present identically.
    # Every child counts once here, whatever it is: a task, a partial, a
    # lambda wrapping a call, a plain thunk. What the child *does* can't be
    # known from the outside — a closure is opaque — so the count can't
    # depend on reading it. Instead the unit is handed to the child (see
    # `Context.unit_pending`), and the first task request inside claims it
    # rather than counting a second one for the same piece of work.
    status = _status
    if status is not None:
        status.unit_added(len(calls))

    # Sibling names are known up front, so their step lines can align.
    width = max((len(_call_name(c)) for c in calls), default=0)

    def invoke(call: Callable[[], Any]) -> tuple[Result, BaseException | None]:
        name = _call_name(call)
        child_start = time.perf_counter()
        if status is not None:
            status.unit_started(name)
        # One buffer for both streams at task level, so the atomic flush keeps
        # this child's stdout/stderr in order; a run() inside it still splits the
        # step's streams via a temporary swap.
        buf = io.StringIO()
        child = replace(
            parent,
            address=_child_address(parent, name),
            _labels={},
            sink=buf,
            err_sink=buf,
            steps=[],
            task=name,
            name_width=width,
            # This child's unit is counted above; the first task request
            # inside claims it instead of counting its own.
            unit_pending=True,
        )
        token = _current.set(child)
        try:
            # The arbiter counts every child body (an exclusive drain must
            # see them); a lineage child of a lane holder bypasses the bars —
            # `serial_active` rode in through `replace(parent, …)`.
            with _globals.lane(None, name=name, inherited=child.serial_active):
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
            # (A string reason is not carried through here: parallel() deliberately
            # normalises failures to a catchable RunFailed, so it must not re-raise
            # a BaseException. A task body's own sys.exit("reason") keeps its
            # message — see executor._call.)
            code = _exit_code(exc)
            error = None
            if code != 0:
                thunk = _label(call, ())
                error = RunFailed(Result(code, command=thunk, raw=thunk))
        except Failed as exc:
            # `footman.fail("reason")` in a thunk: an Exception (not a
            # BaseException), so the gate can re-raise it and its reason surfaces
            # at the task level — better than the sys.exit-in-parallel corner.
            # Its own code rides the return list too.
            code, error = exc.code, exc
        except Exception as exc:  # a failed call must not crash the pool
            code, error = 1, exc
        finally:
            _current.reset(token)
        gate = _globals.console_gate() if dest_is_real else contextlib.nullcontext()
        with gate, lock:
            blob = buf.getvalue()
            # A child that ended mid-colour (a crash, an unterminated SGR) must
            # not bleed into the next child's block when they interleave: cap a
            # colourful run's block with a reset. Only when colour is on, so a
            # byte-clean run stays byte-clean.
            if blob and _colored(parent):
                blob += "\033[0m"
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
        # The caller's view of this child: a sealed record that IS its exit
        # code (so every code-reader keeps working), named and addressed.
        record = Result(
            code,
            command=name,
            address=child.address,
            duration=time.perf_counter() - child_start,
        )
        return record, error

    # -s reaches inside tasks (one worker serialises the calls in
    # submission order), and -j caps the width; same code path either way.
    if parent.sequential:
        workers = 1
    elif parent.jobs > 0:
        workers = max(1, min(parent.jobs, len(calls)))
    else:
        workers = max(1, len(calls))
    # Parked for the pool wait: this body is blocked in footman code and
    # cannot touch globals, so an exclusive drain may exempt it (the
    # ancestry exemption — its own children still count on their own).
    with (
        _globals.parked(),
        ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="fm-parallel"
        ) as pool,
    ):
        outcomes = list(pool.map(invoke, calls))

    if not keep_going:
        for _record, error in outcomes:
            if error is not None:
                raise error
    return [record for record, _ in outcomes]
