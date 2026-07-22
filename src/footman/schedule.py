"""Build the task DAG and run it — in parallel by default, or sequentially.

The chain segments plus each task's `pre`/`post` form a dependency graph
(deduped by task identity). Independent nodes run concurrently on a thread pool;
a node runs once all its prerequisites have succeeded. footman tasks are almost
always I/O-bound (they shell out through `footman.run`, releasing the GIL),
so threads give real concurrency without process isolation.

Output is buffered per task and flushed atomically on completion, so concurrent
tasks never interleave.
"""

from __future__ import annotations

import io
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from itertools import count
from typing import Any, TextIO

from footman import _describe, _progress, context, executor
from footman.registry import (
    Group,
    Task,
    _Opted,
    is_infinite,
    is_interactive,
    keeps_going,
    task_confirm,
    wants_progress,
)
from footman.split import ChainError, Segment


@dataclass
class _Node:
    fn: Task
    seg: Segment
    key: int
    deps: set[int] = field(default_factory=set)
    state: str = "pending"  # pending / running / done / skipped
    result: executor.TaskResult | None = None
    forwarded: dict[str, Any] = field(default_factory=dict)  # `forward`ed values in
    forward_targets: list[_Node] = field(default_factory=list)  # …and out
    keep_going: bool = False  # resolved failure policy for THIS node (per-subtree)


def _default_seg(fn: Task) -> Segment:
    return Segment(task=fn.__name__, path=[fn.__name__])


def _dep_key(fn: Task) -> tuple[int, frozenset[tuple[str, Any]]]:
    """The deduplication identity of a DAG dependency: its base task plus its
    frozen option overrides. A bare task is simply "base with no overrides", so
    it shares one uniform key shape with an `.opts()` reference — and an empty
    `.opts()` (no override at all) collapses onto the bare task by construction,
    with no int-vs-tuple asymmetry. Identical policies share a node (a shared
    prerequisite still runs once); a genuinely different policy is a distinct
    node. The override identity itself lives in `_Opted._dedup_key`."""
    return fn._dedup_key() if isinstance(fn, _Opted) else (id(fn), frozenset())


def _as_task(dep: Task | Group | _Opted) -> Task:
    """A `pre`/`post` dependency may name a runnable group; resolve it to the
    group's default action so it runs (and fans out) like any other task. An
    `.opts()`-wrapped group resolves the same way, carrying its overrides onto
    the default task."""
    if isinstance(dep, _Opted):
        base = dep._opted_base
        if isinstance(base, Group):
            return _Opted(_as_task(base), dep._opted_overrides)
        return dep  # opts on a task is already a valid task reference
    if isinstance(dep, Group):
        if dep.default_task is None:
            raise ChainError(
                f"group {dep.name!r} is a prerequisite but has no @group.default"
            )
        return dep.default_task
    return dep


