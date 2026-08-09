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
import contextvars
import enum
import inspect
import io
import json
import os
import threading
import time
import types as _types
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from types import MappingProxyType, SimpleNamespace
from typing import Any

from footman import _binder, _coerce, _futures, _globals, context, registry
from footman._discover import defining_dir
from footman._manifest import resolved_signature
from footman._split import ChainError, Segment
from footman.context import (
    Context,
    Failed,
    Result,
    RunFailed,
    _current,
    context_param_name,
)
from footman.params import Secret
from footman.registry import Group, Task

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
    started: float | None = None
    """When this task began, on the run's monotonic clock — the ordering key of
    the report. `None` for something that never began (an unavailable task, a
    denied confirm), which is why the report places those by cause instead."""
    state: str = ""
    """What happened, when `ok`/`code` do not say it: `"shared"` for a request
    an execution of this run had already satisfied. Empty means "read it from
    `ok`" — `reported_state` is the one place that resolves the parts into a
    single word, so a new outcome is a new value here rather than another
    boolean beside it. A cross-run cache (a plugin's business) would add
    `"cached"`; the two axes keep one word each."""
    blocked_by: str = ""
    """The task whose outcome meant this one never ran, when there was one. The
    report reads as cause then consequence: a non-run sits directly after
    whatever prevented it."""
    thread: str = ""
    """The worker the execution ran on — the pool thread's own stable name
    (`fm-worker_2`), not the task-shaped name it wore while running. Empty for
    a row that executed nothing (a `shared` row, a refusal)."""
    thread_id: int = 0
    """The OS thread id (`threading.get_native_id`) of that worker, the key a
    profiler's timeline uses. `0` when nothing executed."""
    address: str = ""
    """The row's tree-derived name — the path of requests that led to it,
    with an ordinal once a label repeats among siblings. Deterministic in
    request order as written; empty outside a managed run."""
    eligible: float | None = None
    """When this node could first have started — its last prerequisite's
    finish, on the run's monotonic clock. `started - eligible` is launch
    latency: time spent waiting for a free worker, never attributed to the
    task's own `duration`. `None` for a node with no prerequisites, and for
    anything that never ran."""
    title: str = ""
    """A reviewer's label for the row, when one set it (`@pre_record`).
    Empty means the task's name speaks for itself, as it always did."""
    seq: int | None = None
    """The request-order stamp: when this row's request was made, counted
    run-wide — plan order for scheduled segments, the written line for a
    `parallel()` block's queued calls, the call moment for body calls. The
    report's tie-break for starts landing inside the same instant; `None`
    for a row minted outside the request pipeline."""
    lane_waits: list[tuple[str, float]] = field(default_factory=list)
    """Lanes this task waited for before its body ran — `(lane, seconds)`
    rows, recorded only when the claim actually waited. The label is the
    claim's own (a named lane, `serial`, `exclusive`, `console`), so the
    report answers "what serialised this task, for how long" without
    re-running with eyes on the terminal. Never part of `duration` — the
    body had not started."""
    body_returned: Any = None
    """What the body actually returned — the value dependents and body
    callers receive, snapshotted at the body's exit. `returned` is its
    *reported* twin: a reviewer's `set_returned` rewrites that one only."""
    audit: list[context.AuditEntry] = field(default_factory=list)
    """The verdict's provenance, when the row was reviewed: the body entry
    with what the task itself produced, then a review entry per reviewer, in
    execution order. Empty for an unreviewed row — the verdict is the
    body's, no one touched it."""
    sections: list[context.Section] = field(default_factory=list)
    """Task-authored profiling: the sections, streams and marks the body
    recorded while it ran, on the run's clock. Empty for a task that
    recorded nothing — most of them."""
    after: tuple[str, ...] = ()
    """The rows this one waited for, by address — the plan's edges into this
    node, stamped by the scheduler. What a profile draws dependency arrows
    from. Empty for a root, and for a row outside a managed run."""

    @property
    def failed_at(self) -> str | None:
        """The lifecycle moment the failure came from, None on success — the
        same derived reading a step's `Result` offers."""
        return context._failed_moment(self.code, self.audit)

    @property
    def work_code(self) -> int | None:
        """The code the row carried when the failing moment began — a green
        body failed by its reviewer keeps its 0 here."""
        return context._earned_code(self.code, self.audit)


def reported_state(result: TaskResult) -> str:
    """The one word for what happened, resolved from the parts.

    `ok` and `code` stay the exit-code channel; this is the *reported*
    spelling, so a new outcome (skipped, unavailable, a cross-run cache hit)
    becomes another value here instead of another boolean on the result.
    """
    if result.state:
        return result.state
    if result.cancelled:
        return "cancelled"
    return "ok" if result.ok else "failed"


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
    value: Any, peeled: _coerce.Peeled, label: str, params: dict[str, Any] | None = None
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


def _validate_value(value: Any, peeled: _coerce.Peeled, label: str) -> Any:
    """Validate a value the splitter never saw (env fallback, variadic /
    passthrough token) against the constraints it would have enforced eagerly
    for a CLI token (choices, bounds, path)."""
    choices = _coerce.all_choices(peeled.element)
    if choices is not None:
        shown = str(value.value) if isinstance(value, enum.Enum) else str(value)
        tags = _coerce.element_tags(peeled.element)
        # A mixed union (`Literal['a','b'] | int`) accepts a choice member OR a
        # value that coerces to one of its tags — reject only when neither fits.
        type_ok = bool(tags) and _coerce.coerce_scalar(str(value), tags)[0]
        if shown not in choices and not type_ok:
            extra = f", or {_coerce.type_phrase(tags)}" if tags else ""
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
    token: str, peeled: _coerce.Peeled, label: str, params: dict[str, Any] | None = None
) -> Any:
    """Coerce + validate one token the splitter never validated (an env
    fallback or a `--` passthrough value): strict coercion, then the same
    choices / bounds / path / check(fn) checks a CLI token gets."""
    try:
        value = _coerce.coerce_token(token, peeled.element)
    except ValueError as exc:
        raise ValueError(f"{label} {exc}") from exc
    return _run_checks(_validate_value(value, peeled, label), peeled, label, params)


