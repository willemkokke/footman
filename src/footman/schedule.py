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
    is_infinite,
    is_interactive,
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


def _default_seg(fn: Task) -> Segment:
    return Segment(task=fn.__name__, path=[fn.__name__])


def _build_dag(root: Group, segments: list[Segment]) -> list[_Node]:
    """Nodes for the chain plus their transitive pre/post deps.

    Explicit chain segments each get their own node — repeating a task in the
    chain (`build web build api`) runs it once per mention. Only shared
    pre/post prerequisites are deduped, by task identity, so a prerequisite
    pulled in twice still runs once. Node keys are serial ints; `dep_nodes`
    maps a task to the node its bare deps resolve to.
    """
    nodes: list[_Node] = []
    dep_nodes: dict[int, _Node] = {}
    counter = count()
    seen_explicit: set[int] = set()

    def new_node(fn: Task, seg: Segment) -> _Node:
        node = _Node(fn, seg, next(counter))
        nodes.append(node)
        return node

    def add_dep(fn: Task) -> _Node:
        node = dep_nodes.get(id(fn))
        if node is None:
            node = new_node(fn, _default_seg(fn))
            dep_nodes[id(fn)] = node
            _link(node)
        return node

    def _link(node: _Node) -> None:
        for dep in getattr(node.fn, "_footman_pre", []):
            node.deps.add(add_dep(dep).key)
        for dep in getattr(node.fn, "_footman_post", []):
            add_dep(dep).deps.add(node.key)

    for seg in segments:
        fn = executor.resolve(root, seg.path)
        existing = dep_nodes.get(id(fn))
        if existing is not None and id(fn) not in seen_explicit:
            # First explicit mention of a task already pulled in as a bare dep:
            # adopt this segment's args instead of creating a duplicate.
            existing.seg = seg
            seen_explicit.add(id(fn))
            continue
        node = new_node(fn, seg)
        if existing is None:
            dep_nodes[id(fn)] = node
        seen_explicit.add(id(fn))
        _link(node)
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
) -> context.Context:
    ctx = context.Context(**(ctx_config or {}), passthrough=list(seg.passthrough or []))
    ctx.sink = None if (sequential and not capture) else io.StringIO()
    # Step lines dress for their *destination*: a buffered block replays
    # onto `real`, so its children style exactly as parallel() children
    # style for their parent's terminal — both engines, one look. Only
    # liveness (sink is None, judged in run()) gates in-place rewrites.
    ctx.tty = not capture and real.isatty() and not _plain_output(ctx.no_color)
    ctx.task = seg.task
    ctx.name_width = name_width
    return ctx


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
    keep_going: bool = False,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
    estimate: _progress.Estimate | None = None,
    progress: bool = True,
    jobs: int = 0,
) -> list[executor.TaskResult]:
    """Build and run the DAG; return results in dependency order."""
    segments, denied = _gate_confirms(root, segments, ctx_config)
    nodes = _build_dag(root, segments)
    _check_cycles(nodes)
    # One node has nothing to parallelise — run it on the sequential-live
    # path instead: output streams as it happens, and run()'s TTY mode
    # (colour, in-place step rewrite) applies. `fm check` is this shape. An
    # interactive task also forces sequential: it owns the terminal, so it
    # can't share with parallel siblings (a human-wait is the bottleneck).
    sequential = (
        sequential or len(nodes) == 1 or any(is_interactive(n.fn) for n in nodes)
    )
    # A run containing an infinite task has no progress to show — its
    # duration isn't late, it's intentional. The status line yields to a
    # one-time hint (printed at the node's start) saying how this ends.
    endless = any(is_infinite(n.fn) for n in nodes)
    with context.routing() as (real, err):
        status = _make_status(
            err, ctx_config, capture, estimate, progress and not endless
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
                _run_sequential(
                    nodes, real, keep_going, capture, ctx_config, status, hint_err
                )
            else:
                _run_parallel(
                    nodes, real, err, keep_going, capture, ctx_config, status, jobs
                )
        finally:
            if status is not None:
                context.set_status(None)
                status.close()
    return denied + [n.result for n in _toposort(nodes) if n.result is not None]


def _run_sequential(
    nodes, real, keep_going, capture, ctx_config, status, err=None
) -> None:
    done: dict[int, bool] = {}
    failed = False
    width = max((len(n.seg.task) for n in nodes), default=0)
    for node in _toposort(nodes):
        if any(not done.get(d) for d in node.deps) or (failed and not keep_going):
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
        )
        if status is not None:
            status.unit_started(node.seg.task)
        if err is not None and is_infinite(node.fn):
            hint = f"{node.seg.task} runs until you stop it — Ctrl-C"
            err.write(_describe.dim(hint, True) + "\n")
            err.flush()
        node.result = executor.run_task(node.fn, node.seg, ctx)
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


def _run_parallel(
    nodes, real, err, keep_going, capture, ctx_config, status, jobs
) -> None:
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
        )
        n.result = executor.run_task(n.fn, n.seg, ctx)
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
                        dep_lost(n) or (failed and not keep_going)
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
        except BaseException:
            # Abort (Ctrl-C, or an internal error surfaced above): drop
            # everything not yet started; the pool's exit joins the in-flight
            # tasks (on Ctrl-C their subprocesses got the terminal's SIGINT
            # too). The app layer reports "interrupted" and exits 130; the
            # status line is cleared by run_plan's finally.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
