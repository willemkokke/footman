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
from dataclasses import dataclass
from typing import Any

from footman import coerce
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
    has_var_positional = False

    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
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
        if peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            coerced = [coerce.coerce_one(v, peeled.element) for v in items]
            single = peeled.multiple == "one_or_many" and len(items) == 1
            kwargs[param.name] = coerced[0] if single else coerced
        else:
            kwargs[param.name] = coerce.coerce_one(raw, peeled.element)

    if seg.passthrough is not None and not has_var_positional:
        raise ChainError(
            f"{seg.task}: nothing after `--` can be forwarded "
            f"(the task has no *args to receive passthrough)"
        )
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


def run_segment(root: Group, seg: Segment, *, capture: bool = False) -> TaskResult:
    """Resolve, bind, and run a single segment, recording the outcome.

    With *capture*, the task's output is collected into the result so the
    ``--json`` report stays pure machine-readable output. Capture works at two
    levels: Python-level ``sys.stdout``/``stderr`` (so it composes with pytest
    and captures the task's own ``print``s) and the underlying file descriptors
    (so a subprocess the task spawns is captured too).
    """
    fn = resolve(root, seg.path)
    args, kwargs = bind(seg, fn)
    if not capture:
        start = time.perf_counter()
        code, returned, error = _call(fn, args, kwargs)
        return _result(seg, code, returned, error, time.perf_counter() - start)

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

    return _result(
        seg, code, returned, error, duration, py_buffer.getvalue() + fd_output
    )


def _result(
    seg: Segment,
    code: int,
    returned: Any,
    error: BaseException | None,
    duration: float,
    output: str = "",
) -> TaskResult:
    return TaskResult(
        task=seg.task,
        ok=error is None and code == 0,
        code=code if error is None else 1,
        returned=returned,
        error=error,
        duration=duration,
        output=output,
    )


def run_chain(
    root: Group,
    segments: list[Segment],
    *,
    keep_going: bool = False,
    capture: bool = False,
) -> list[TaskResult]:
    """Run segments in order, stopping at the first failure unless keep_going."""
    results: list[TaskResult] = []
    for seg in segments:
        result = run_segment(root, seg, capture=capture)
        results.append(result)
        if not result.ok and not keep_going:
            break
    return results