def _env_value(
    param: inspect.Parameter,
    peeled: _coerce.Peeled,
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
        return _container(peeled, [one(p) for p in parts], label)
    return one(raw)


def _bind_group(
    group: _coerce.Group,
    parts: list[str],
    many: bool,
    label: str,
) -> Any:
    """Group a flat stream by a declared arity and build each group.

    Commas and repetition both feed one stream; the arity says where each
    group ends. Nothing is inferred — the arity is declared, so a leftover
    is a refusal rather than a rounding.
    """
    size = group.total
    if many:
        if len(parts) % size:
            raise ValueError(
                f"{label} takes values in groups of {size} ({group.label()}) "
                f"— got {len(parts)}, which leaves {len(parts) % size} over"
            )
        chunks = [parts[i : i + size] for i in range(0, len(parts), size)]
    else:
        # One group, so the count settles a variable arity on its own: any
        # length between required and total is unambiguous here, which is
        # why optional constructor parameters are allowed bare and refused
        # inside a container.
        if not group.required <= len(parts) <= size:
            want = (
                f"{group.label()}"
                if group.required == size
                else f"{group.required} to {size} values ({group.label()})"
            )
            raise ValueError(f"{label} takes {want} — got {len(parts)}")
        chunks = [parts]

    built = []
    for chunk in chunks:
        values = []
        for index, token in enumerate(chunk):
            slot = group.names[index] if group.names else f"value {index + 1}"
            try:
                values.append(_coerce.coerce_checked(token, group.types[index]))
            except ValueError as exc:
                raise ValueError(f"{label}: {slot} {exc}") from exc
        built.append(group.build(values))
    return built if many else built[0]


def _container(peeled: _coerce.Peeled, values: list[Any], label: str = "") -> Any:
    """The collection the annotation named. Every collection shares a list's
    grammar and differs only here — handing back a list would give the body a
    container its annotation does not name.

    A set of an unhashable element is the one way this can fail, and it fails
    at the annotation rather than at the value, so it is taught rather than
    raised as a bare `TypeError` from deep inside binding."""
    if peeled.container is list:
        return values
    try:
        return peeled.container(values)
    except TypeError as exc:  # set[T] where T does not hash
        name = getattr(peeled.element, "__name__", peeled.element)
        raise ValueError(
            f"{label} cannot be a {peeled.container.__name__} of {name}: "
            f"{name} is not hashable"
        ) from exc


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
    peeled: _coerce.Peeled,
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
            return _container(
                peeled, [_run_checks(v, peeled, label, params) for v in bound], label
            )
        if isinstance(bound, dict):
            return {k: _run_checks(v, peeled, label, params) for k, v in bound.items()}
        return _run_checks(bound, peeled, label, params)

    if (group := _coerce.group_of(peeled.element)) is not None:
        # A fixed-arity shape with no field names — a plain `tuple[X, Y]`. A
        # JSON array is its grouped stream in another dress, so `[1, 2]` fills
        # it exactly as `--v=1,2` does. A *named* record never reaches here:
        # `is_document_target` claims it above and binds it from an object,
        # which is the spelling its field names earn it.
        raw = _stdin_json(payload)
        items = raw if isinstance(raw, list) else [raw]
        # JSON's own scalars are rendered back to the text the command line
        # would have delivered, so both channels share one grouping and one
        # set of messages. It also keeps the two agreeing about `[1, 2]` for
        # a `tuple[str, str]`: `--v=1,2` binds `("1", "2")`, so this does.
        bound = _bind_group(
            group,
            [i if isinstance(i, str) else json.dumps(i) for i in items],
            peeled.multiple,
            label,
        )
        return _run_checks(bound, peeled, label, params)

    # The scalar fall-through. It used to validate without coercing, so
    # `Stdin[Colour]` refused 'red\n' but handed the body the *string*
    # 'red' when it passed — half the contract enforced, half dropped.
    # Coercing here is what the `marker.field` branch above already does.
    text = _decode_stdin(payload)
    if peeled.element in (str, Any) or peeled.element is None:
        return _run_checks(_validate_value(text, peeled, label), peeled, label, params)
    # One trailing newline is the shell's, not the value's: `echo 42 |` is
    # the ordinary way to pipe a value, and keeping the newline made every
    # validated scalar fail on it.
    return _coerce_extra(text.removesuffix("\n"), peeled, label, params)


def _document_shape(peeled: _coerce.Peeled) -> Any:
    """The JSON shape a bare `stdin` parameter binds to, rebuilt from its
    peeled form: the dataclass itself, `list[T]` for a list parameter,
    `dict[K, V]` for a mapping. `None` for the text/bytes forms."""
    if peeled.mapping:
        value_t: Any = (
            _types.GenericAlias(list, (peeled.element,))
            if peeled.value_multiple
            else peeled.element
        )
        return _types.GenericAlias(dict, (peeled.key, value_t))
    if peeled.multiple:
        return _types.GenericAlias(list, (peeled.element,))
    if _binder.is_document_target(peeled.element):
        return peeled.element
    return None


def _stdin_fillable(peeled: _coerce.Peeled) -> bool:
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
    peeled: _coerce.Peeled,
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
        from footman._manifest import _run_completer

        options = _run_completer(peeled.completer, {})
        if options:
            label = marker.prompt or f"{cli}:"
            while True:
                try:
                    # The unguarded core: this is the framework's own prompt,
                    # and bind now runs inside the managed window.
                    chosen = context._select_core(
                        label,
                        options,
                        multiple=peeled.multiple,
                        no_input=ctx is not None and ctx.no_input,
                    )
                except RuntimeError as exc:  # a bad number: say so, re-show
                    note(f"  {exc}")
                    continue
                try:
                    if peeled.multiple:
                        picked = [
                            _run_checks(
                                _coerce.coerce_token(c, peeled.element),
                                peeled,
                                f"--{cli}",
                                params,
                            )
                            for c in chosen
                        ]
                        return ",".join(chosen), picked
                    value = _coerce.coerce_token(chosen, peeled.element)
                    return chosen, _run_checks(value, peeled, f"--{cli}", params)
                except ValueError as exc:
                    note(f"  {exc}")
                    continue

    choices = _coerce.all_choices(peeled.element)
    hints = choices
    if hints is None and peeled.completer is not None:
        # A best-effort completer suggests, never enforces: its values ride
        # the hint, the answer stays free text.
        from footman._manifest import _run_completer

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
            value = _coerce.coerce_token(raw, peeled.element)
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
        peeled = _coerce.peel(param.annotation)
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
) -> tuple[list[Any], dict[str, Any], frozenset[str]]:
    """Turn a segment's string values into `(*args, **kwargs)` for *fn*.

    Coercion (union member selection, list handling, one-or-many collapse) goes
    through `footman._coerce`, the same module the manifest and splitter use.
    `check(fn)` validators run here on the coerced values, and absent options
    fall back to their `env()` variable before their default.

    *forwarded* carries values a dispatching task passed down via the `forward`
    marker. Precedence is CLI value > forwarded > env > default: a forwarded
    value overrides only a parameter that *has* a default (it never rescues a
    required one — a prerequisite must still be independently runnable).

    The third return is the **presence set**: the parameters the caller
    supplied, as opposed to the ones footman inferred. A CLI value counts (bare
    or attached — naming an option is asking for it), so does a piped `stdin`
    payload and an answered `ask()` prompt; an `env()` fallback and a default do
    not, because nobody asked for those. `Context.given` is stamped from it, and
    it is what lets a task tell "the default one, please" from "no opinion".
    """
    sig = resolved_signature(fn)
    empty = inspect.Parameter.empty
    var_args: list[Any] = []
    kwargs: dict[str, Any] = {}
    supplied: set[str] = set()

    for param in sig.parameters.values():
        # The parameters bound to this one's left, at their effective values,
        # for a contextual check(fn, params).
        siblings = _left_siblings(sig, param, kwargs)
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            extra = [*seg.variadic, *(seg.passthrough or [])]
            if param.annotation is empty:
                var_args = list(extra)
            else:
                peeled = _coerce.peel(param.annotation)
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
                peeled = _coerce.peel(param.annotation)
                # CLI > stdin > env > default > prompt: the piped payload
                # outranks the ambient environment, and both lose to an
                # explicit option.
                if peeled.stdin is not None:
                    value = _stdin_value(param, peeled, siblings)
                    if value is not _MISSING:
                        # A piped payload is the caller handing the value over,
                        # so it counts as supplied — unlike the env fallback
                        # below, which is ambient and answers for nobody.
                        supplied.add(param.name)
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
                    # Asked and answered: the caller supplied this one too,
                    # just interactively rather than on the line.
                    supplied.add(param.name)
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
        # Named on the line — with a value or bare, both of which are the
        # caller asking for this parameter rather than footman inferring it.
        supplied.add(param.name)
        raw = seg.values[cli]
        if isinstance(raw, bool):  # a flag, already resolved by the splitter
            kwargs[param.name] = raw
            continue
        if param.annotation is empty:
            # No annotation, but a basic default types the parameter anyway
            # (`port=8000` is an int here exactly as it is to a type
            # checker). Substituting the inferred type routes the value
            # through the very path an annotated one takes — same coercion,
            # same taught errors — rather than a parallel one.
            inferred = _coerce.inferred_type(param.default)
            if inferred is None:
                kwargs[param.name] = raw
                continue
            peeled = _coerce.peel(inferred)
        else:
            peeled = _coerce.peel(param.annotation)
        label = f"--{cli}"
        if peeled.mapping:
            result: dict[Any, Any] = {}
            for key, value in raw:
                k = _coerce.coerce_one(key, peeled.key)
                v = _run_checks(
                    _coerce.coerce_one(value, peeled.element), peeled, label, siblings
                )
                if peeled.value_multiple:
                    result.setdefault(k, []).append(v)
                else:
                    result[k] = v
            kwargs[param.name] = result
        elif (group := _coerce.group_of(peeled.element)) is not None:
            items = raw if isinstance(raw, list) else [raw]
            bound = _bind_group(group, items, peeled.multiple, label)
            kwargs[param.name] = _run_checks(
                _container(peeled, bound, label) if peeled.multiple else bound,
                peeled,
                label,
                siblings,
            )
        elif peeled.multiple:
            items = raw if isinstance(raw, list) else [raw]
            kwargs[param.name] = _container(
                peeled,
                [
                    _run_checks(
                        _coerce.coerce_one(v, peeled.element), peeled, label, siblings
                    )
                    for v in items
                ],
                label,
            )
        else:
            kwargs[param.name] = _run_checks(
                _coerce.coerce_one(raw, peeled.element), peeled, label, siblings
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
    return [*pos, *var_args], kwargs, frozenset(supplied)


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
        peeled = _coerce.peel(param.annotation)
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
            out[param.name] = _container(
                peeled, [_coerce.coerce_one(v, peeled.element) for v in items]
            )
        else:
            out[param.name] = _coerce.coerce_one(raw, peeled.element)
    return out


@dataclass(frozen=True)
class _PlanParam:
    param: inspect.Parameter
    peeled: _coerce.Peeled
    validates: bool  # choices / bounds / path requirement / check(fn) to enforce
    sources: bool  # stdin / env / a required ask() to consult when absent


@dataclass(frozen=True)
class _CallPlan:
    sig: inspect.Signature  # the caller's signature: ctx already stripped
    entries: tuple[_PlanParam, ...]  # in signature order


# Keyed by the body's id — one entry per decoration, alive for the process
# because the registry holds every task. Two threads racing on a first call
_CALL_PLAN = "_footman_call_plan"


def _call_plan(fn: Task) -> _CallPlan:
    """The per-task plan a bare call binds through, built on the first body
    call and memoised — a task never called from Python never pays for it.

    Stamped on the body function itself (the `_footman_*` house pattern), so
    the plan lives and dies with its function — an id-keyed module cache
    would hand a recycled id another function's plan. Two threads racing on
    a first call both build the same plan; the last write wins, harmlessly.
    """
    from footman._manifest import call_signature

    body = registry.task_body(fn)
    plan: _CallPlan | None = getattr(body, _CALL_PLAN, None)
    if plan is not None:
        return plan
    sig = call_signature(fn)
    empty = inspect.Parameter.empty
    entries: list[_PlanParam] = []
    for param in sig.parameters.values():
        if param.annotation is empty or param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        peeled = _coerce.peel(param.annotation)
        validates = bool(
            peeled.checks
            or peeled.bounds is not None
            or peeled.path_req is not None
            or _coerce.all_choices(peeled.element) is not None
        )
        sources = param.kind is not inspect.Parameter.VAR_POSITIONAL and (
            peeled.stdin is not None
            or peeled.env is not None
            or (peeled.ask is not None and param.default is empty)
        )
        if validates or sources:
            entries.append(_PlanParam(param, peeled, validates, sources))
    plan = _CallPlan(sig, tuple(entries))
    setattr(body, _CALL_PLAN, plan)
    return plan


def _validate_explicit(
    value: Any, peeled: _coerce.Peeled, label: str, siblings: dict[str, Any]
) -> None:
    """Validate a Python value a caller passed explicitly — the annotation is
    the contract however the task was asked for — without coercing it: the
    static signature already polices the types, and coercion exists because
    the command line only has strings."""

    def one(v: Any) -> None:
        if v is None:  # an Optional's explicit None: nothing to check
            return
        _run_checks(_validate_value(v, peeled, label), peeled, label, siblings)

    if peeled.mapping and isinstance(value, dict):
        for v in value.values():
            if peeled.value_multiple and isinstance(v, (list, tuple)):
                for element in v:
                    one(element)
            else:
                one(v)
    elif peeled.multiple and isinstance(value, (list, tuple)):
        for element in value:
            one(element)
    else:
        one(value)


def bind_call(
    fn: Task, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any], frozenset[str]]:
    """A body call's arguments through the same ladder `bind` runs.

    The handle sees the call before Python applies defaults, so an omitted
    parameter is distinguishable from its default passed explicitly: absence
    consults the sources binding would — stdin, then env, then (for a
    defaultless parameter) an `ask()` prompt — and an explicit value wins over
    all of them, exactly as a CLI value does. Explicit values run the
    annotation's validators but are never coerced.

    Called before the work key is computed, so identity reads the values the
    body will actually receive: a segment, a prerequisite and a body call
    that resolve to the same values are one piece of work.

    The third return is the **presence set**, the same one `bind` produces from
    a segment — so `build(profile=<the default>)` and `fm build --profile` say
    the same thing about their caller, and `build()` and `fm build` say the same
    thing about theirs. `bind_partial` is what makes it knowable: it records
    only what was passed, and it runs before `apply_defaults()` fills the rest.
    """
    plan = _call_plan(fn)
    try:
        bound = plan.sig.bind_partial(*args, **kwargs)
    except TypeError:
        # Won't bind: let the call raise where it is made, and claim nothing
        # about presence for arguments we could not match to parameters.
        return args, kwargs, frozenset()
    # Bound before anything fills a gap: the one moment on this path where what
    # the caller named is distinguishable from what footman is about to infer.
    # Read ahead of the `entries` shortcut, because a task with nothing to
    # validate and no sources to consult still has a caller with intentions.
    supplied: set[str] = set(bound.arguments)
    if not plan.entries:
        return args, kwargs, frozenset(supplied)
    name = getattr(fn, "__name__", str(fn))
    empty = inspect.Parameter.empty
    for entry in plan.entries:
        param, peeled = entry.param, entry.peeled
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            if entry.validates:
                label = f"{name}(*{param.name})"
                siblings = _left_siblings(plan.sig, param, bound.arguments)
                for element in bound.arguments.get(param.name, ()):
                    _validate_explicit(element, peeled, label, siblings)
            continue
        if param.name in bound.arguments:
            if entry.validates:
                label = f"{name}({param.name}=…)"
                siblings = _left_siblings(plan.sig, param, bound.arguments)
                _validate_explicit(bound.arguments[param.name], peeled, label, siblings)
            continue
        if not entry.sources:
            continue
        siblings = _left_siblings(plan.sig, param, bound.arguments)
        if peeled.stdin is not None:
            value = _stdin_value(param, peeled, siblings)
            if value is not _MISSING:
                supplied.add(param.name)  # piped in: handed over, not inferred
                bound.arguments[param.name] = value
                continue
        if peeled.env is not None:
            value = _env_value(param, peeled, siblings)
            if value is not _MISSING:
                # Deliberately not `supplied`: the environment is ambient, and
                # answers for nobody in particular.
                bound.arguments[param.name] = value
                continue
        if peeled.ask is not None and param.default is empty:
            cli = registry.cli_name(param.name)
            supplied.add(param.name)  # asked and answered
            _, bound.arguments[param.name] = _prompt_param(
                cli, peeled, context._current.get(), siblings
            )
    bound.apply_defaults()
    return bound.args, dict(bound.kwargs), frozenset(supplied)


