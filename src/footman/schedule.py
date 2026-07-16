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
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, TextIO

from footman import context, executor
from footman.registry import Group, Task
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
    """Nodes for the chain plus their transitive pre/post deps (deduped)."""
    nodes: dict[int, _Node] = {}
    order: list[int] = []

    def add(fn: Task, seg: Segment, explicit: bool) -> _Node:
        key = id(fn)
        if key in nodes:
            if explicit:
                nodes[key].seg = seg  # a chain segment's args beat a bare dep
            return nodes[key]
        node = _Node(fn, seg, key)
        nodes[key] = node
        order.append(key)
        for dep in getattr(fn, "_footman_pre", []):
            node.deps.add(add(dep, _default_seg(dep), False).key)
        for dep in getattr(fn, "_footman_post", []):
            add(dep, _default_seg(dep), False).deps.add(key)
        return node

    for seg in segments:
        add(executor.resolve(root, seg.path), seg, True)
    return [nodes[k] for k in order]


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


def _make_ctx(
    seg: Segment,
    ctx_config: dict[str, Any] | None,
    *,
    sequential: bool,
    capture: bool,
    real: TextIO,
) -> context.Context:
    ctx = context.Context(**(ctx_config or {}), passthrough=list(seg.passthrough or []))
    ctx.sink = None if (sequential and not capture) else io.StringIO()
    ctx.tty = sequential and not capture and real.isatty()
    return ctx


def run_plan(
    root: Group,
    segments: list[Segment],
    *,
    sequential: bool = False,
    keep_going: bool = False,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
) -> list[executor.TaskResult]:
    """Build and run the DAG; return results in dependency order."""
    nodes = _build_dag(root, segments)
    _check_cycles(nodes)
    with context.routing() as real:
        if sequential:
            _run_sequential(nodes, real, keep_going, capture, ctx_config)
        else:
            _run_parallel(nodes, real, keep_going, capture, ctx_config)
    return [n.result for n in _toposort(nodes) if n.result is not None]


def _run_sequential(nodes, real, keep_going, capture, ctx_config) -> None:
    done: dict[int, bool] = {}
    failed = False
    for node in _toposort(nodes):
        if any(not done.get(d) for d in node.deps) or (failed and not keep_going):
            node.state = "skipped"
            continue
        ctx = _make_ctx(
            node.seg, ctx_config, sequential=True, capture=capture, real=real
        )
        node.result = executor.run_task(node.fn, node.seg, ctx)
        node.state = "done"
        done[node.key] = node.result.ok
        failed = failed or not node.result.ok


def _run_parallel(nodes, real, keep_going, capture, ctx_config) -> None:
    by_key = {n.key: n for n in nodes}
    lock = threading.Lock()
    failed = False

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
        ctx = _make_ctx(n.seg, ctx_config, sequential=False, capture=capture, real=real)
        n.result = executor.run_task(n.fn, n.seg, ctx)
        if not capture:  # flush this task's buffered output as one block
            with lock:
                real.write(ctx.sink.getvalue())  # type: ignore[union-attr]
                real.flush()

    with ThreadPoolExecutor() as pool:
        futures: dict[Any, _Node] = {}
        try:
            while True:
                for n in nodes:
                    if n.state == "pending" and (
                        dep_lost(n) or (failed and not keep_going)
                    ):
                        n.state = "skipped"
                for n in nodes:
                    if n.state == "pending" and dep_ok(n):
                        n.state = "running"
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
                    if not (node.result and node.result.ok):
                        failed = True
        except BaseException:
            # Abort (Ctrl-C, or an internal error surfaced above): drop
            # everything not yet started; the pool's exit joins the in-flight
            # tasks (on Ctrl-C their subprocesses got the terminal's SIGINT
            # too). The app layer reports "interrupted" and exits 130.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
