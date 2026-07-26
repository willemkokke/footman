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

import contextlib
import enum
import inspect
import io
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from types import MappingProxyType
from typing import Any

from footman import _binder, _globals, coerce, context, registry
from footman.context import (
    Context,
    Failed,
    Result,
    RunFailed,
    _current,
    context_param_name,
)
from footman.discover import defining_dir
from footman.manifest import resolved_signature
from footman.params import Secret
from footman.registry import Group, Task
from footman.split import ChainError, Segment

EX_USAGE = 64
"""The refusal exit code — footman did not understand the command line.

`EX_USAGE` from BSD's `sysexits.h`: an unknown task, an unknown flag, a
malformed chain, a value that will not coerce, an unavailable task. Distinct
from anything a task says on purpose, so a caller can tell a broken
invocation from a real verdict; the low codes (1, 2, …) belong to tasks and
their subprocesses. Interrupt stays 130."""


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
    steps: list[Result] = field(default_factory=list)
    cancelled: bool = False  # failed only because fail-fast killed it mid-run


def resolve(root: Group, path: list[str]) -> Task:
    """Walk *path* (`["docs", "build"]`) to its task function.

    A path that lands on a runnable group (`["lint"]`) resolves to that group's
    default action — the function `@group.default` registered.
    """
    node = root
    for name in path[:-1]:
        node = node.groups[name]
    last = path[-1]
    if last in node.tasks:
        return node.tasks[last]
    group = node.groups[last]
    if group.default_task is None:
        raise KeyError(last)  # a non-runnable group is never a segment target
    return group.default_task


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