def _call(
    fn: Task, args: list[Any], kwargs: dict[str, Any], as_call: bool = False
) -> tuple[int, Any, BaseException | None]:
    try:
        # The task's own body — never the handle, whose call is the body-call
        # machinery that would route this invocation straight back here.
        returned = registry.task_body(fn)(*args, **kwargs)
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
    if isinstance(returned, int) and not isinstance(returned, bool) and not as_call:
        # An int return is the exit-code channel — unless the signature
        # declares `Stdout[int]`, in which case the number is the document
        # (a filter like wordcount could not exist otherwise). Declaration
        # wins; a bare `-> int` keeps its long-standing meaning. It is a
        # *segment's* channel, though: a body call asked for the value, and
        # `n = measure()` has always handed the number over.
        declares, _ = _coerce.emitted(resolved_signature(fn).return_annotation)
        if not declares:
            return returned, returned, None
    return 0, returned, None


class Unavailable(Exception):
    """A `@requires`-gated task was asked to run; the message is the reason."""


def unavailable(fn: Task, seg: Segment) -> TaskResult | None:
    """The refusal for a `@requires`-gated task, or `None` when it may run.

    Availability is re-checked live at the moment of execution — the manifest's
    cached answer is only ever a listing annotation — and it is checked before
    binding, so an unavailable task refuses for the reason it is unavailable
    rather than for whatever its arguments would have done. Every way of asking
    a task to run comes through here, so a body call refuses exactly as a
    prerequisite does.
    """
    reason = registry.availability(fn)
    if reason is None:
        return None
    return TaskResult(
        task=seg.task,
        ok=False,
        code=EX_USAGE,
        error=Unavailable(reason),
        started=time.perf_counter(),
    )


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
def _cwd_hold(ctx: Context) -> Generator[None]:
    """The cwd lane's application: chdir to the task's resolved directory
    and restore — legitimate only under the lane's sole occupancy. The
    foreign-cwd guard then passes naturally: live and target agree."""
    saved = _globals.real_getcwd()
    try:
        if ctx.cwd is not None and not ctx.cwd_unmanaged:
            _globals.real_chdir(ctx.cwd)
        yield
    finally:
        with contextlib.suppress(OSError):
            _globals.real_chdir(saved)


