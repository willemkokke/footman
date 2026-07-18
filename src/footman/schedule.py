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


_SPINNER = "|/-\\"
_CLEAR = "\r\033[K"


class _Progress:
    """One live status line for a parallel run, on the *real* stdout.

    Task output is buffered per task and flushed as a block on completion;
    this line is the only thing that may write between blocks, and it always
    clears itself before a block lands. Updates are event-driven (task
    submits and completions) — no background timer thread. Enabled only on a
    TTY, outside capture mode, and not under --quiet; colour follows
    --no-color / NO_COLOR.
    """

    def __init__(self, real: TextIO, nodes: list[_Node], color: bool) -> None:
        self.real = real
        self.nodes = nodes
        self.color = color
        self.live = False
        self.ticks = 0

    def render(self) -> None:
        self.ticks += 1
        running = [n.seg.task for n in self.nodes if n.state == "running"]
        finished = sum(n.state in ("done", "skipped") for n in self.nodes)
        failures = sum(
            n.state == "done" and not (n.result and n.result.ok) for n in self.nodes
        )
        line = f"{_SPINNER[self.ticks % 4]} {finished}/{len(self.nodes)}"
        if failures:
            text = f"{failures} failed"
            if self.color:
                text = f"\033[31m{text}\033[0m"
            line += f" ({text})"
        if running:
            names = ", ".join(running[:4]) + (" ..." if len(running) > 4 else "")
            line += f"  running: {names}"
        self.real.write(f"{_CLEAR}{line}")
        self.real.flush()
        self.live = True

    def clear(self) -> None:
        if self.live:
            self.real.write(_CLEAR)
            self.real.flush()
            self.live = False


def _make_progress(
    real: TextIO,
    nodes: list[_Node],
    ctx_config: dict[str, Any] | None,
    capture: bool,
) -> _Progress | None:
    cfg = ctx_config or {}
    if capture or cfg.get("quiet") or len(nodes) < 2 or not real.isatty():
        return None
    color = not cfg.get("no_color") and "NO_COLOR" not in os.environ
    return _Progress(real, nodes, color)


def _run_parallel(nodes, real, keep_going, capture, ctx_config) -> None:
    by_key = {n.key: n for n in nodes}
    lock = threading.Lock()
    failed = False
    progress = _make_progress(real, nodes, ctx_config, capture)

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
                if progress is not None:
                    progress.clear()
                real.write(ctx.sink.getvalue())  # type: ignore[union-attr]
                real.flush()
                if progress is not None:
                    progress.render()

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
                if progress is not None:
                    with lock:
                        progress.render()
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
            if progress is not None:
                with lock:
                    progress.clear()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            if progress is not None:
                with lock:
                    progress.clear()