def _build_dag(root: Group, segments: list[Segment]) -> list[_Node]:
    """Nodes for the chain plus their transitive pre/post deps.

    Explicit chain segments each get their own node — repeating a task in the
    chain (`build web build api`) runs it once per mention. Only shared
    pre/post prerequisites are deduped, by task identity, so a prerequisite
    pulled in twice still runs once. Node keys are serial ints; `dep_nodes`
    maps a task to the node its bare deps resolve to.
    """
    nodes: list[_Node] = []
    dep_nodes: dict[object, _Node] = {}
    counter = count()
    seen_explicit: set[object] = set()

    def new_node(fn: Task, seg: Segment) -> _Node:
        node = _Node(fn, seg, next(counter))
        nodes.append(node)
        return node

    def add_dep(fn: Task) -> _Node:
        node = dep_nodes.get(_dep_key(fn))
        if node is None:
            node = new_node(fn, _default_seg(fn))
            dep_nodes[_dep_key(fn)] = node
            _link(node)
        return node

    def _thread(dep: _Node, fmap: dict[str, Any], source: str) -> None:
        # A forwarded value reaches only a dispatched task that *declares* the
        # parameter (partial reach); two dispatchers sending different values to
        # a shared prerequisite is a taught error, not a silent last-wins.
        if not fmap:
            return
        declared = {
            p.name for p in executor.resolved_signature(dep.fn).parameters.values()
        }
        for name, value in fmap.items():
            if name not in declared:
                continue
            if name in dep.forwarded and dep.forwarded[name] != value:
                raise ChainError(
                    f"{dep.seg.task}: {name!r} is forwarded with conflicting values "
                    f"(one from {source!r}); run the forwarding tasks separately"
                )
            dep.forwarded[name] = value

    def _link(node: _Node) -> None:
        pre = list(getattr(node.fn, "_footman_pre", []))
        # An empty-body group default fans out the group's own tasks: they become
        # implicit prerequisites, so the scheduler runs them (in parallel) and the
        # default's forward-marked values thread into the ones that declare them.
        group = getattr(node.fn, "_footman_default_group", None)
        if group is not None and getattr(node.fn, "_footman_default_fanout", False):
            pre = [*group.tasks.values(), *pre]
        for dep in pre:
            d = add_dep(_as_task(dep))
            node.deps.add(d.key)
            node.forward_targets.append(d)  # forwarding threaded in a later pass
        for dep in getattr(node.fn, "_footman_post", []):
            d = add_dep(_as_task(dep))
            d.deps.add(node.key)
            node.forward_targets.append(d)

    for seg in segments:
        fn = executor.resolve(root, seg.path)
        key = _dep_key(fn)
        existing = dep_nodes.get(key)
        if existing is not None and key not in seen_explicit:
            # First explicit mention of a task already pulled in as a bare dep:
            # adopt this segment's args instead of creating a duplicate.
            existing.seg = seg
            seen_explicit.add(key)
            continue
        node = new_node(fn, seg)
        if existing is None:
            dep_nodes[key] = node
        seen_explicit.add(key)
        _link(node)

    # Thread forwarded values in a second pass, dependents before their deps (the
    # reverse of the run order), so a node's *received* values are complete before
    # it forwards on — this is what makes forwarding chain through a group default
    # into its surfaces. It runs after segment adoption above, so each node's seg
    # (hence its forward map) is final.
    for node in reversed(_toposort(nodes)):
        fmap = executor.forward_map(node.fn, node.seg, node.forwarded)
        for target in node.forward_targets:
            _thread(target, fmap, node.seg.task)
    return nodes


def _check_cycles(nodes: list[_Node]) -> None:
    """Reject a cyclic dependency graph with a taught error naming the cycle.

    Without this check the run loop would find no ready node, run nothing, and
    exit 0 — a silent success that lies.
    """
    by_key = {n.key: n for n in nodes}
    state: dict[int, int] = {}  # 1 = on the current path, 2 = fully explored

    def visit(node: _Node, path: list[str]) -> None:
        state[node.key] = 1
        path.append(node.seg.task)
        for dep in node.deps:
            child = by_key.get(dep)
            if child is None:
                continue
            mark = state.get(child.key, 0)
            if mark == 1:
                cycle = [*path[path.index(child.seg.task) :], child.seg.task]
                raise ChainError(
                    f"dependency cycle: {' -> '.join(cycle)} "
                    f"(check the pre/post declarations of these tasks)"
                )
            if mark == 0:
                visit(child, path)
        path.pop()
        state[node.key] = 2

    for node in nodes:
        if state.get(node.key, 0) == 0:
            visit(node, [])


def _toposort(nodes: list[_Node]) -> list[_Node]:
    """Deps before dependents, stable by appearance order."""
    by_key = {n.key: n for n in nodes}
    result: list[_Node] = []
    seen: set[int] = set()

    def visit(node: _Node) -> None:
        if node.key in seen:
            return
        seen.add(node.key)
        for dep in node.deps:
            if dep in by_key:
                visit(by_key[dep])
        result.append(node)

    for node in nodes:
        visit(node)
    return result


def _plain_output(no_color: bool) -> bool:
    """No colour at all: the `--no-color` flag, `NO_COLOR`, or a dumb terminal.

    Per D6 this means the live rewrite is *absent*, not rewritten without escape
    codes — the same output a pipe gets.
    """
    return no_color or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"