@contextlib.contextmanager
def _serial_globals(ctx: Context) -> Generator[None]:
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


def bind_global_options(options: Sequence[Any], tokens: Sequence[str]) -> str | None:
    """Deliver the parsed leading globals to their owning plugin options.

    Every option gets a value for the run — a flag's presence, an option's
    coerced `=`-attached value, or its default — and freezes, so `.value`
    answers anywhere in-run. The value string runs the same coercion,
    bounds, choices and `check(fn)` pipeline a task parameter's would.
    Returns the teaching message for a value that will not _coerce.
    """
    by_flag: dict[str, Any] = {}
    for opt in options:
        by_flag.setdefault("--" + opt.name, opt)
    values: dict[str, Any] = {}
    for tok in tokens:
        name, eq, raw = tok.partition("=")
        opt = by_flag.get(name)
        if opt is None:
            continue
        if opt.annotation is bool:
            values[opt.name] = True
            continue
        if not eq and opt.bare is not None:
            # A bare mention of a value-optional option means the author's
            # `bare=`, run through the ordinary pipeline so a `bare=` that
            # would not coerce is taught, not smuggled.
            raw = str(opt.bare)
        peeled = _coerce.peel(opt.annotation)
        try:
            values[opt.name] = _coerce_extra(raw, peeled, name)
        except ValueError as exc:
            return str(exc)
    for opt in by_flag.values():
        fallback = False if opt.annotation is bool else opt.default
        opt._value = values.get(opt.name, fallback)
        opt._frozen = True
    return None


