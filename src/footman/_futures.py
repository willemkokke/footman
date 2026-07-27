"""Tasks as run-scoped futures: what happens when a task body calls a task.

`artifact = build()` inside a task body used to be a plain function call — it
ran `build` again even if the DAG had just run it, on the caller's thread, with
no context of its own and no `TaskResult` anywhere. That was the one execution
path the framework couldn't see.

Now a call routes through here, and per **(task, resolved arguments)** the run
keeps a once-cell:

* already run → the memoised value comes back;
* running on another thread → the caller blocks on its future;
* neither → the caller claims it and runs it **inline, on its own thread**.

So `pre=[build]` plus `build()` in the body is a cache hit, not a second build,
and the callee gets full task treatment: its own context, its own lane
decision, its own reported result.

Sharing is a property of the *request*, not of this layer: a request resolved
volatile (`@task(volatile=…)`, `.opts(volatile=…)`, or inherited from the task
that asked) never reads a cell. It still fills an empty one, because what a run
remembers is the first result it produced, and a later shared request can reuse
it. `schedule` resolves the same ladder for DAG nodes, so a declared request and
a called one behave the same.

Two things are deliberately *not* here. A call outside a run is a plain call —
importing a tasks file and calling a function must keep working. And lane
acquisition stays at the task boundary, so a body call to a `serial=`/
`exclusive=` task is refused rather than deadlocking mid-body.
"""

from __future__ import annotations

import dataclasses
import io
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from footman import registry
from footman.split import ChainError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from footman.executor import TaskResult

# The memo key's argument half when the arguments cannot be frozen (an
# unhashable value with no obvious frozen form — a live object, a generator).
# Such a call is honest work every time rather than a wrong cache hit.
_UNKEYABLE = object()


class _Cell:
    """One (task, arguments) execution: its future, its owner, its label."""

    __slots__ = ("future", "label", "owner")

    def __init__(self, owner: int, label: str) -> None:
        self.future: Future[Any] = Future()
        self.owner = owner  # the thread that claimed it (for the wait graph)
        self.label = label