def _make_ctx(
    seg: Segment,
    ctx_config: dict[str, Any] | None,
    *,
    sequential: bool,
    capture: bool,
    real: TextIO,
    name_width: int = 0,
    keep_going: bool = False,
) -> context.Context:
    ctx = context.Context(**(ctx_config or {}), passthrough=list(seg.passthrough or []))
    ctx.keep_going = keep_going  # per-subtree policy; tags this task's subprocesses
    ctx.sink = None if (sequential and not capture) else io.StringIO()
    # Step lines dress for their *destination*: a buffered block replays
    # onto `real`, so its children style exactly as parallel() children
    # style for their parent's terminal — both engines, one look. Only
    # liveness (sink is None, judged in run()) gates in-place rewrites.
    ctx.tty = not capture and real.isatty() and not _plain_output(ctx.no_color)
    ctx.task = seg.task
    ctx.name_width = name_width
    return ctx


def resolve_keep_going(root: Group, segments: list[Segment], cli: bool | None) -> bool:
    """A run-wide *summary* of the failure policy: does anything keep going?

    An explicit command-line choice (`-k` / `--fail-fast`) wins; unspecified,
    true if any invoked task — a chain task or a `pre`/`post` prerequisite —
    declares (or `.opts()`-overrides) `keep_going=True`. The scheduler resolves
    the actual *per-node* policy with `_scope_keep_going`; this summary is for
    callers that just want the one-bit answer.
    """
    if cli is not None:
        return cli
    try:
        nodes = _build_dag(root, segments)
    except (KeyError, IndexError, ChainError):
        return False  # a malformed chain surfaces its real error in run_plan
    return any(keeps_going(n.fn) is True for n in nodes)


def _scope_keep_going(nodes: list[_Node], cli: bool | None) -> None:
    """Assign each node its failure policy — the per-subtree scoping.

    A command-line `-k`/`--fail-fast` wins run-wide. Otherwise each node takes
    its own declared (or `.opts()`-overridden) `keep_going`, and a keep-going
    node propagates that down its own subtree — its `pre`/`post` prerequisites
    keep going with it — so a mixed chain honours each side: a keep-going gate
    surfaces all of its own failures while an independent fail-fast task still
    bails on the first. A node's own policy always wins over an inherited one, so
    an explicit fail-fast prerequisite stays a fail-fast boundary. Unspecified
    everywhere is the built-in fail-fast.
    """
    if cli is not None:
        for node in nodes:
            node.keep_going = cli
        return
    inherited: set[int] = set()  # keys a keep-going dependent has reached
    for node in reversed(_toposort(nodes)):  # dependents resolve before their deps
        own = keeps_going(node.fn)  # True / False / None (reads declared + opted)
        node.keep_going = own if own is not None else node.key in inherited
        if node.keep_going:
            inherited.update(node.deps)  # keep this subtree's prerequisites going


def dag_wants_progress(root: Group, segments: list[Segment]) -> bool:
    """Whether every task in the expanded DAG — pre/post deps included —
    consented to timing. One `@task(progress=False)` opts the run out of
    recording and of a determinate bar (the pulse still shows)."""
    return all(wants_progress(n.fn) for n in _build_dag(root, segments))


class NotConfirmed(Exception):
    """A `@task(confirm=…)` gate was declined (or unanswerable off a terminal)."""

    def __init__(self, task: str) -> None:
        super().__init__(f"{task}: not confirmed")


def _ask_confirm(message: str, *, no_input: bool) -> bool:
    """The `@task(confirm=)` gate. Off a terminal or under `--no-input` the
    answer is no — like just and go-task, a confirm fails without `--yes`
    rather than proceeding unasked. Asked on stderr before output routing."""
    if no_input or not context._stdin_is_tty():
        return False
    reply = context._prompt_core(f"{message} [y/N] ", default="n")
    return reply.strip().lower() in ("y", "yes")


def _gate_confirms(
    root: Group, segments: list[Segment], ctx_config: dict[str, Any] | None
) -> tuple[list[Segment], list[executor.TaskResult]]:
    """Resolve each invoked task's `@task(confirm=)` before the DAG is built —
    asked in invocation order, before any prerequisite runs. A confirmed task
    is kept; a denied one is dropped (so its exclusive pre-deps are pruned with
    it) and reported as a failed 'not confirmed' result, so the run exits
    non-zero. `--yes` auto-confirms every gate."""
    cfg = ctx_config or {}
    assume_yes = bool(cfg.get("assume_yes"))
    no_input = bool(cfg.get("no_input"))
    kept: list[Segment] = []
    denied: list[executor.TaskResult] = []
    for seg in segments:
        message = task_confirm(executor.resolve(root, seg.path))
        if not message or assume_yes or _ask_confirm(message, no_input=no_input):
            kept.append(seg)
        else:
            denied.append(
                executor.TaskResult(
                    task=seg.task, ok=False, code=1, error=NotConfirmed(seg.task)
                )
            )
    return kept, denied