def _advise_unread_uses(ctx: Context, fn: Task) -> None:
    """`-v` only: a task that declared `uses=[OPT]` but finished without
    reading `.value` gets an advisory — a stale declaration misleads help
    and provenance, but quietly, so it never nags an ordinary run."""
    if not ctx.verbose:
        return
    for opt in registry.task_uses(fn):
        if (ctx.task or "?") not in opt._reads:
            _globals._note(
                f"global-unread:{opt.name}",
                f"task {ctx.task or '?'} declares --{opt.name} in uses= but "
                f"never read it this run — prune the declaration if it is "
                f"stale",
            )


# --- the per-task lifecycle: pre_task / post_task ----------------------------


_hook_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "footman_hook_owner", default=None
)

# The run-wide moments, which sit *outside* the run: `pre_tasks` happens at
# discovery (the manifest child included) and `post_tasks` after the plan is
# done. Both are named here while they run, so a task call from one can say
# which moment it is in rather than degrading to a bare function call.
_wide_moment: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "footman_wide_moment", default=None
)


@contextlib.contextmanager
def wide_moment(name: str) -> Any:
    """Mark a run-wide lifecycle moment for the duration of one hook."""
    token = _wide_moment.set(name)
    try:
        yield
    finally:
        _wide_moment.reset(token)


class HookFailed(RuntimeError):
    """A per-task lifecycle hook raised; the message names the plugin."""


@dataclass(frozen=True)
class _HookPlugin:
    name: str  # the defining module: what a failure names, what state keys on
    bind_pre: tuple[Task, ...]  # pre_bind hooks — before parameters exist
    pre: tuple[Task, ...]
    post: tuple[Task, ...]


@dataclass(frozen=True)
class _Lifecycle:
    inv: Any  # the frozen Invocation
    plugins: tuple[_HookPlugin, ...]
    finish: tuple[Task, ...] = ()  # post_tasks hooks, cascade order


# The run's per-task hooks, installed by the app layer for the duration of one
# invocation. A module global, not a contextvar: every pool thread and body
# call must see the same ladder. `None` — the common case — is the fast path.
_lifecycle: _Lifecycle | None = None


def install_lifecycle(inv: Any, contributions: Mapping[str, Sequence[Task]]) -> None:
    """Group the tree's per-task hooks by plugin and arm them for the run.

    A plugin is the module that defined the hook: its `pre_task` and
    `post_task` pair through that identity, its `task.state` namespace is
    private to it, and a failure names it. Plugin order is the order of first
    contribution (cascade order); posts unwind in reverse.
    """
    global _lifecycle
    grouped: dict[str, tuple[list[Task], list[Task], list[Task]]] = {}
    for kind, slot in (("pre_bind", 0), ("pre_task", 1), ("post_task", 2)):
        for hook in contributions.get(kind, ()):
            owner = getattr(hook, "__module__", None) or "<unknown>"
            grouped.setdefault(owner, ([], [], []))[slot].append(hook)
    finish = tuple(contributions.get("post_tasks", ()))
    _lifecycle = (
        _Lifecycle(
            inv,
            tuple(
                _HookPlugin(name, tuple(bind_pre), tuple(pre), tuple(post))
                for name, (bind_pre, pre, post) in grouped.items()
            ),
            finish,
        )
        if grouped or finish
        else None
    )


def clear_lifecycle() -> None:
    global _lifecycle
    _lifecycle = None


def run_post_tasks(
    results: Sequence[TaskResult], total: float, json_mode: bool
) -> BaseException | None:
    """Fire the run's `post_tasks` hooks, main thread, before the report.

    The invocation is handed the whole story — every row as a result view,
    the `skipped` subset, and the wall-clock — written past the freeze by
    the run itself (hooks still cannot write). Under `--json` a hook's
    stdout is rerouted to stderr: the envelope owns stdout. Every hook runs;
    the first failure is kept, named, and fails the invocation.
    """
    life = _lifecycle
    if life is None or not life.finish:
        return None
    inv = life.inv
    views = tuple(ResultView(r) for r in results)
    skipped = tuple(
        v for r, v in zip(results, views, strict=True) if reported_state(r) == "skipped"
    )
    object.__setattr__(inv, "results", views)
    object.__setattr__(inv, "skipped", skipped)
    object.__setattr__(inv, "total_ms", round(total * 1000, 3))
    redirect = (
        contextlib.redirect_stdout(context.real_stderr())
        if json_mode
        else contextlib.nullcontext()
    )
    first: BaseException | None = None
    with redirect, wide_moment("post_tasks"):
        for hook in life.finish:
            try:
                hook(inv)
            except Exception as exc:
                if first is None:
                    failure = HookFailed(
                        f"post_tasks hook "
                        f"{getattr(hook, '__name__', '?')!r} from "
                        f"{getattr(hook, '__module__', '?')} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    failure.__cause__ = exc
                    first = failure
    return first


class TaskHandle:
    """One task execution, as a per-task lifecycle hook sees it.

    Read-only facts — `name`, `args`, `source_hash` — plus the two lanes a
    hook may write through: `env`, the task's own environment overlay, and
    `state`, a namespace private to the current plugin and this execution.
    """

    __slots__ = ("_bound", "_ctx", "_fn", "_kwargs", "_raw_args", "_states", "name")

    _fn: Task
    _ctx: Context
    _raw_args: tuple[Any, ...] | None
    _kwargs: dict[str, Any] | None
    _bound: Mapping[str, Any] | None
    _states: dict[str, SimpleNamespace]
    name: str

    def __init__(self, fn: Task, seg: Segment, ctx: Context) -> None:
        object.__setattr__(self, "_fn", fn)
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_raw_args", None)
        object.__setattr__(self, "_kwargs", None)
        object.__setattr__(self, "_bound", None)
        object.__setattr__(self, "_states", {})
        object.__setattr__(self, "name", seg.task)

    def _bind(self, args: Sequence[Any], kwargs: dict[str, Any]) -> None:
        """Hand the handle its bound arguments — binding just happened."""
        object.__setattr__(self, "_raw_args", tuple(args))
        object.__setattr__(self, "_kwargs", dict(kwargs))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"task.{name} is read-only — per-plugin scratch goes on "
            f"task.state, per-task environment in task.env"
        )

    @property
    def args(self) -> Mapping[str, Any]:
        """The bound arguments — what the body actually receives, defaults
        included, read-only. Mutation would let a plugin silently break the
        typed contract the framework exists to enforce. Not readable at
        `pre_bind`, which runs before values exist."""
        bound: Mapping[str, Any] | None = self._bound
        if bound is None:
            raw_args, kwargs = self._raw_args, self._kwargs
            if raw_args is None or kwargs is None:  # set together by _bind
                raise RuntimeError(
                    "task.args is not readable at pre_bind — nothing is "
                    "bound yet; read the values in pre_task, the post-bind "
                    "moment"
                )
            from footman._manifest import call_signature

            try:
                b = call_signature(self._fn).bind_partial(*raw_args, **kwargs)
                b.apply_defaults()
                mapping = dict(b.arguments)
            except TypeError:  # an unbindable shape: show what was passed
                mapping = dict(kwargs)
            bound = MappingProxyType(mapping)
            object.__setattr__(self, "_bound", bound)
        return bound

    @property
    def env(self) -> dict[str, str]:
        """The task's environment overlay: `run()` merges it into every
        subprocess, in-body `os.environ` reads see it. Never the process
        globals — a parallel sibling keeps its own."""
        return self._ctx.env

    @property
    def state(self) -> SimpleNamespace:
        """Scratch shared between this plugin's pre and post: one namespace
        per (plugin, execution), invisible to every other plugin."""
        owner = _hook_owner.get()
        if owner is None:
            raise RuntimeError(
                "task.state is per-plugin, so it is only reachable inside a "
                "lifecycle hook"
            )
        states: dict[str, SimpleNamespace] = self._states
        ns = states.get(owner)
        if ns is None:
            ns = states[owner] = SimpleNamespace()
        return ns

    @property
    def source_hash(self) -> str | None:
        """`registry.task_source_hash` for this task — a tripwire, not an
        identity: `None` when the source cannot be read, and shallow by
        nature (the body's own source, nothing it calls)."""
        return registry.task_source_hash(self._fn)


