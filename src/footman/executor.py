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
from types import MappingProxyType
from typing import Any

from footman import coerce, context, registry
from footman.context import (
    Context,
    RunFailed,
    StepResult,
    _current,
    context_param_name,
)
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


def _wants_context(fn: Any) -> bool:
    """True when a validator accepts a second positional argument — the sibling
    parameters coerced so far. Decided by *inspecting* the signature, never by
    catching a `TypeError` from the call, so a real arity error raised inside the
    validator is not mistaken for the one-argument form."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False  # a builtin/C callable with no signature — treat as one-arg
    positional = 0
    for p in params:
        if p.kind is p.VAR_POSITIONAL:
            return True
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= 2


def _run_checks(
    value: Any, peeled: coerce.Peeled, label: str, params: dict[str, Any] | None = None
) -> Any:
    """Apply `check(fn)` validators to one coerced value (element-level).

    A validator declaring a second argument receives the sibling parameters
    already coerced (those to its left in the signature), read-only — so it can
    validate against another input, e.g. a version against the current release of
    the package named in an earlier parameter."""
    view: MappingProxyType[str, Any] | None = None
    for fn in peeled.checks:
        try:
            if _wants_context(fn):
                if view is None:
                    view = MappingProxyType(dict(params) if params else {})
                fn(value, view)
            else:
                fn(value)
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc
    return value


def _validate_value(value: Any, peeled: coerce.Peeled, label: str) -> Any:
    """Validate a value the splitter never saw (env fallback, variadic /
    passthrough token) against the constraints it would have enforced eagerly
    for a CLI token (choices, bounds, path)."""
    choices = coerce.all_choices(peeled.element)
    if choices is not None:
        shown = str(value.value) if isinstance(value, enum.Enum) else str(value)
        tags = coerce.element_tags(peeled.element)
        # A mixed union (`Literal['a','b'] | int`) accepts a choice member OR a
        # value that coerces to one of its tags — reject only when neither fits.
        type_ok = bool(tags) and coerce.coerce_scalar(str(value), tags)[0]
        if shown not in choices and not type_ok:
            extra = f", or {coerce.type_phrase(tags)}" if tags else ""
            raise ValueError(
                f"{label} must be one of {'|'.join(choices)}{extra} (got {value!r})"
            )
    if (
        peeled.bounds is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        lo, hi = peeled.bounds
        # Negated form rejects NaN (compares False to everything), matching the
        # splitter's eager bounds check; identical to </> for real numbers.
        if (lo is not None and not (value >= lo)) or (
            hi is not None and not (value <= hi)
        ):
            raise ValueError(f"{label} must be between {lo} and {hi} (got {value!r})")
    if peeled.path_req is not None and isinstance(value, PurePath):
        tests = {"exists": Path.exists, "file": Path.is_file, "dir": Path.is_dir}
        if not tests[peeled.path_req](Path(value)):
            raise ValueError(f"{label}: {value} does not satisfy {peeled.path_req}")
    return value


def _coerce_extra(
    token: str, peeled: coerce.Peeled, label: str, params: dict[str, Any] | None = None
) -> Any:
    """Coerce + validate one token the splitter never validated (an env
    fallback or a `--` passthrough value): strict coercion, then the same
    choices / bounds / path / check(fn) checks a CLI token gets."""
    try:
        value = coerce.coerce_token(token, peeled.element)
    except ValueError as exc:
        raise ValueError(f"{label} {exc}") from exc
    return _run_checks(_validate_value(value, peeled, label), peeled, label, params)


def _env_value(
    param: inspect.Parameter,
    peeled: coerce.Peeled,
    params: dict[str, Any] | None = None,
) -> Any:
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
        return _coerce_extra(token, peeled, label, params)

    if peeled.multiple:
        parts = [raw] if peeled.nosplit else [p for p in raw.split(",") if p] or [raw]
        return [one(p) for p in parts]
    return one(raw)


def _prompt_param(
    cli: str,
    peeled: coerce.Peeled,
    ctx: Context | None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Resolve a defaultless `ask()` parameter by prompting, coercing the answer
    through the same pipeline as a CLI token and re-asking on a bad value. Off a
    terminal or under `--no-input`/`--json` it raises instead — the value must
    then be supplied on the command line."""
    marker = peeled.ask
    assert marker is not None  # bind only calls this when ask() is present
    if (ctx is not None and ctx.no_input) or not context._stdin_is_tty():
        raise ValueError(
            f"--{cli} is required and nothing supplied it — pass --{cli} "
            f"(a terminal is needed to prompt; --no-input and --json never ask)."
        )
    choices = coerce.all_choices(peeled.element)
    hint = f" ({'/'.join(choices)})" if choices else ""
    text = marker.prompt or f"{cli}{hint}: "
    while True:
        raw = context._prompt_core(text, secret=marker.secret)
        if choices is not None and raw not in choices:
            out = context.real_stderr()
            out.write(f"  choose one of {', '.join(choices)}\n")
            out.flush()
            continue
        try:
            value = coerce.coerce_token(raw, peeled.element)
            return _run_checks(value, peeled, f"--{cli}", params)
        except ValueError as exc:
            out = context.real_stderr()
            out.write(f"  {exc}\n")
            out.flush()


