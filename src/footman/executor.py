"""Bind resolved segments to their task functions and run them.

The splitter validated the command line against the manifest and produced
string-valued :class:`~footman.split.Segment` objects. Here — on the execution
path, with the user's module imported — we resolve each segment to its real
function, coerce the strings to the annotated types, and call it.

Coercion covers what the manifest grammar promises: ``int``/``float``, ``Path``,
``Enum``/``Literal`` choices, ``list[...]`` (repeatable), and ``*args`` variadic
(which also receives anything after ``--``). A task "fails" if it raises or
returns a non-zero ``int`` exit code; failures stop the chain unless
``--keep-going`` is set.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from footman import coerce
from footman.context import Context, StepResult, _current, context_param_name
from footman.manifest import resolved_signature
from footman.registry import Group, Task
from footman.split import ChainError, Segment


@dataclass
class TaskResult:
    """Outcome of running one segment."""

    task: str
    ok: bool
    code: int = 0
    returned: Any = None
    error: BaseException | None = None
    duration: float = 0.0
    output: str = ""
    steps: list[StepResult] = field(default_factory=list)


def resolve(root: Group, path: list[str]) -> Task:
    """Walk *path* (``["docs", "build"]``) to its task function."""
    node = root
    for name in path[:-1]:
        node = node.groups[name]
    return node.tasks[path[-1]]


def bind(seg: Segment, fn: Task) -> tuple[list[Any], dict[str, Any]]:
    """Turn a segment's string values into ``(*args, **kwargs)`` for *fn*.

    Coercion (union member selection, list handling, one-or-many collapse) goes
    through :mod:`footman.coerce`, the same module the manifest and splitter use.
    """
    sig = resolved_signature(fn)
    empty = inspect.Parameter.empty
    var_args: list[Any] = []
    kwargs: dict[str, Any] = {}

    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            element = str if param.annotation is empty else param.annotation
            extra = [*seg.variadic, *(seg.passthrough or [])]
            var_args = [coerce.coerce_one(v, element) for v in extra]
            continue

        cli = param.name.replace("_", "-")
        if cli not in seg.values:
            continue
        raw = seg.values[cli]
        if isinstance(raw, bool):  # a flag, already resolved by the splitter
            kwargs[param.name] = raw
            continue
        if param.annotation is empty:
            kwargs[param.name] = raw
            continue

        peeled = coerce.peel(param.annotation)
        if peeled.mapping:
            result: dict[Any, Any] = {}
            for key, value in raw:
                k = coerce.coerce_one(key, peeled.key)
                v = coerce.coerce_one(value, peeled.element)
                if peeled.value_multiple:
                    result.setdefault(k, []).append(v)
                else:
                    result[k] = v
            kwargs[param.name] = result
        elif peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            kwargs[param.name] = [coerce.coerce_one(v, peeled.element) for v in items]
        else:
            kwargs[param.name] = coerce.coerce_one(raw, peeled.element)

    # `--` passthrough always has a home now: a task's *args, and/or the run
    # context (`passthrough()` / `ctx.passthrough`). So it is never an error.
    return var_args, kwargs


def _call(
    fn: Task, args: list[Any], kwargs: dict[str, Any]
) -> tuple[int, Any, BaseException | None]:
    try:
        returned = fn(*args, **kwargs)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, None, None
    except Exception as exc:  # a failed task must not crash the runner
        return 1, None, exc
    if isinstance(returned, int) and not isinstance(returned, bool):
        return returned, returned, None
    return 0, returned, None


def run_segment(
    root: Group,
    seg: Segment,
    *,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
) -> TaskResult:
    """Resolve, bind, and run a single segment within a fresh run context.

    A :class:`~footman.context.Context` is set on the contextvar around the call
    (so ``run()`` finds it) and injected as the first argument if the task
    declares a ``ctx`` parameter. With *capture*, the task's output is collected
    for the ``--json`` report (fd-level, so subprocesses are captured too).
    """
    fn = resolve(root, seg.path)
    try:
        args, kwargs = bind(seg, fn)
    except ChainError:
        raise  # e.g. passthrough with no *args — reported by the app layer
    except Exception as exc:  # a coercion failure (e.g. a custom-type constructor)
        return _result(seg, 2, None, exc, 0.0)

    ctx = Context(**(ctx_config or {}), passthrough=list(seg.passthrough or []))
    ctx.report = not capture  # under --json, record steps but don't print
    if context_param_name(resolved_signature(fn)):
        args = [ctx, *args]  # ctx is the first positional parameter

    token = _current.set(ctx)
    try:
        if capture:
            code, returned, error, duration, output = _call_captured(fn, args, kwargs)
        else:
            start = time.perf_counter()
            code, returned, error = _call(fn, args, kwargs)
            duration, output = time.perf_counter() - start, ""
    finally:
        _current.reset(token)

    return _result(seg, code, returned, error, duration, output, ctx.steps)


def _call_captured(
    fn: Task, args: list[Any], kwargs: dict[str, Any]
) -> tuple[int, Any, BaseException | None, float, str]:
    py_buffer = io.StringIO()
    with tempfile.TemporaryFile(mode="w+") as fd_buffer:
        saved_out, saved_err = os.dup(1), os.dup(2)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(fd_buffer.fileno(), 1)
            os.dup2(fd_buffer.fileno(), 2)
            start = time.perf_counter()
            with (
                contextlib.redirect_stdout(py_buffer),
                contextlib.redirect_stderr(py_buffer),
            ):
                code, returned, error = _call(fn, args, kwargs)
            duration = time.perf_counter() - start
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)
        fd_buffer.seek(0)
        fd_output = fd_buffer.read()
    return code, returned, error, duration, py_buffer.getvalue() + fd_output


def _result(
    seg: Segment,
    code: int,
    returned: Any,
    error: BaseException | None,
    duration: float,
    output: str = "",
    steps: list[StepResult] | None = None,
) -> TaskResult:
    return TaskResult(
        task=seg.task,
        ok=error is None and code == 0,
        code=code if error is None else 1,
        returned=returned,
        error=error,
        duration=duration,
        output=output,
        steps=steps or [],
    )


def run_chain(
    root: Group,
    segments: list[Segment],
    *,
    keep_going: bool = False,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
) -> list[TaskResult]:
    """Run segments in order, stopping at the first failure unless keep_going."""
    results: list[TaskResult] = []
    for seg in segments:
        result = run_segment(root, seg, capture=capture, ctx_config=ctx_config)
        results.append(result)
        if not result.ok and not keep_going:
            break
    return results