class _Session:
    """The run's cells, plus the wait-for graph that keeps them honest."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cells: dict[Any, _Cell] = {}
        self.waits: dict[int, Any] = {}  # thread -> the key it is blocked on
        self.results: list[TaskResult] = []


_active: _Session | None = None


@contextmanager
def session() -> Iterator[_Session]:
    """Install a fresh cell registry for the duration of one run.

    Run-scoped by construction: memoisation means "this work happened in *this*
    run", never a cache that outlives it. Nested installs (a `Runner` inside a
    task, tests) keep the outer session — one run, one memo.
    """
    global _active
    if _active is not None:
        yield _active
        return
    _active = _Session()
    try:
        yield _active
    finally:
        _active = None


def collected() -> list[TaskResult]:
    """Results of tasks run by body calls this run, in completion order."""
    return list(_active.results) if _active is not None else []


def _freeze(value: Any) -> Any:
    """A hashable normal form for one argument value, or `_UNKEYABLE`.

    Collections get frozen shapes (a list and a tuple of the same items are
    the same work), and anything already hashable is its own key. What has no
    frozen form is not forced into one: it reads as unkeyable, and its call
    runs.
    """
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        parts = tuple(_freeze(v) for v in value)
        return _UNKEYABLE if _UNKEYABLE in parts else parts
    if isinstance(value, (set, frozenset)):
        parts = tuple(sorted((_freeze(v) for v in value), key=repr))
        return _UNKEYABLE if _UNKEYABLE in parts else frozenset(parts)
    if isinstance(value, dict):
        items = tuple(
            sorted(((_freeze(k), _freeze(v)) for k, v in value.items()), key=repr)
        )
        return _UNKEYABLE if any(_UNKEYABLE in p for p in items) else items
    try:
        hash(value)
    except TypeError:
        return _UNKEYABLE
    return value


def wants_fresh(task: Any) -> bool:
    """Whether *this request* must execute rather than reuse an execution.

    The sharing ladder for a call: the reference's own `.opts(volatile=…)` or
    the task's declaration, then what the calling task inherited (`ctx.volatile`
    — a freshly-requested task asks freshly for everything it needs), then
    shared. The scheduler resolves the same ladder for a node.
    """
    from footman import context

    own = registry.volatility(task)
    if own is not None:
        return own
    ctx = context._current.get()
    return ctx is not None and ctx.volatile


def _key(task: Any, args: Any, kwargs: dict[str, Any]) -> Any | None:
    """The cell key for this work: the task's dedup identity plus its arguments
    in the same normal form binding produces, so a DAG node and a body call that
    resolve to the same arguments name one piece of work. `None` when the
    arguments have no frozen form.
    """
    from footman import manifest

    try:
        bound = manifest.resolved_signature(task).bind(*args, **kwargs)
    except TypeError:
        return None  # a call that won't bind: let it raise where it is made
    bound.apply_defaults()
    frozen = tuple(
        (name, _freeze(value))
        for name, value in bound.arguments.items()
        if name != "ctx"  # injected, never part of the work's identity
    )
    if any(value is _UNKEYABLE for _, value in frozen):
        return None
    return (registry.work_key(task), frozen)


def _deadlock(run: _Session, key: Any, me: int) -> list[str] | None:
    """The wait-for chain proving this wait can never resolve, or `None`.

    The static DAG has `_check_cycles`; a call graph only exists at runtime, so
    it is walked here: follow the key to its owning thread, then that thread's
    own wait, until either the chain ends (fine) or it arrives back at this
    thread (a cycle — nobody would ever finish).
    """
    chain: list[str] = []
    at: Any = key
    seen: set[Any] = set()
    while at is not None and at not in seen:
        seen.add(at)
        cell = run.cells.get(at)
        if cell is None or cell.future.done():
            return None  # finished work blocks nobody
        chain.append(cell.label)
        if cell.owner == me:
            return chain
        at = run.waits.get(cell.owner)
    return None


def _label(task: Any) -> str:
    return registry.cli_name(getattr(task, "__name__", "task"))


def call(task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """A task called from Python — the body-call path.

    Outside a run this is the plain function call it always was. Inside one it
    is the once-cell: hit the memo, wait for the thread that is running it, or
    claim it and run it here.
    """
    run = _active
    if run is None:
        if isinstance(task, registry._Opted):
            return task._plain_call(args, kwargs)
        return registry.task_body(task)(*args, **kwargs)
    _refuse_unrunnable(task)
    key = _key(task, args, kwargs)
    if key is None:  # arguments with no frozen form: honest work every time
        return _run_now(task, args, kwargs)
    if wants_fresh(task):
        # Asked for freshly: never read a cell. It still *fills* an empty one,
        # because the first result of a run is the one the run remembers — a
        # later shared request can reuse it.
        value = _run_now(task, args, kwargs)
        _fill(key, _label(task), value)
        return value

    me = threading.get_ident()
    claimed, cell = _claim(run, key, me, _label(task))
    if claimed:  # nobody had run it: this thread owns it, inline, right here
        try:
            value = _run_now(task, args, kwargs)
        except BaseException as exc:
            cell.future.set_exception(exc)
            raise
        cell.future.set_result(value)
        return value
    try:
        # A finished cell answers instantly (the memo); a live one blocks until
        # the thread that claimed it is done, and both hand back one value.
        value = cell.future.result()
    finally:
        with run.lock:
            run.waits.pop(me, None)
    _record(_cached_result(cell.label, value))
    return value


def _claim(run: _Session, key: Any, me: int, label: str) -> tuple[bool, _Cell]:
    """Take ownership of *key*'s cell, or hand back the cell already there.

    Returns `(claimed, cell)` — `claimed` means this thread must run the work.
    Waiting on a cell is registered in the wait-for graph while the lock is
    held, so a cycle is caught before anyone blocks on it.
    """
    with run.lock:
        cell = run.cells.get(key)
        if cell is None:
            cell = _Cell(me, label)
            run.cells[key] = cell
            return True, cell
        if cell.future.done():
            return False, cell  # already run this run: the memo answers
        if (chain := _deadlock(run, key, me)) is not None:
            raise ChainError(
                f"{label}: this call can never return — "
                f"{' → '.join([*chain, chain[0]])} waits on itself. "
                f"A task cannot call (directly or through another task) "
                f"a task that is waiting for it; declare the order with "
                f"pre= instead."
            )
        run.waits[me] = key
        return False, cell


def publish(task: Any, args: Any, kwargs: dict[str, Any], value: Any) -> None:
    """Record a task's pristine return so a later request can reuse it.

    Called by the executor for every task it runs, which is what makes
    `pre=[build]` followed by `build()` in the body one build.
    """
    key = _key(task, tuple(args), kwargs)
    if key is not None:
        _fill(key, _label(task), value)


def _fill(key: Any, label: str, value: Any) -> None:
    """Remember *value* for *key* — the first result to arrive, and only it.

    First-write-wins: what the run remembers is the first execution's result,
    so a freshly-requested re-run never rewrites history, and a cell the
    machinery already owns is left for its claimant to resolve.
    """
    run = _active
    if run is None:
        return
    with run.lock:
        if key in run.cells:
            return
        cell = _Cell(threading.get_ident(), label)
        cell.future.set_result(value)
        run.cells[key] = cell


def _record(result: TaskResult) -> None:
    """Add a called task's outcome to the run's report — ran, refused, denied,
    or satisfied by an execution the run had already performed."""
    if (run := _active) is not None:
        run.results.append(result)


def _cached_result(label: str, value: Any) -> TaskResult:
    """The report entry for a request the run had already satisfied.

    Recorded rather than left invisible: the work happened, and a reader (or a
    journal) should see that a second request for it was answered instead of
    silently vanishing. It never began, so it carries no start and is placed by
    cause — `blocked_by` names the task whose own run satisfied it, which is
    itself, so the ordering rule lands it directly after that execution. `ok`
    is true because the work did succeed, just earlier, so the run's exit code
    is untouched.
    """
    from footman.executor import TaskResult

    return TaskResult(
        task=label,
        ok=True,
        returned=value,
        state="cached",
        blocked_by=label,
    )


def _refuse_unrunnable(task: Any) -> None:
    """Refuse the two body calls that cannot be given a task boundary.

    A `serial=`/`exclusive=` task's lane is acquired at the boundary and never
    mid-body — the invariant that keeps the arbiter deadlock-free — and an
    `infinite` task is a future that never resolves.
    """
    name = _label(task)
    if registry.task_lane(task) is not None:
        raise ChainError(
            f"{name} declares a serial/exclusive lane, so it cannot be called "
            f"from a task body — its lane is taken at the task boundary, never "
            f"mid-body. Declare it as pre=[{name}] instead."
        )
    if registry.is_infinite(task):
        raise ChainError(
            f"{name} is an infinite task, so calling it from a task body would "
            f"never return. Run it as its own segment, or declare it as "
            f"pre=[{name}]."
        )


def _run_now(task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Run *task* here and now with full task semantics, and return its value.

    A failure raises in the caller, exactly as a direct call always did — and
    the callee's own `TaskResult` still joins the run's report, so the work is
    visible even though the value came back through Python.

    Every gate a declared task passes, a called one passes too: `@requires`
    availability, and `@task(confirm=)`. The confirm is the one gate that
    cannot be resolved up front the way the scheduler resolves a segment's —
    a call is not knowable before the run — so it is asked here, at the call.
    """
    from footman import context, executor, schedule

    parent = context.current()
    label = _label(task)
    seg = schedule._default_seg(task)
    if (refusal := executor.unavailable(task, seg)) is not None:
        _record(refusal)
        raise refusal.error or ChainError(f"{label} is unavailable")
    if (denial := schedule.confirm_gate(task, seg, parent)) is not None:
        _record(denial)
        raise denial.error or ChainError(f"{label} was not confirmed")
    buf = io.StringIO()
    child = dataclasses.replace(
        parent,
        fn=task,
        env=dict(parent.env),
        cwd=None,  # let the callee's own cwd policy resolve
        sink=buf,
        err_sink=buf,
        steps=[],
        task=label,
    )
    result = executor.run_bound(
        task, seg, child, list(args), dict(kwargs), as_call=True
    )
    _record(result)
    if parent.sink is not None and (text := buf.getvalue()):
        parent.sink.write(text)  # the callee's output belongs to the run, not /dev/null
    if result.error is not None:
        # A failure raises in the caller, exactly as a direct call always did.
        raise result.error
    if not result.ok:  # a bare `sys.exit(N)`, which carries no error object
        raise ChainError(f"{label} exited with code {result.code}")
    return result.returned