def _decode_stdin(payload: bytes) -> str:
    """stdin bytes as text: UTF-8, universal newlines. A stream that is not
    UTF-8 is a taught refusal — bind it as `bytes` instead of guessing."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"stdin is not valid UTF-8 ({exc}) — bind the parameter as "
            f"`bytes` to read the raw stream"
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _stdin_json(payload: bytes) -> Any:
    """The JSON value on stdin — any shape; the caller owns the fit."""
    if not payload.strip():
        raise ValueError("stdin was empty; expected a JSON document")
    try:
        return json.loads(_decode_stdin(payload))
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not JSON: {exc}") from exc


def _stdin_document(payload: bytes) -> dict[str, Any]:
    """The JSON object on stdin, for a `stdin(\"field\")` parameter."""
    document = _stdin_json(payload)
    if not isinstance(document, dict):
        raise ValueError(
            f"stdin holds a JSON {type(document).__name__}, not an object — "
            f"a stdin(field) parameter reads one top-level key"
        )
    return document


def _stdin_value(
    param: inspect.Parameter,
    peeled: coerce.Peeled,
    params: dict[str, Any] | None = None,
) -> Any:
    """The stdin path for an absent option: CLI beats stdin beats env.

    The boundary read happens here (once per process, shared — see
    `context.stdin_payload`), and the value flows through the same coercion
    and validation a CLI token gets. `_MISSING` means stdin did not supply
    it: no pipe, or a JSON document without the named field — the parameter
    then falls to env, default, or the required-and-absent refusal.
    """
    marker = peeled.stdin
    assert marker is not None  # bind only calls this when stdin is present
    payload = context.stdin_payload()
    if payload is None:
        return _MISSING
    label = f"--{param.name.replace('_', '-')} (from stdin)"

    if marker.field is not None:
        value = _stdin_document(payload).get(marker.field, _MISSING)
        if value is _MISSING or value is None:  # absent and null both fall
            return _MISSING
        if isinstance(value, (dict, list)):
            raise ValueError(
                f"{label}: the {marker.field!r} field is a JSON "
                f"{type(value).__name__}, not a single value"
            )
        token = value if isinstance(value, str) else str(value)
        return _coerce_extra(token, peeled, label, params)

    if marker.lines:
        lines = _decode_stdin(payload).splitlines()
        return [_coerce_extra(line, peeled, label, params) for line in lines]

    if peeled.element is bytes:
        return _run_checks(payload, peeled, label, params)

    target = _document_shape(peeled)
    if target is not None:
        bound = _binder.bind_document(_stdin_json(payload), target, param.name)
        if isinstance(bound, list):
            return [_run_checks(v, peeled, label, params) for v in bound]
        if isinstance(bound, dict):
            return {k: _run_checks(v, peeled, label, params) for k, v in bound.items()}
        return _run_checks(bound, peeled, label, params)

    text = _decode_stdin(payload)
    return _run_checks(_validate_value(text, peeled, label), peeled, label, params)


def _document_shape(peeled: coerce.Peeled) -> Any:
    """The JSON shape a bare `stdin` parameter binds to, rebuilt from its
    peeled form: the dataclass itself, `list[T]` for a list parameter,
    `dict[K, V]` for a mapping. `None` for the text/bytes forms."""
    if peeled.mapping:
        value_t: Any = list[peeled.element] if peeled.value_multiple else peeled.element
        return dict[peeled.key, value_t]
    if peeled.multiple:
        return list[peeled.element]
    if _binder.is_document_target(peeled.element):
        return peeled.element
    return None


def _stdin_fillable(peeled: coerce.Peeled) -> bool:
    """Whether the boundary's stdin would fill this parameter — the ask()
    front-loader skips a question stdin already answers."""
    payload = context.stdin_payload()
    if payload is None:
        return False
    if peeled.stdin is not None and peeled.stdin.field is not None:
        try:
            value = _stdin_document(payload).get(peeled.stdin.field)
        except ValueError:
            return False
        return value is not None
    return True


def _prompt_param(
    cli: str,
    peeled: coerce.Peeled,
    ctx: Context | None,
    params: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """Resolve a defaultless `ask()` parameter by prompting, coercing the answer
    through the same pipeline as a CLI token and re-asking on a bad value. Off a
    terminal or under `--no-input`/`--json` it raises instead — the value must
    then be supplied on the command line. Returns `(raw, value)`: the accepted
    token and its coerced value — bind uses the value, the ask front-loader
    records the raw token so binding re-runs the one pipeline."""
    marker = peeled.ask
    assert marker is not None  # bind only calls this when ask() is present
    if (ctx is not None and ctx.no_input) or not context._stdin_is_tty():
        raise ValueError(
            f"--{cli} is required and nothing supplied it — pass --{cli} "
            f"(a terminal is needed to prompt; --no-input and --json never ask)."
        )

    def note(text: str) -> None:
        out = context.real_stderr()
        out.write(context._scrub(text) + "\n")  # echoes reflect typed input
        out.flush()

    # A *strict* live completer is a menu: its choices are law, so show them
    # as numbers rather than free text — and `Many[...]` makes it a
    # multi-select. (Secrets never menu; best-effort completers only hint.)
    if peeled.completer is not None and peeled.completer.strict and not marker.secret:
        from footman.manifest import _run_completer

        options = _run_completer(peeled.completer, {})
        if options:
            label = marker.prompt or f"{cli}:"
            while True:
                try:
                    chosen = context.select(label, options, multiple=peeled.multiple)
                except RuntimeError as exc:  # a bad number: say so, re-show
                    note(f"  {exc}")
                    continue
                try:
                    if peeled.multiple:
                        picked = [
                            _run_checks(
                                coerce.coerce_token(c, peeled.element),
                                peeled,
                                f"--{cli}",
                                params,
                            )
                            for c in chosen
                        ]
                        return ",".join(chosen), picked
                    value = coerce.coerce_token(chosen, peeled.element)
                    return chosen, _run_checks(value, peeled, f"--{cli}", params)
                except ValueError as exc:
                    note(f"  {exc}")
                    continue

    choices = coerce.all_choices(peeled.element)
    hints = choices
    if hints is None and peeled.completer is not None:
        # A best-effort completer suggests, never enforces: its values ride
        # the hint, the answer stays free text.
        from footman.manifest import _run_completer

        hints = _run_completer(peeled.completer, {}) or None
    if hints and len(hints) > 6:
        hints = [*hints[:6], "…"]
    hint = f" ({'/'.join(hints)})" if hints else ""
    text = marker.prompt or f"{cli}{hint}: "
    while True:
        raw = context._prompt_core(text, secret=marker.secret)
        if choices is not None and raw not in choices:
            note(f"  choose one of {', '.join(choices)}")
            continue
        try:
            value = coerce.coerce_token(raw, peeled.element)
            value = _run_checks(value, peeled, f"--{cli}", params)
        except ValueError as exc:
            note(f"  {exc}")
            continue
        if marker.secret:
            raw = Secret(raw)
            if isinstance(value, str):
                value = Secret(value)
        return raw, value


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


def resolve_asks(fn: Task, seg: Segment, ctx: Context | None) -> None:
    """Front-load a node's promptable `ask()` parameters — ask-serial,
    run-parallel: asked before anything runs.

    Every question whose answer cannot depend on a prerequisite's effects is
    asked up front (the scheduler calls this over the DAG in order), so the
    human answers once and walks away. A parameter carrying a live `suggest`
    completer resolves at node launch instead — its menu may need a dep's
    output — and CLI/env/default fills are skipped exactly as `bind` skips
    them. The accepted answer lands in `seg.values` as a raw token, so
    binding runs the one coercion pipeline: a front-loaded answer is
    indistinguishable from a CLI value. A required question with no way to
    ask (`--no-input`, no terminal) raises here, refusing the run before
    anything starts.
    """
    sig = resolved_signature(fn)
    empty = inspect.Parameter.empty
    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.annotation is empty:
            continue
        cli = registry.cli_name(param.name)
        if cli in seg.values:
            continue
        peeled = coerce.peel(param.annotation)
        if peeled.ask is None or param.default is not empty:
            continue
        if peeled.completer is not None:
            continue  # a live suggest may need a prerequisite's effects
        if peeled.stdin is not None and _stdin_fillable(peeled):
            continue  # the piped payload fills it at bind time
        if peeled.env is not None and os.environ.get(peeled.env) is not None:
            continue  # the env variable fills it at bind time
        raw, _value = _prompt_param(cli, peeled, ctx, None)
        seg.values[cli] = raw


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

        cli = registry.cli_name(param.name)
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
                # CLI > stdin > env > default > prompt: the piped payload
                # outranks the ambient environment, and both lose to an
                # explicit option.
                if peeled.stdin is not None:
                    value = _stdin_value(param, peeled, siblings)
                    if value is not _MISSING:
                        kwargs[param.name] = value
                        continue
                if peeled.env is not None:
                    value = _env_value(param, peeled, siblings)
                    if value is not _MISSING:
                        kwargs[param.name] = value
                        continue
                # ask(): prompt for a required (defaultless) param nothing
                # else filled — the prompt is the last resort.
                if peeled.ask is not None and param.default is empty:
                    _, kwargs[param.name] = _prompt_param(cli, peeled, ctx, siblings)
                    continue
                if peeled.stdin is not None and param.default is empty:
                    # Required, reads stdin, and nothing supplied it. A taught
                    # refusal, never a blocking terminal read.
                    if peeled.stdin.field is not None and (
                        context.stdin_payload() is not None
                    ):
                        raise ValueError(
                            f"--{cli} is required and the JSON document on "
                            f"stdin has no {peeled.stdin.field!r} field — "
                            f"add the field, or pass --{cli}"
                        )
                    if (
                        not peeled.mapping
                        and not peeled.multiple
                        and (_binder.is_document_target(peeled.element))
                    ):
                        # A whole-document parameter has no token spelling —
                        # the boundary is its only source, so teach the pipe.
                        raise ValueError(
                            f"<{param.name}> is required and reads a JSON "
                            f"document from stdin — pipe one in, or replay "
                            f"a fixture (< payload.json)"
                        )
                    raise ValueError(
                        f"--{cli} is required and reads stdin — pipe a "
                        f"document in, redirect a file (< payload), or "
                        f"pass --{cli}"
                    )
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


def forward_map(
    fn: Task, seg: Segment, received: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The `forward`-marked parameter values *fn* passes to what it dispatches.

    Read from the segment's CLI value or the parameter's default — never by
    prompting, so building the map is side-effect free. Only defaulted
    parameters contribute; a required one is never forwarded (matching `bind`).

    A value *fn* itself *received* via forwarding wins over its segment/default,
    so a forwarded value chains through a callee that re-declares the marker.
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
        if received is not None and param.name in received:
            out[param.name] = received[param.name]
            continue
        cli = registry.cli_name(param.name)
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
        # A non-int, non-None code is Python's `sys.exit("message")` idiom: the
        # object is the reason the interpreter would print to stderr. Carry it as
        # the failure error so it renders (stderr + --json) like any other failure,
        # instead of vanishing into a bare "exited with code 1". An int/None code
        # (the "fail with code N" idiom) has no message to surface.
        has_reason = not isinstance(exc.code, int) and exc.code is not None
        code = 1 if has_reason else (exc.code if isinstance(exc.code, int) else 0)
        return code, None, (exc if has_reason else None)
    except Failed as exc:
        # `footman.fail("reason", code=…)`: a deliberate stop. Honour its code and
        # carry the reason as the error, rendered verbatim (see _app / context).
        return exc.code, None, exc
    except RunFailed as exc:
        # A `run()` command failed: propagate its own exit code, not a flat 1,
        # so `fm` mirrors the command's code (docs/ci.md's "exited N" contract).
        return (exc.result.code or 1), None, exc
    except Exception as exc:  # a failed task must not crash the runner
        return 1, None, exc
    if isinstance(returned, int) and not isinstance(returned, bool):
        # An int return is the exit-code channel — unless the signature
        # declares `Stdout[int]`, in which case the number is the document
        # (a filter like wordcount could not exist otherwise). Declaration
        # wins; a bare `-> int` keeps its long-standing meaning.
        declares, _ = coerce.emitted(resolved_signature(fn).return_annotation)
        if not declares:
            return returned, returned, None
    return 0, returned, None


class Unavailable(Exception):
    """A `@requires`-gated task was asked to run; the message is the reason."""


def resolve_cwd(fn: Task, ctx: Context) -> tuple[Path | None, bool]:
    """The task's working directory under the policy ladder, resolved once.

    Ladder: the task's own declaration — `.opts(cwd=)` over `@task(cwd=)`,
    both read through the same `getattr` an `_Opted` proxies — then the
    config default (`ctx.cwd_policy`), then `"taskfile"`. `rel` resolves the
    same way and is appended to the base. Pure path arithmetic: the
    directory need not exist at resolve time — existence errors surface
    where the path is used.

    Returns `(cwd, unmanaged)`. Under `"unmanaged"` the cwd is the live
    process cwd at task start (for the body to read) and the flag makes
    `run()` spawn children with `cwd=None`. `"taskfile"` falls back to
    `root` when the task carries no defining-dir stamp (config-mounted
    plugins); with nothing known (bare calls outside discovery) the cwd
    stays `None`, as before.
    """
    policy = registry.task_cwd(fn) or ctx.cwd_policy or "taskfile"
    rel = registry.task_rel(fn)
    if policy == "unmanaged":
        if rel:
            raise ValueError(
                "rel=… needs a managed base and cwd='unmanaged' has none — "
                "use cwd='asinvoked' for a pinned launch-directory base"
            )
        return Path.cwd(), True
    base: Path | None
    if isinstance(policy, Path):
        base = policy
    elif policy == "root":
        base = Path(ctx.root_dir) if ctx.root_dir else None
    elif policy == "asinvoked":
        base = Path(ctx.invoked_dir) if ctx.invoked_dir else Path.cwd()
    elif policy == "taskfile":
        home = defining_dir(fn)
        if home is not None:
            base = Path(home)
        else:
            base = Path(ctx.root_dir) if ctx.root_dir else None
    else:  # a config default naming an absolute path (validated at startup)
        base = Path(policy)
    if base is None:
        return None, False
    return (base / rel) if rel else base, False


@contextlib.contextmanager
def _serial_globals(ctx: Context) -> Iterator[None]:
    """A serial/exclusive body owns the real process globals.

    Sole occupancy (the lane) is what makes this safe: apply the task's
    resolved cwd with a real chdir and its env overlay onto the real
    `os.environ`, snapshot and restore both. `serial_active` flips the
    routers and guards to pass-through for the duration — this is the
    declared regime where today's conveniences are legitimate again.
    """
    ctx.serial_active = True
    saved_cwd = _globals.real_getcwd()
    saved_env = dict(os.environ)  # passthrough: serial_active is already set
    try:
        if ctx.env:
            os.environ.update(ctx.env)
        if ctx.cwd is not None and not ctx.cwd_unmanaged:
            _globals.real_chdir(ctx.cwd)
        yield
    finally:
        with contextlib.suppress(OSError):  # the saved dir may have vanished
            _globals.real_chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        ctx.serial_active = False


def run_task(
    fn: Task, seg: Segment, ctx: Context, forwarded: dict[str, Any] | None = None
) -> TaskResult:
    """Bind *seg* to *fn* and run it within *ctx* (contextvar set for run()).

    `ctx` is injected as the first argument if the task declares a `ctx`
    parameter. Output routing (per-task buffering for parallel/`--json`) is the
    caller's job via `ctx.sink`; here we just capture its final value.
    *forwarded* carries `forward`-marked values from a dispatching task.
    """
    # `@requires` availability is re-checked live at the moment of execution —
    # the manifest's cached answer is only ever a listing annotation.
    if (reason := registry.availability(fn)) is not None:
        return TaskResult(
            task=seg.task, ok=False, code=EX_USAGE, error=Unavailable(reason)
        )
    try:
        args, kwargs = bind(seg, fn, ctx, forwarded)
    except ChainError:
        raise  # e.g. passthrough with no *args — reported by the app layer
    except Exception as exc:  # a coercion failure (e.g. a custom-type constructor)
        return _result(seg, EX_USAGE, None, exc, 0.0)

    if context_param_name(resolved_signature(fn)):
        args = [ctx, *args]  # ctx is the first positional parameter

    ctx.fn = fn  # what inherited() reads to find the shadowed task
    ctx.interactive = registry.is_interactive(fn)  # arms the prompt guard
    ctx.atomic = registry.is_atomic(fn)  # its subprocesses opt out of the kill
    if ctx.cwd is None:  # a preset ctx.cwd (tests / use_context) wins
        try:
            ctx.cwd, ctx.cwd_unmanaged = resolve_cwd(fn, ctx)
        except ValueError as exc:  # e.g. rel= under an unmanaged config default
            return _result(seg, EX_USAGE, None, exc, 0.0)

    # The arbiter lane is a *scheduling* declaration, acquired here at the
    # task boundary — never mid-body, which is what keeps it deadlock-free.
    # A lineage child (serial_active inherited through a fan-out) extends
    # its ancestor's hold instead of contending with it.
    inherited = ctx.serial_active
    lane_policy = None if inherited else registry.task_lane(fn)
    console = not inherited and registry.is_interactive(fn)

    token = _current.set(ctx)
    ctx.in_task = True  # a mid-body prompt()/confirm()/select() is now guarded
    start = time.perf_counter()
    try:
        with _globals.lane(
            lane_policy, name=seg.task, inherited=inherited, console=console
        ):
            if lane_policy is not None:
                with _serial_globals(ctx):
                    code, returned, error = _call(fn, args, kwargs)
            else:
                code, returned, error = _call(fn, args, kwargs)
    finally:
        _current.reset(token)
    duration = time.perf_counter() - start
    output = ctx.sink.getvalue() if isinstance(ctx.sink, io.StringIO) else ""
    result = _result(seg, code, returned, error, duration, output, ctx.steps)
    # A task that failed while fail-fast was already aborting the run wasn't a
    # genuine failure — it was cut off. Report that honestly, not as "failed".
    if not result.ok and context._aborting.is_set():
        result.cancelled = True
    return result


def _result(
    seg: Segment,
    code: int,
    returned: Any,
    error: BaseException | None,
    duration: float,
    output: str = "",
    steps: list[Result] | None = None,
) -> TaskResult:
    return TaskResult(
        task=seg.task,
        ok=error is None and code == 0,
        # Honor an explicit non-zero code (run_task passes EX_USAGE for
        # bind/coercion refusals); only synthesize 1 when an error carries no
        # code of its own.
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
