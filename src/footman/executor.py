"""Bind resolved segments to their task functions and run them.

The splitter validated the command line against the manifest and produced
string-valued `Segment` objects. Here — on the execution
path, with the user's module imported — we resolve each segment to its real
function, coerce the strings to the annotated types, and call it.

Coercion covers what the manifest grammar promises: `int`/`float`, `Path`,
`Enum`/`Literal` choices, `list[...]` (repeatable), and `*args` variadic
(which also receives anything after `--`). A task "fails" if it raises or
returns a non-zero `int` exit code; failures stop the chain unless
`--keep-going` is set.
"""

from __future__ import annotations

import inspect
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footman import coerce
from footman.context import Context, StepResult, _current, context_param_name
from footman.discover import defining_dir
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
    """Walk *path* (`["docs", "build"]`) to its task function."""
    node = root
    for name in path[:-1]:
        node = node.groups[name]
    return node.tasks[path[-1]]


def bind(seg: Segment, fn: Task) -> tuple[list[Any], dict[str, Any]]:
    """Turn a segment's string values into `(*args, **kwargs)` for *fn*.

    Coercion (union member selection, list handling, one-or-many collapse) goes
    through `footman.coerce`, the same module the manifest and splitter use.
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


def run_task(fn: Task, seg: Segment, ctx: Context) -> TaskResult:
    """Bind *seg* to *fn* and run it within *ctx* (contextvar set for run()).

    `ctx` is injected as the first argument if the task declares a `ctx`
    parameter. Output routing (per-task buffering for parallel/`--json`) is the
    caller's job via `ctx.sink`; here we just capture its final value.
    """
    try:
        args, kwargs = bind(seg, fn)
    except ChainError:
        raise  # e.g. passthrough with no *args — reported by the app layer
    except Exception as exc:  # a coercion failure (e.g. a custom-type constructor)
        return _result(seg, 2, None, exc, 0.0)

    if context_param_name(resolved_signature(fn)):
        args = [ctx, *args]  # ctx is the first positional parameter

    if ctx.cwd is None and (home := defining_dir(fn)) is not None:
        ctx.cwd = Path(home)  # run from the folder that defined the task

    token = _current.set(ctx)
    start = time.perf_counter()
    try:
        code, returned, error = _call(fn, args, kwargs)
    finally:
        _current.reset(token)
    duration = time.perf_counter() - start
    output = ctx.sink.getvalue() if isinstance(ctx.sink, io.StringIO) else ""
    return _result(seg, code, returned, error, duration, output, ctx.steps)


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
    """Run a chain sequentially (a thin shim over the DAG scheduler)."""
    from footman import schedule

    return schedule.run_plan(
        root,
        segments,
        sequential=True,
        keep_going=keep_going,
        capture=capture,
        ctx_config=ctx_config,
    )