class ResultView:
    """A `post_task`'s view of the result: the sealed record, read-only.

    Observers see, never judge: the review window (`pre_record`) closed
    before this view was made, and every write there — code, title, the
    reported value via `set_returned` — is attributed in the record's
    audit. An observer that finds a problem fails the task instead, loudly:
    `footman.fail(reason, code)` from a `post_task` hook is the veto, and
    the failure names the hook and the moment.
    """

    __slots__ = ("_result",)

    def __init__(self, result: TaskResult) -> None:
        object.__setattr__(self, "_result", result)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_result"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"result.{name} is read-only in post_task — observers see, never "
            f"judge. Amend verdicts in the review window instead "
            f"(pre_record, where set_returned also lives), or veto with "
            f"fail(reason, code)."
        )


def _reserved_note(
    ctx: Context, plugin: str, hook: Task, kind: str = "pre_task"
) -> None:
    sink = ctx.err_sink if ctx.err_sink is not None else context.real_stderr()
    sink.write(
        f"note: {plugin} {kind} {getattr(hook, '__name__', '?')!r} returned "
        f"a value; a pre hook's return channel is reserved (for a pre that "
        f"supplies the task's result) — keep per-task state on task.state\n"
    )


def _own_hook_error(
    hook: Any, moment: str, task_name: str, exc: Exception
) -> HookFailed:
    failure = HookFailed(
        f"{moment} hook {getattr(hook, '__name__', '?')!r} on task "
        f"{task_name!r} failed: {type(exc).__name__}: {exc}"
    )
    failure.__cause__ = exc
    return failure


def _enter_own_hooks(
    handle: TaskHandle, attr: str, moment: str
) -> BaseException | None:
    """Run the task's own hooks for one enter moment — the handle-attached
    lane, innermost: closest to the task, after any plugin's."""
    for hook in registry.own_hooks(handle._fn, attr):
        try:
            hook()
        except Exception as exc:
            return _own_hook_error(hook, moment, handle.name, exc)
    return None


def _enter_bind_hooks(
    life: _Lifecycle | None, handle: TaskHandle
) -> BaseException | None:
    """Run `pre_bind` hooks before binding: every plugin's in plugin order,
    then the task's own (innermost).

    The first failure stops the walk and fails the task — binding never
    happens, the body never runs, and the posts still fire when the attempt
    concludes.
    """
    if life is None:
        return _enter_own_hooks(handle, registry._PRE_BIND_HOOKS, "pre_bind")
    for plugin in life.plugins:
        for hook in plugin.bind_pre:
            token = _hook_owner.set(plugin.name)
            try:
                value = hook(life.inv, handle)
            except Exception as exc:
                failure = HookFailed(
                    f"pre_bind hook {getattr(hook, '__name__', '?')!r} from "
                    f"{plugin.name} failed for task {handle.name!r}: "
                    f"{type(exc).__name__}: {exc}"
                )
                failure.__cause__ = exc
                return failure
            finally:
                _hook_owner.reset(token)
            if value is not None:
                _reserved_note(handle._ctx, plugin.name, hook, kind="pre_bind")
    return _enter_own_hooks(handle, registry._PRE_BIND_HOOKS, "pre_bind")


def _enter_task_hooks(
    life: _Lifecycle | None, handle: TaskHandle
) -> BaseException | None:
    """Run `pre_task` hooks: every plugin's in plugin order, then the task's
    own (innermost — plugins are the wider audience, the task's own hooks
    nest closest to the body).

    The first failure stops the walk and fails the task — the body will not
    run. It does not gate the posts: a post is the task-finished event, so
    once an execution reached this moment, every registered `post_task`
    fires when it concludes, irrespective of which pres its plugin
    registered or how any of them fared.
    """
    if life is None:
        return _enter_own_hooks(handle, registry._PRE_TASK_HOOKS, "pre_task")
    for plugin in life.plugins:
        for hook in plugin.pre:
            token = _hook_owner.set(plugin.name)
            try:
                value = hook(life.inv, handle)
            except Exception as exc:
                failure = HookFailed(
                    f"pre_task hook {getattr(hook, '__name__', '?')!r} from "
                    f"{plugin.name} failed for task {handle.name!r}: "
                    f"{type(exc).__name__}: {exc}"
                )
                failure.__cause__ = exc
                return failure
            finally:
                _hook_owner.reset(token)
            if value is not None:
                _reserved_note(handle._ctx, plugin.name, hook)
    return _enter_own_hooks(handle, registry._PRE_TASK_HOOKS, "pre_task")