def run_plan(
    root: Group,
    segments: list[Segment],
    *,
    sequential: bool = False,
    keep_going: bool | None = None,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
    estimate: _progress.Estimate | None = None,
    progress: bool = True,
    jobs: int = 0,
) -> list[executor.TaskResult]:
    """Build and run the DAG; return results in dependency order."""
    context.reset_abort()  # clear any latched fail-fast from a previous run
    segments, denied = _gate_confirms(root, segments, ctx_config)
    nodes = _build_dag(root, segments)
    _check_cycles(nodes)
    _scope_keep_going(nodes, keep_going)  # per-node failure policy (tri-state + scope)
    # One node has nothing to parallelise — run it on the sequential-live
    # path instead: output streams as it happens, and run()'s TTY mode
    # (colour, in-place step rewrite) applies. `fm check` is this shape. An
    # interactive task also forces sequential: it owns the terminal, so it
    # can't share with parallel siblings (a human-wait is the bottleneck).
    # An interactive task owns the real terminal: it forces sequential (it can't
    # share with parallel siblings) and suppresses the status line, whose
    # clear-line repaints would otherwise erase its prompt.
    interactive = any(is_interactive(n.fn) for n in nodes)
    sequential = sequential or len(nodes) == 1 or interactive
    # A run containing an infinite task has no progress to show — its
    # duration isn't late, it's intentional. The status line yields to a
    # one-time hint (printed at the node's start) saying how this ends.
    endless = any(is_infinite(n.fn) for n in nodes)
    with context.routing() as (real, err):
        status = _make_status(
            err,
            ctx_config,
            capture,
            estimate,
            progress and not endless and not interactive,
        )
        if status is not None:
            status.unit_added(len(nodes))
            context.set_status(status)  # parallel() and the routers find it
            status.open()
        try:
            if sequential:
                cfg = ctx_config or {}
                hint_err = (
                    err
                    if endless
                    and not capture
                    and not cfg.get("quiet")
                    and err.isatty()
                    and not _plain_output(bool(cfg.get("no_color")))
                    else None
                )
                try:
                    _run_sequential(nodes, real, capture, ctx_config, status, hint_err)
                except BaseException:
                    # Ctrl-C mid-task: the running child is group-isolated, so it
                    # missed the terminal's SIGINT — reap its tree by hand before
                    # the interrupt propagates. (Parallel does this itself.)
                    context.terminate_live_children()
                    raise
            else:
                _run_parallel(nodes, real, err, capture, ctx_config, status, jobs)
        finally:
            if status is not None:
                context.set_status(None)
                status.close()
    return denied + [n.result for n in _toposort(nodes) if n.result is not None]


def _run_sequential(nodes, real, capture, ctx_config, status, err=None) -> None:
    done: dict[int, bool] = {}
    failed = False
    width = max((len(n.seg.task) for n in nodes), default=0)
    for node in _toposort(nodes):
        if any(not done.get(d) for d in node.deps) or (failed and not node.keep_going):
            node.state = "skipped"
            if status is not None:
                status.unit_skipped(node.seg.task)
            continue
        ctx = _make_ctx(
            node.seg,
            ctx_config,
            sequential=True,
            capture=capture,
            real=real,
            name_width=width,
            keep_going=node.keep_going,
        )
        if status is not None:
            status.unit_started(node.seg.task)
        if err is not None and is_infinite(node.fn):
            hint = f"{node.seg.task} runs until you stop it — Ctrl-C"
            err.write(_describe.dim(hint, True) + "\n")
            err.flush()
        node.result = executor.run_task(node.fn, node.seg, ctx, node.forwarded)
        node.state = "done"
        if status is not None:
            status.unit_finished(node.seg.task, node.result.ok)
        done[node.key] = node.result.ok
        failed = failed or not node.result.ok


