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

import enum
import inspect
import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from footman import coerce, registry
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


_MISSING = object()


def _run_checks(value: Any, peeled: coerce.Peeled, label: str) -> Any:
    """Apply `check(fn)` validators to one coerced value (element-level)."""
    for fn in peeled.checks:
        try:
            fn(value)
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc
    return value


def _validate_env(value: Any, peeled: coerce.Peeled, label: str) -> Any:
    """Validate an env-sourced value against the constraints the splitter
    would have enforced eagerly for a CLI token (choices, bounds, path)."""
    choices, _, _ = coerce.element_choices(peeled.element)
    if choices is not None:
        shown = str(value.value) if isinstance(value, enum.Enum) else str(value)
        if shown not in choices:
            raise ValueError(
                f"{label} must be one of {'|'.join(choices)} (got {value!r})"
            )
    if (
        peeled.bounds is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        lo, hi = peeled.bounds
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ValueError(f"{label} must be between {lo} and {hi} (got {value!r})")
    if peeled.path_req is not None and isinstance(value, PurePath):
        tests = {"exists": Path.exists, "file": Path.is_file, "dir": Path.is_dir}
        if not tests[peeled.path_req](Path(value)):
            raise ValueError(f"{label}: {value} does not satisfy {peeled.path_req}")
    return value


def _env_value(param: inspect.Parameter, peeled: coerce.Peeled) -> Any:
    """The env-fallback path for an absent option: CLI beats env beats default.

    The env string flows through the same coercion, bounds, choices, and
    `check(fn)` validators a CLI token would — it just runs at binding time
    (the splitter never sees the environment).
    """
    raw = os.environ.get(peeled.env) if peeled.env is not None else None
    if raw is None:
        return _MISSING
    label = f"--{param.name.replace('_', '-')} (from ${peeled.env})"

    def one(token: str) -> Any:
        value = _validate_env(coerce.coerce_one(token, peeled.element), peeled, label)
        return _run_checks(value, peeled, label)

    if peeled.multiple:
        parts = [raw] if peeled.nosplit else [p for p in raw.split(",") if p] or [raw]
        return [one(p) for p in parts]
    return one(raw)


def bind(seg: Segment, fn: Task) -> tuple[list[Any], dict[str, Any]]:
    """Turn a segment's string values into `(*args, **kwargs)` for *fn*.

    Coercion (union member selection, list handling, one-or-many collapse) goes
    through `footman.coerce`, the same module the manifest and splitter use.
    `check(fn)` validators run here on the coerced values, and absent options
    fall back to their `env()` variable before their default.
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
            if param.annotation is not empty:
                peeled = coerce.peel(param.annotation)
                if peeled.env is not None:
                    value = _env_value(param, peeled)
                    if value is not _MISSING:
                        kwargs[param.name] = value
            continue
        raw = seg.values[cli]
        if isinstance(raw, bool):  # a flag, already resolved by the splitter
            kwargs[param.name] = raw
            continue
        if param.annotation is empty:
            kwargs[param.name] = raw
            continue

        peeled = coerce.peel(param.annotation)
        label = f"--{cli}"
        if peeled.mapping:
            result: dict[Any, Any] = {}
            for key, value in raw:
                k = coerce.coerce_one(key, peeled.key)
                v = _run_checks(coerce.coerce_one(value, peeled.element), peeled, label)
                if peeled.value_multiple:
                    result.setdefault(k, []).append(v)
                else:
                    result[k] = v
            kwargs[param.name] = result
        elif peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            kwargs[param.name] = [
                _run_checks(coerce.coerce_one(v, peeled.element), peeled, label)
                for v in items
            ]
        else:
            kwargs[param.name] = _run_checks(
                coerce.coerce_one(raw, peeled.element), peeled, label
            )

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


class Unavailable(Exception):
    """A `when=`-disabled task was asked to run; the message is the reason."""


def run_task(fn: Task, seg: Segment, ctx: Context) -> TaskResult:
    """Bind *seg* to *fn* and run it within *ctx* (contextvar set for run()).

    `ctx` is injected as the first argument if the task declares a `ctx`
    parameter. Output routing (per-task buffering for parallel/`--json`) is the
    caller's job via `ctx.sink`; here we just capture its final value.
    """
    # `when=` availability is re-checked live at the moment of execution —
    # the manifest's cached answer is only ever a listing annotation.
    if (reason := registry.availability(fn)) is not None:
        return TaskResult(task=seg.task, ok=False, code=2, error=Unavailable(reason))
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