def _left_siblings(
    sig: inspect.Signature, current: inspect.Parameter, kwargs: dict[str, Any]
) -> dict[str, Any]:
    """The effective values of the parameters to *current*'s left — a provided
    value where one was resolved, else the parameter's own default — so a
    contextual `check` reads what the body will actually receive, never a copy of
    the default that can drift out of sync."""
    view: dict[str, Any] = {}
    for p in sig.parameters.values():
        if p.name == current.name:
            break
        if p.name in kwargs:
            view[p.name] = kwargs[p.name]
        elif p.default is not inspect.Parameter.empty:
            view[p.name] = p.default
    return view


def bind(
    seg: Segment,
    fn: Task,
    ctx: Context | None = None,
    forwarded: dict[str, Any] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Turn a segment's string values into `(*args, **kwargs)` for *fn*.

    Coercion (union member selection, list handling, one-or-many collapse) goes
    through `footman.coerce`, the same module the manifest and splitter use.
    `check(fn)` validators run here on the coerced values, and absent options
    fall back to their `env()` variable before their default.

    *forwarded* carries values a dispatching task passed down via the `forward`
    marker. Precedence is CLI value > forwarded > env > default: a forwarded
    value overrides only a parameter that *has* a default (it never rescues a
    required one — a prerequisite must still be independently runnable).
    """
    sig = resolved_signature(fn)
    empty = inspect.Parameter.empty
    var_args: list[Any] = []
    kwargs: dict[str, Any] = {}

    for param in sig.parameters.values():
        # The parameters bound to this one's left, at their effective values,
        # for a contextual check(fn, params).
        siblings = _left_siblings(sig, param, kwargs)
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            extra = [*seg.variadic, *(seg.passthrough or [])]
            if param.annotation is empty:
                var_args = list(extra)
            else:
                peeled = coerce.peel(param.annotation)
                label = f"<{param.name}>"
                var_args = [_coerce_extra(v, peeled, label, siblings) for v in extra]
            continue

        cli = param.name.replace("_", "-")
        if cli not in seg.values:
            # A forwarded value overrides a defaulted parameter (never a
            # required one — the guard on `param.default`), ahead of env/default.
            if (
                forwarded is not None
                and param.name in forwarded
                and param.default is not empty
            ):
                kwargs[param.name] = forwarded[param.name]
                continue
            if param.annotation is not empty:
                peeled = coerce.peel(param.annotation)
                if peeled.env is not None:
                    value = _env_value(param, peeled, siblings)
                    if value is not _MISSING:
                        kwargs[param.name] = value
                        continue
                # ask(): prompt for a required (defaultless) param the CLI and
                # env didn't fill — CLI > env > default > prompt, so a default
                # short-circuits it and the prompt is the last resort.
                if peeled.ask is not None and param.default is empty:
                    kwargs[param.name] = _prompt_param(cli, peeled, ctx, siblings)
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
                v = _run_checks(
                    coerce.coerce_one(value, peeled.element), peeled, label, siblings
                )
                if peeled.value_multiple:
                    result.setdefault(k, []).append(v)
                else:
                    result[k] = v
            kwargs[param.name] = result
        elif peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            kwargs[param.name] = [
                _run_checks(
                    coerce.coerce_one(v, peeled.element), peeled, label, siblings
                )
                for v in items
            ]
        else:
            kwargs[param.name] = _run_checks(
                coerce.coerce_one(raw, peeled.element), peeled, label, siblings
            )

    # Positional-only params (`def build(target, /)`) cannot be passed by
    # keyword, so move the leading run of them out of kwargs into positional
    # args, in signature order. A defaultless one is splitter-enforced present,
    # so a `hole` (a skipped optional) is only ever filled by an existing
    # default and never leaves a gap before a supplied later param.
    pos: list[Any] = []
    hole: list[Any] = []
    ctx_name = context_param_name(sig)
    for param in sig.parameters.values():
        if param.kind is not inspect.Parameter.POSITIONAL_ONLY:
            break  # positional-only params always lead the signature
        if param.name == ctx_name:
            continue  # run_task injects ctx as the first positional itself
        if param.name in kwargs:
            pos += hole
            hole = []
            pos.append(kwargs.pop(param.name))
        elif param.default is not empty:
            hole.append(param.default)

    # `--` passthrough always has a home now: a task's *args, and/or the run
    # context (`passthrough()` / `ctx.passthrough`). So it is never an error.
    return [*pos, *var_args], kwargs


def forward_map(fn: Task, seg: Segment) -> dict[str, Any]:
    """The `forward`-marked parameter values *fn* passes to what it dispatches.

    Read from the segment's CLI value or the parameter's default — never by
    prompting, so building the map is side-effect free. Only defaulted
    parameters contribute; a required one is never forwarded (matching `bind`).
    """
    sig = resolved_signature(fn)
    empty = inspect.Parameter.empty
    out: dict[str, Any] = {}
    for param in sig.parameters.values():
        if param.annotation is empty or param.default is empty:
            continue
        peeled = coerce.peel(param.annotation)
        if not peeled.forward:
            continue
        cli = param.name.replace("_", "-")
        if cli not in seg.values:
            out[param.name] = param.default
            continue
        raw = seg.values[cli]
        if isinstance(raw, bool):
            out[param.name] = raw
        elif peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            out[param.name] = [coerce.coerce_one(v, peeled.element) for v in items]
        else:
            out[param.name] = coerce.coerce_one(raw, peeled.element)
    return out


def _call(
    fn: Task, args: list[Any], kwargs: dict[str, Any]
) -> tuple[int, Any, BaseException | None]:
    try:
        returned = fn(*args, **kwargs)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, None, None
    except RunFailed as exc:
        # A `run()` command failed: propagate its own exit code, not a flat 1,
        # so `fm` mirrors the command's code (docs/ci.md's "exited N" contract).
        return (exc.result.code or 1), None, exc
    except Exception as exc:  # a failed task must not crash the runner
        return 1, None, exc
    if isinstance(returned, int) and not isinstance(returned, bool):
        return returned, returned, None
    return 0, returned, None


class Unavailable(Exception):
    """A `when=`-disabled task was asked to run; the message is the reason."""


def run_task(
    fn: Task, seg: Segment, ctx: Context, forwarded: dict[str, Any] | None = None
) -> TaskResult:
    """Bind *seg* to *fn* and run it within *ctx* (contextvar set for run()).

    `ctx` is injected as the first argument if the task declares a `ctx`
    parameter. Output routing (per-task buffering for parallel/`--json`) is the
    caller's job via `ctx.sink`; here we just capture its final value.
    *forwarded* carries `forward`-marked values from a dispatching task.
    """
    # `when=` availability is re-checked live at the moment of execution —
    # the manifest's cached answer is only ever a listing annotation.
    if (reason := registry.availability(fn)) is not None:
        return TaskResult(task=seg.task, ok=False, code=2, error=Unavailable(reason))
    try:
        args, kwargs = bind(seg, fn, ctx, forwarded)
    except ChainError:
        raise  # e.g. passthrough with no *args — reported by the app layer
    except Exception as exc:  # a coercion failure (e.g. a custom-type constructor)
        return _result(seg, 2, None, exc, 0.0)

    if context_param_name(resolved_signature(fn)):
        args = [ctx, *args]  # ctx is the first positional parameter

    ctx.fn = fn  # what inherited() reads to find the shadowed task
    ctx.interactive = registry.is_interactive(fn)  # arms the prompt guard
    if ctx.cwd is None and (home := defining_dir(fn)) is not None:
        ctx.cwd = Path(home)  # run from the folder that defined the task

    token = _current.set(ctx)
    ctx.in_task = True  # a mid-body prompt()/confirm()/select() is now guarded
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
        # Honor an explicit non-zero code (run_task passes 2 for bind/coercion
        # refusals); only synthesize 1 when an error carries no code of its own.
        code=code if code != 0 else (1 if error is not None else 0),
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