def _make_status(
    err: TextIO,
    ctx_config: dict[str, Any] | None,
    capture: bool,
    estimate: _progress.Estimate | None,
    enabled: bool,
) -> _progress.StatusLine | None:
    """The run's live line — bar or pulse — or None when it can't show.

    Status is commentary, so it lives on stderr: piping stdout
    (`fm check > log`) keeps the line visible on the terminal. Applies to
    every run shape, single node included — that's `fm check`.
    """
    cfg = ctx_config or {}
    if (
        not enabled
        or capture
        or cfg.get("quiet")
        or not err.isatty()  # the status stream's own tty-ness decides
        or _plain_output(bool(cfg.get("no_color")))
    ):
        return None
    # Past the guard the run is colourful by definition (no_color/NO_COLOR/dumb
    # all bail above), so the live line always renders with escapes.
    return _progress.StatusLine(err, estimate, color=True)


def _run_parallel(nodes, real, err, capture, ctx_config, status, jobs) -> None:
    by_key = {n.key: n for n in nodes}
    lock = threading.Lock()
    failed = False
    width = max((len(n.seg.task) for n in nodes), default=0)

    def dep_ok(n: _Node) -> bool:
        return all(
            by_key[d].state == "done" and by_key[d].result and by_key[d].result.ok
            for d in n.deps
            if d in by_key
        )

    def dep_lost(n: _Node) -> bool:
        def lost(m: _Node) -> bool:
            return m.state == "skipped" or (
                m.state == "done" and bool(m.result) and not m.result.ok  # type: ignore[union-attr]
            )

        return any(lost(by_key[d]) for d in n.deps if d in by_key)

    def run_node(n: _Node) -> None:
        ctx = _make_ctx(
            n.seg,
            ctx_config,
            sequential=False,
            capture=capture,
            real=real,
            name_width=width,
            keep_going=n.keep_going,
        )
        n.result = executor.run_task(n.fn, n.seg, ctx, n.forwarded)
        if not capture:  # flush this task's buffered output as one block
            with lock:
                blob = ctx.sink.getvalue()  # type: ignore[union-attr]
                if status is not None:
                    # A direct real-stream write (bypasses the routers): the
                    # status line clears itself and tracks the column.
                    status.notify(blob)
                real.write(blob)
                real.flush()

    with ThreadPoolExecutor(max_workers=jobs if jobs > 0 else None) as pool:
        futures: dict[Any, _Node] = {}
        try:
            while True:
                for n in nodes:
                    if n.state == "pending" and (
                        dep_lost(n) or (failed and not n.keep_going)
                    ):
                        n.state = "skipped"
                        if status is not None:
                            status.unit_skipped(n.seg.task)
                for n in nodes:
                    if n.state == "pending" and dep_ok(n):
                        n.state = "running"
                        if status is not None:
                            status.unit_started(n.seg.task)
                        futures[pool.submit(run_node, n)] = n
                if not futures:
                    break
                completed, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for fut in completed:
                    node = futures.pop(fut)
                    # `run_task` catches task exceptions itself; anything the
                    # future carries (KeyboardInterrupt in the worker, an
                    # internal error) must propagate, not read as success.
                    exc = fut.exception()
                    if exc is not None:
                        raise exc
                    node.state = "done"
                    ok = bool(node.result and node.result.ok)
                    if status is not None:
                        status.unit_finished(node.seg.task, ok)
                    if not ok:
                        failed = True
                        # True fail-fast: stop launching new nodes (the skip pass
                        # above) *and* reap the FAIL-FAST siblings already in
                        # flight, so a doomed branch dies now instead of waiting
                        # out a five-minute test suite. `failfast_only` spares a
                        # keep-going task in a mixed run — it isn't doomed.
                        context.terminate_live_children(failfast_only=True)
        except BaseException:
            # Abort (Ctrl-C, or an internal error surfaced above): drop
            # everything not yet started, then kill in-flight subprocess trees.
            # This must happen *before* the pool's `with` exit joins the worker
            # threads: each is blocked in communicate() on a group-isolated child
            # that no longer receives the terminal's SIGINT, so without an
            # explicit kill the join — and the whole Ctrl-C — would hang. The app
            # layer reports "interrupted" and exits 130; run_plan's finally
            # clears the status line.
            context.terminate_live_children()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