def _exit_task_hooks(
    life: _Lifecycle | None,
    handle: TaskHandle,
    result: TaskResult,
) -> BaseException | None:
    """Unwind `post_task` hooks: the task's own first (innermost), then
    every plugin's in reverse plugin order; every one runs.

    The first failure is kept (it fails an otherwise-green task); later
    hooks still unwind, context-manager style. A `fail()` from any of them
    is the veto — its own code, an "observe" audit entry.
    """
    view = ResultView(result)
    first: BaseException | None = None
    for hook in registry.own_hooks(handle._fn, registry._POST_TASK_HOOKS):
        name = getattr(hook, "__name__", "?")
        try:
            hook(view)
        except context.Failed as exc:
            if not result.audit:
                result.audit.append(
                    context._audit_entry("body", result.task, result.code)
                )
            result.audit.append(context._audit_entry("observe", name, exc.code))
            if first is None:
                first = exc
        except Exception as exc:
            if not result.audit:
                result.audit.append(
                    context._audit_entry("body", result.task, result.code)
                )
            result.audit.append(context._audit_entry("observe", name, None))
            if first is None:
                first = _own_hook_error(hook, "post_task", handle.name, exc)
    if life is not None:
        for plugin in reversed(life.plugins):
            for hook in plugin.post:
                token = _hook_owner.set(plugin.name)
                name = getattr(hook, "__name__", "?")
                try:
                    hook(life.inv, handle, view)
                except context.Failed as exc:
                    # The veto: fail(reason, code) from an observer is a
                    # deliberate failure that rides the error channel — loud,
                    # attributed, its own code (never 0; fail() refuses that).
                    # The audit records the moment; the work's own story stays.
                    if not result.audit:
                        result.audit.append(
                            context._audit_entry("body", result.task, result.code)
                        )
                    result.audit.append(context._audit_entry("observe", name, exc.code))
                    if first is None:
                        first = exc
                except Exception as exc:
                    if not result.audit:
                        result.audit.append(
                            context._audit_entry("body", result.task, result.code)
                        )
                    result.audit.append(context._audit_entry("observe", name, None))
                    if first is None:
                        failure = HookFailed(
                            f"post_task hook {name!r} "
                            f"from {plugin.name} failed for task {handle.name!r}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        failure.__cause__ = exc
                        first = failure
                finally:
                    _hook_owner.reset(token)
    return first


def run_task(
    fn: Task, seg: Segment, ctx: Context, forwarded: dict[str, Any] | None = None
) -> TaskResult:
    """Bind *seg* to *fn* and run it within *ctx* (contextvar set for run()).

    `ctx` is injected as the first argument if the task declares a `ctx`
    parameter. Output routing (per-task buffering for parallel/`--json`) is the
    caller's job via `ctx.sink`; here we just capture its final value.
    *forwarded* carries `forward`-marked values from a dispatching task.
    """
    if (refusal := unavailable(fn, seg)) is not None:
        return refusal
    # The managed window opens before binding: with `ctx` current and
    # `in_task` set, a `pre_bind` hook's `task.env` writes reach `env()`
    # fallbacks, coercion and `check(fn)` validators through the environ
    # router — `_env_value` reads `os.environ`, and the router serves the
    # merged view. The guards ride the same window, so hook code answers to
    # the same rules a body does; the framework's own prompts use the real
    # stream and are never caught.
    token = _current.set(ctx)
    ctx.in_task = True
    life = _lifecycle
    own = registry.has_own_hooks(fn)
    handle = TaskHandle(fn, seg, ctx) if life is not None or own else None
    try:
        if (
            handle is not None
            and (hook_error := _enter_bind_hooks(life, handle)) is not None
        ):
            # A raising pre_bind fails the task; binding never happens, the
            # body never runs — but the attempt concluded, so the posts fire.
            result = _result(seg, 1, None, hook_error, 0.0)
            _exit_task_hooks(life, handle, result)
            return result
        try:
            args, kwargs, ctx.given = bind(seg, fn, ctx, forwarded)
        except ChainError:
            raise  # e.g. passthrough with no *args — reported by the app layer
        except Exception as exc:  # a coercion failure (custom-type constructor)
            result = _result(seg, EX_USAGE, None, exc, 0.0)
            if handle is not None:
                # A bind failure still concluded the attempt: the posts fire
                # (a bind-time span needs closing), with the refusal result.
                _exit_task_hooks(life, handle, result)
            return result
        return run_bound(fn, seg, ctx, args, kwargs, handle=handle)
    finally:
        _current.reset(token)


def run_bound(
    fn: Task,
    seg: Segment,
    ctx: Context,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    as_call: bool = False,
    handle: TaskHandle | None = None,
) -> TaskResult:
    """Run *fn* with arguments already resolved — everything after binding.

    The half of `run_task` that owns a task's *execution*: the context wiring,
    the arbiter lane, the body, and the result. Split out because a body call
    (`artifact = build()`) arrives with real Python arguments and must not be
    re-bound from a command line, yet still deserves the full task treatment —
    its own context, its own lane decision, its own `TaskResult`.
    """
    # Sharing means the same thing whichever way a task was reached, so this
    # request joins the same protocol a body call uses: claim the work, or wait
    # for whoever already holds it and report the answer as `shared`. A request
    # declared `shared=False` never joins — `ctx` carries that answer. The key
    # is computed before `ctx` joins the arguments, so the context can never
    # become part of the work's identity.
    # `as_call` means the cell layer is already holding this work's cell (it
    # claimed before delegating here) and will resolve it — claiming again from
    # the same thread would read as this task waiting on itself.
    work = _futures.work_of(fn, args, kwargs) if ctx.shared and not as_call else None
    claimed, cell = _futures.claim(work, seg.task)
    if not claimed:
        # The pair is per request — only the body is shared. The pre fires
        # here, post-bind and before the wait, so a span honestly covers it;
        # the post closes the request with its row, and `result.state` says
        # whether this request executed or was satisfied by another.
        life = _lifecycle
        if life is not None or registry.has_own_hooks(fn):
            if handle is None:
                handle = TaskHandle(fn, seg, ctx)
            handle._bind(args, kwargs)
            if (enter_error := _enter_task_hooks(life, handle)) is not None:
                result = _result(seg, 1, None, enter_error, 0.0)
                _exit_task_hooks(life, handle, result)
                return result
        try:
            value = _futures.join(cell)
        except BaseException as exc:  # the execution we waited on failed
            result = _result(seg, 1, None, exc, 0.0)
            # Genuine prevention: the failure this request waited on is why
            # it has no answer — named, so the report seats it after it.
            result.blocked_by = cell.label
        else:
            result = _futures.shared_result(seg.task, value, cell.record)
            result.address = ctx.address  # the reference row is this request's
        if handle is not None:
            post_error = _exit_task_hooks(life, handle, result)
            if post_error is not None and result.ok:
                result.ok = False
                result.code = (
                    post_error.code if isinstance(post_error, context.Failed) else 1
                )
                result.error = post_error
                result.state = ""  # it no longer reads as a clean share
        return result

    plain_args = args  # the caller-visible arguments, before ctx is injected
    if context_param_name(resolved_signature(fn)):
        args = [ctx, *args]  # ctx is the first positional parameter

    ctx.fn = fn  # what inherited() reads to find the shadowed task
    ctx.interactive = registry.is_interactive(fn)  # arms the prompt guard
    ctx.atomic = registry.is_atomic(fn)  # its subprocesses opt out of the kill
    if ctx.cwd is None:  # a preset ctx.cwd (tests / use_context) wins
        try:
            ctx.cwd, ctx.cwd_unmanaged = resolve_cwd(fn, ctx)
        except ValueError as exc:  # e.g. rel= under an unmanaged config default
            _futures.resolve(cell, None, exc)  # never leave a waiter blocked
            return _result(seg, EX_USAGE, None, exc, 0.0)

    # The arbiter lane is a *scheduling* declaration, acquired here at the
    # task boundary — never mid-body, which is what keeps it deadlock-free.
    # A lineage child (serial_active inherited through a fan-out) extends
    # its ancestor's hold instead of contending with it.
    inherited = ctx.serial_active
    lane_policy = None if inherited else registry.task_lane(fn)
    named = () if inherited else registry.task_lanes(fn)
    console = not inherited and (
        registry.is_interactive(fn) or any(ln.name == "console" for ln in named)
    )
    named = tuple(ln for ln in named if ln.name != "console")

    # Wear the task's name while it runs, so a sampling profiler's timeline
    # reads as tasks rather than `fm-worker_3`; a serial/exclusive hold is
    # badged, so lane occupancy shows. Restored in the finally below; a body
    # call nests naturally (the callee's name while the callee runs). The
    # worker's own stable name and OS id land on the result, the correlation
    # keys a profiler dump uses.
    worker = threading.current_thread()
    born = worker.name
    badge = {"serial": " [serial]", "exclusive": " [exclusive]"}.get(
        lane_policy or "", ""
    )
    worker.name = f"fm:{seg.task}{badge}"

    token = _current.set(ctx)
    ctx.in_task = True  # a mid-body prompt()/confirm()/select() is now guarded
    start = time.perf_counter()
    started = start  # the report's ordering key: when this task actually began
    life = _lifecycle
    hook_error: BaseException | None = None
    if life is not None or registry.has_own_hooks(fn):
        # The handle rides in from run_task (declared path) or the futures
        # layer (a body call); a direct run_bound caller gets a fresh one.
        # Its arguments are the pre-injection values: ctx is machinery, not
        # a value a hook should read back as one of the task's own. Pre
        # hooks run inside the timed span, so their cost is the task's cost.
        if handle is None:
            handle = TaskHandle(fn, seg, ctx)
        handle._bind(plain_args, kwargs)
        hook_error = _enter_task_hooks(life, handle)
    else:
        handle = None
    lane_waits: list[tuple[str, float]] = []
    try:
        error: BaseException | None
        if hook_error is not None:
            # A raising pre fails the task like a failed prerequisite: the
            # body never runs, and the failure names the plugin.
            code, returned, error = 1, None, hook_error
        else:
            with _globals.lane(
                lane_policy,
                name=seg.task,
                inherited=inherited,
                console=console,
                named=named,
            ) as lane_waits:
                if lane_policy is not None:
                    with _serial_globals(ctx):
                        code, returned, error = _call(fn, args, kwargs, as_call)
                elif any(ln.name == "cwd" for ln in named):
                    # The cwd lane's hold: sole occupancy of the one real
                    # working directory, applied with a real chdir for the
                    # duration — the fourth exit in the foreign-cwd taught
                    # list, now a first-class claim.
                    with _cwd_hold(ctx):
                        code, returned, error = _call(fn, args, kwargs, as_call)
                else:
                    code, returned, error = _call(fn, args, kwargs, as_call)
        if error is None:
            # The body finished and had every chance to read what it declared.
            _advise_unread_uses(ctx, fn)
    finally:
        _current.reset(token)
        worker.name = born
    duration = time.perf_counter() - start
    output = ctx.sink.getvalue() if isinstance(ctx.sink, io.StringIO) else ""
    result = _result(seg, code, returned, error, duration, output, ctx.steps)
    result.address = ctx.address
    result.body_returned = returned
    result.started = started
    result.thread = born
    result.thread_id = threading.get_native_id()
    result.lane_waits = lane_waits
    result.sections = list(ctx.sections)
    reviewers = registry.task_reviewers(fn)
    if reviewers and hook_error is None:
        # The row's review window: the body concluded, the record is still a
        # draft, and the task's own reviewers see it before it is sealed,
        # observed, or reported. Reviewers run inside-out; the first failure
        # stops the walk and fails the task with the hook's own error —
        # nothing a broken review half-did is kept.
        audit = [context._audit_entry("body", seg.task, result.code)]
        view = context.ResultView(
            title=seg.task,
            code=result.code,
            stdout=result.output,
            stderr="",
            duration=result.duration,
            raw="",
            command=seg.task,
            returned=result.returned,
        )
        for reviewer in reviewers:
            name = getattr(reviewer, "__name__", repr(reviewer))
            try:
                reviewer(view)
            except Exception as exc:
                audit.append(context._audit_entry("review", name, None))
                failure = HookFailed(
                    f"pre_record hook {name!r} failed reviewing task "
                    f"{seg.task!r}: {type(exc).__name__}: {exc}"
                )
                failure.__cause__ = exc
                result.ok = False
                if result.code == 0:
                    result.code = 1
                result.error = failure
                error = failure  # a waiter shares the failure, not a value
                break
            audit.append(
                context._audit_entry(
                    "review", name, view.code if "code" in view._touched else None
                )
            )
        else:
            # Every reviewer finished: the verdict is what the review left.
            result.code = view.code
            result.ok = result.error is None and view.code == 0
            if "title" in view._touched:
                result.title = view.title
            if "returned" in view._touched:
                # The *reported* value only: the body's return was
                # snapshotted at the body's exit and resolves below.
                result.returned = view.returned
        result.audit = audit
    if handle is not None:
        post_error = _exit_task_hooks(life, handle, result)
        if post_error is not None and result.ok:
            # An observer that crashed must not pass silently: the task
            # fails, named, exactly as a raising pre would have failed it.
            # A deliberate veto (fail() from the hook) keeps its own code —
            # never 0, fail() refuses that spelling.
            result.ok = False
            result.code = (
                post_error.code if isinstance(post_error, context.Failed) else 1
            )
            result.error = post_error
            error = post_error
    # The body's return, snapshotted the moment it was handed over: what
    # a dependent or a body-caller receives is what the annotation promised,
    # whatever a reporter later does to the *reported* result. Resolved after
    # the posts, so a hook-failed task never hands a waiter a green value; a
    # `set_returned` cannot reach it, because `returned` was captured at
    # `_call` exit. Only a shared execution holds a cell (`claim` gives an
    # unshared one none), so what a run reuses never depends on scheduling
    # order.
    _futures.resolve(cell, returned, error, record=result)
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
    from footman import _schedule

    return _schedule.run_plan(
        root,
        segments,
        sequential=True,
        keep_going=keep_going,
        capture=capture,
        ctx_config=ctx_config,
    )
