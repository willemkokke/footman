"""Manifest generation and caching — the "cold" path.

The manifest is a JSON description of the command tree (groups, tasks, and the
CLI shape of every parameter). The execution path imports the user's tasks
module anyway, so introspecting the tree and rewriting the cache is effectively
free. The completion hot path (`footman._complete`) only ever *reads* the
cached JSON — it never imports this module or the user's code.

Parameter mapping (function signature -> CLI shape):

| Signature                | CLI shape                                 |
| ------------------------ | ----------------------------------------- |
| `fix: bool = False`      | flag `--fix` / `--no-fix`                 |
| `mode: str = "loose"`    | option `--mode VALUE`                     |
| `env: Literal[...]`      | completable, eagerly-validated choices    |
| `count: int = 100`       | typed option, validated at parse time     |
| `paths: list[Path] = ()` | repeatable option (`--paths a --paths b`) |
| `template: Path`         | required positional (exact arity)         |
| `*cmd: str`              | variadic trailing passthrough             |
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import warnings
from pathlib import Path
from typing import Any

from footman import _binder, _coerce, _describe, _discover, _paths, docstrings, registry
from footman.context import context_param_name
from footman.params import suggest
from footman.registry import Group

SCHEMA_VERSION = 2


class ManifestError(Exception):
    """A tasks file describes a command surface footman cannot honour.

    Raised at manifest-build time (the execution path) with a taught message;
    the app layer reports it and exits 2.
    """


class CompleterError(ManifestError):
    """A strict dynamic completer failed while refreshing its choices.

    Raised so a broken completer surfaces as a taught error instead of
    silently baking an empty choice list — which would disable the very
    validation `strict=True` promises.
    """


class SpecError(ManifestError):
    """A parameter's markers are inconsistent (e.g. `env()` with no default)."""


def resolved_signature(fn: Any) -> inspect.Signature:
    """Signature of *fn* with string annotations evaluated to real types.

    `from __future__ import annotations` (and any PEP 563 usage) turns a
    tasks file's annotations into strings; `eval_str` turns them back into the
    types the grammar reasons about. Falls back to the raw signature if a name
    cannot be resolved (e.g. a type defined in a local scope).
    """
    try:
        return inspect.signature(fn, eval_str=True)
    except (NameError, TypeError, AttributeError):
        return inspect.signature(fn)


def call_signature(fn: Any) -> inspect.Signature:
    """The signature a Python caller binds against: the declared one minus the
    context parameter. A body call never passes `ctx` — `run_bound` injects it
    at the task boundary — so binding a call's arguments against the declared
    signature would land the first positional value in the `ctx` slot.
    """
    sig = resolved_signature(fn)
    name = context_param_name(sig)
    if name is None:
        return sig
    return sig.replace(
        parameters=[p for p in sig.parameters.values() if p.name != name]
    )


def _unique_globals(root: Group) -> list[Any]:
    """The tree's plugin globals, deduped by identity, contribution order.

    The same singleton pulled through two routes is one option; a name clash
    between two different singletons was refused at discovery, before the
    manifest could bake either."""
    seen: list[Any] = []
    for opt in root.contributions.get("globals", ()):
        if not any(o is opt for o in seen):
            seen.append(opt)
    return seen


def _global_spec(opt: Any, memo: dict[int, list[str]]) -> dict[str, Any]:
    """One manifest entry for a plugin's global option — described by the
    same machinery as a task parameter, so choices, path-typed file
    completion and `suggest()` come along by construction."""
    synthetic = inspect.Parameter(
        opt.name.replace("-", "_"),
        inspect.Parameter.KEYWORD_ONLY,
        annotation=opt.annotation,
        default=opt.default if opt.annotation is not bool else bool(opt.default),
    )
    spec = _finish(param_spec(synthetic), memo)
    spec["name"] = opt.name  # the cli spelling is the identity
    if opt.help:
        spec["help"] = opt.help
    spec["owner"] = opt.owner
    return spec


def param_spec(param: inspect.Parameter) -> dict[str, Any]:
    """Map one function parameter to its CLI shape (one manifest entry).

    Dynamic-completer params get a transient `_completer` key that
    `_finish` replaces with the completer's (cached) choices.
    """
    spec: dict[str, Any] = {"name": registry.cli_name(param.name)}
    ann = param.annotation
    empty = inspect.Parameter.empty

    if param.kind is inspect.Parameter.VAR_KEYWORD:
        raise SpecError(
            f"**{param.name} is not supported — declare named parameters, or "
            f"accept KEY=VALUE pairs with a dict[str, str] parameter"
        )

    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        spec["kind"] = "variadic"
        if ann is not empty:
            peeled = _coerce.peel(ann)  # unwrap Annotated so markers reach the spec
            tags = _coerce.element_tags(peeled.element)
            if tags and tags != ["str"]:
                spec["types"] = tags
            _marker_keys(spec, peeled, param, has_default=False)
        return spec

    has_default = param.default is not empty
    if has_default:
        # Bake the default into the manifest when it survives the JSON
        # coercion mirror (Path → str, Enum → value, …) — an additive key for
        # help, the catalog, and the markdown exporter. An exotic default is
        # simply omitted, never an error.
        ok_default, encoded = _describe.jsonable(param.default)
        if ok_default:
            spec["default"] = encoded
    # A keyword-only parameter (after `*` or `*args`) is an option by
    # Python's own declaration — defaultless, it is a *required* option,
    # the same shape defaultless dicts and flags already take.
    kw_only = param.kind is inspect.Parameter.KEYWORD_ONLY

    if ann is empty:
        if isinstance(param.default, bool):
            spec["kind"] = "flag"
        elif has_default or kw_only:
            spec["kind"] = "option"
            if not has_default:
                spec["required"] = True
        else:
            spec["kind"] = "argument"
        return spec

    peeled = _coerce.peel(ann)
    if peeled.mapping:
        # A dict is always an option (--name KEY=VALUE); when it has no default
        # it is a *required* option — footman has no positional-mapping syntax.
        spec["kind"] = "option"
        spec["mapping"] = True
        if not has_default:
            spec["required"] = True
        _marker_keys(spec, peeled, param, has_default)
        if peeled.nosplit:
            spec["nosplit"] = True
        if (ktags := _coerce.element_tags(peeled.key)) and ktags != ["str"]:
            spec["key_types"] = ktags
        vchoices = _coerce.all_choices(peeled.element)
        vtags = _coerce.element_tags(peeled.element)
        if vchoices is not None:
            spec["value_choices"] = vchoices
        if vtags and vtags != ["str"] and _coerce.eagerly_checkable(peeled.element):
            spec["value_types"] = vtags
        return spec

    element = peeled.element
    if (
        peeled.stdin is not None
        and peeled.stdin.field is None
        and not peeled.stdin.lines
        and not peeled.multiple
        and dataclasses.is_dataclass(element)
        and isinstance(element, type)
    ):
        # A whole-document parameter is boundary-only, not a CLI surface: a
        # document is not one token, and exploding it into per-field flags
        # founders on nesting. The splitter and completion both key on the
        # known kinds, so `"stdin"` is invisible to them by construction;
        # help still lists it, with the shape it binds.
        spec["kind"] = "stdin"
        spec["shape"] = element.__name__
        _marker_keys(spec, peeled, param, has_default)
        return spec

    if _coerce.is_flag(element) and not peeled.multiple:
        # Only a *scalar* bool is a --flag; `list[bool]` stays a repeatable
        # option whose tokens parse as booleans (true/false/1/0/yes/no/on/off).
        spec["kind"] = "flag"
        # ask() prompts if absent; stdin fills at the boundary — either one
        # makes a defaultless flag satisfiable without the command line.
        if not has_default and peeled.ask is None and peeled.stdin is None:
            spec["required"] = True  # else state it explicitly: --x or --no-x
        _marker_keys(spec, peeled, param, has_default)
        return spec

    if peeled.optional:
        # Arg[T]: an optional trailing positional — greedy for one token when
        # present, running on the default when absent (`+` says "absent, next
        # task"). Deterministic by construction: no name-peeking, cap one.
        if not has_default:
            raise SpecError(
                f"<{param.name}>: Arg[…] needs a default — an optional "
                f"positional's absence must mean something"
            )
        if peeled.multiple:
            raise SpecError(
                f"<{param.name}>: Arg[…] takes at most one token; a "
                f"many-valued positional is already optionalable as "
                f"`Many[…]` with a default"
            )
        spec["kind"] = "argument"
        spec["optional"] = True
    elif (peeled.ask is not None or peeled.stdin is not None) and not has_default:
        # ask() and stdin both make a defaultless parameter a CLI-optional
        # option: absence is filled at the boundary (a prompt, the piped
        # payload) or refused with a taught message there, so the splitter
        # must let it be missing rather than enforce it as a required
        # positional.
        spec["kind"] = "option"
    elif has_default or kw_only:
        spec["kind"] = "option"
        if not has_default:
            spec["required"] = True
    else:
        spec["kind"] = "argument"
    _marker_keys(spec, peeled, param, has_default)
    if peeled.multiple:
        spec["multiple"] = True
        if peeled.nosplit:
            spec["nosplit"] = True
    if peeled.completer is not None:
        spec["dynamic"] = {"strict": peeled.completer.strict}
        spec["choices"] = []
        spec["_completer"] = peeled.completer
        return spec

    choices = _coerce.all_choices(element)
    tags = _coerce.element_tags(element)
    if choices is not None:
        spec["choices"] = choices
    # Emit `types` only when the element is eagerly checkable — a union with a
    # custom member (`UUID | int`) can't be accept/rejected up front, so leave
    # it to binding rather than eagerly rejecting valid values.
    if tags and tags != ["str"] and _coerce.eagerly_checkable(element):
        spec["types"] = tags
    elif choices is None and not tags and not isinstance(element, type):
        if isinstance(element, str) and "stdin" in element:
            # `eval_str` failed for the whole signature, so the marker never
            # even peeled — but the annotation names it, and a silently
            # text-degraded stdin parameter would be a mystery at bind time.
            raise SpecError(
                f"<{param.name}>: annotation {element!r} did not resolve — "
                f"a stdin payload type and everything it names must be "
                f"module-level, where `eval_str` can see them"
            )
        # The annotation resolves to nothing footman can coerce (a string
        # that never resolved, a value, an exotic generic): values will pass
        # through as plain text. Silent degrade is a debugging tax — say so.
        warnings.warn(
            f"footman: parameter {param.name!r}: annotation {element!r} is "
            f"not a usable type; values are passed through as text",
            stacklevel=2,
        )
    return spec


def _marker_keys(
    spec: dict[str, Any],
    peeled: _coerce.Peeled,
    param: inspect.Parameter,
    has_default: bool,
) -> None:
    """Additive manifest keys for the `Annotated` markers (path/bounds/env/doc).

    `check(fn)` deliberately never lands in the manifest — functions don't
    serialize (the same reason `_finish` strips `_completer`); it runs at
    binding time instead.
    """
    if peeled.ask is not None and peeled.ask.secret:
        # A secret parameter never publishes values: no baked choices, no
        # completer run — the flag completes, its value stays yours.
        spec["secret"] = True
    if peeled.doc is not None:
        spec["doc"] = peeled.doc
    if peeled.path_req is not None:
        spec["path"] = peeled.path_req
    if peeled.bounds is not None:
        lo, hi = peeled.bounds
        if lo is not None:
            spec["min"] = lo
        if hi is not None:
            spec["max"] = hi
    if peeled.env is not None:
        if spec.get("mapping"):
            raise SpecError(
                f"<{param.name}>: env() is not supported on dict parameters"
            )
        if not has_default:
            raise SpecError(
                f"<{param.name}>: env({peeled.env!r}) needs a default — an "
                f"env fallback makes the parameter optional, so it needs "
                f"somewhere to fall"
            )
        spec["env"] = peeled.env
    if peeled.stdin is not None:
        marker = peeled.stdin
        if spec.get("mapping") and (marker.field is not None or marker.lines):
            raise SpecError(
                f"<{param.name}>: a dict parameter reads stdin whole (a JSON "
                f"object) — stdin(field)/stdin(lines=True) do not apply"
            )
        if marker.lines and not peeled.multiple:
            raise SpecError(
                f"<{param.name}>: stdin(lines=True) needs a list parameter — "
                f"each line binds as one element"
            )
        if marker.field is not None and peeled.multiple:
            raise SpecError(
                f"<{param.name}>: stdin({marker.field!r}) binds a single "
                f"value — a list parameter reads lines (stdin(lines=True)) "
                f"or a JSON array (bare stdin)"
            )
        if isinstance(peeled.element, str):
            raise SpecError(
                f"<{param.name}>: annotation {peeled.element!r} did not "
                f"resolve — a stdin payload type and everything it names "
                f"must be module-level, where `eval_str` can see them"
            )
        if marker.field is not None:
            spec["stdin"] = f"field:{marker.field}"
        elif marker.lines:
            spec["stdin"] = "lines"
        elif peeled.element is bytes:
            spec["stdin"] = "bytes"
        elif (
            spec.get("mapping")
            or peeled.multiple
            or spec.get("kind") == "stdin"
            or _binder.is_document_target(peeled.element)
        ):
            spec["stdin"] = "json"
        else:
            spec["stdin"] = "text"


def _run_completer(completer: suggest, memo: dict[int, list[str]]) -> list[str]:
    """Call a completer at most once per build (deduped by function identity).

    A raising *strict* completer aborts the build with `CompleterError` — its
    whole point is validation, so failing silent would validate nothing. A
    best-effort completer (`strict=False`) degrades to no candidates.
    """
    key = id(completer.fn)
    if key not in memo:
        try:
            memo[key] = [str(v) for v in completer.fn()]
        except Exception as exc:
            if completer.strict:
                name = getattr(completer.fn, "__qualname__", repr(completer.fn))
                raise CompleterError(
                    f"dynamic choices from {name}() failed: "
                    f"{type(exc).__name__}: {exc} — fix the completer, or pass "
                    f"suggest(fn, strict=False) if this data is best-effort"
                ) from exc
            memo[key] = []
    return memo[key]


def _finish(spec: dict[str, Any], memo: dict[int, list[str]]) -> dict[str, Any]:
    completer = spec.pop("_completer", None)
    if spec.get("secret"):
        spec.pop("choices", None)  # never bake a secret's values
        completer = None
    if completer is not None:
        spec["choices"] = _run_completer(completer, memo)
    return spec


def _cli_params(fn: Any):
    """The parameters that form a task's CLI (the injected ctx is not one)."""
    sig = resolved_signature(fn)
    ctx_name = context_param_name(sig)
    return [p for p in sig.parameters.values() if p.name != ctx_name]


def _source_of(fn: Any) -> str:
    code = getattr(fn, "__code__", None)
    if code is None:
        return ""
    return f"{code.co_filename}:{code.co_firstlineno}"


def _task_node(fn: Any, memo: dict[int, list[str]]) -> dict[str, Any]:
    sig = resolved_signature(fn)
    infinite = registry.is_infinite(fn)
    interactive = registry.is_interactive(fn)
    confirm = registry.task_confirm(fn)
    lane = registry.task_lane(fn)
    ctx_name = context_param_name(sig)  # the injected ctx param is not a CLI arg
    parsed = docstrings.parse(inspect.getdoc(fn))
    params = [
        _finish(param_spec(p), memo)
        for p in sig.parameters.values()
        if p.name != ctx_name
    ]
    # An optional positional must trail everything positional: a required
    # argument or a rest-consumer after it would make "which token is
    # whose" ambiguous — exactly what the grammar refuses to be.
    seen_optional: str | None = None
    for spec in params:
        if seen_optional is not None and (
            spec["kind"] == "variadic"
            or (spec["kind"] == "argument" and not spec.get("optional"))
            or (spec["kind"] == "argument" and spec.get("multiple"))
        ):
            raise SpecError(
                f"<{seen_optional}>: an Arg[…] optional positional must come "
                f"last — <{spec['name']}> follows it, so which token belongs "
                f"to whom would be a guess. Reorder, or make "
                f"<{spec['name']}> an option."
            )
        if spec["kind"] == "argument" and spec.get("optional"):
            seen_optional = spec["name"]
    for spec in params:
        if spec["name"] == "help" and spec["kind"] in ("flag", "option"):
            raise SpecError(
                "<help>: 'help' is a reserved parameter name — it maps to "
                "--help, which footman intercepts anywhere on the line to show "
                "help and never run a task, so the option could never be "
                "reached. Rename it (e.g. show_help). It is the only reserved "
                "name: every other global must come before the first task, so a "
                "task parameter may reuse it (fm deploy --json binds --json to "
                "deploy)."
            )
    known: set[str] = set()
    for spec in params:
        python_name = str(spec["name"]).replace("-", "_")
        known.add(python_name)
        # The docstring fills in; an explicit doc() marker already won.
        if "doc" not in spec and (text := parsed.params.get(python_name)):
            spec["doc"] = text
    if ctx_name:
        known.add(ctx_name)  # documenting the injected ctx param is fine
    if unknown := sorted(set(parsed.params) - known):
        warnings.warn(
            f"footman: {getattr(fn, '__name__', fn)!s}: docstring documents "
            f"unknown parameter(s): {', '.join(unknown)}",
            stacklevel=2,
        )
    node: dict[str, Any] = {"help": parsed.summary, "params": params}
    used = registry.task_uses(fn)
    if used:
        # The globals this task declares it reads — help, the catalog and
        # agents see the dependency without running anything.
        node["uses"] = [opt.name for opt in used]
    if (previous := _discover.shadowed(fn)) is not None:
        # Additive, and only for the rare overridden task: the options of
        # the task this one shadows, so `--help` can show the call
        # `inherited()` will make.
        node["shadows"] = {
            "params": [_finish(param_spec(p), memo) for p in _cli_params(previous)],
            "where": _source_of(previous),
        }
    declares, _inner = _coerce.emitted(sig.return_annotation)
    if declares:
        if interactive:
            raise SpecError(
                f"{getattr(fn, '__name__', fn)!s}: Stdout[…] and "
                f"interactive=True cannot both hold — an interactive task "
                f"owns the real terminal, uncaptured, and a declaring task's "
                f"stdout belongs to its return value. Drop one."
            )
        node["emits"] = True  # additive: this task's stdout is its return value
    if infinite:
        node["infinite"] = True  # additive: listings and help say how it ends
    if interactive:
        node["interactive"] = True  # additive: this task owns the terminal
    if lane is not None:
        node["lane"] = lane  # additive: "serial" or "exclusive" scheduling
    if confirm:
        node["confirm"] = confirm  # additive: the yes/no gate before it runs
    if parsed.long:
        node["long"] = parsed.long
    # Additive availability annotation (`@requires`): the name stays listed and
    # completable either way — execution re-checks the predicate live.
    if (reason := registry.availability(fn)) is not None:
        node["disabled"] = reason
    return node


def _hide(node: dict[str, Any], own: bool | None, inherited: bool) -> dict[str, Any]:
    """Stamp the resolved `hidden` onto a task node: its own answer if it gave
    one, otherwise the group's. Additive — absent means listed."""
    if inherited if own is None else own:
        node["hidden"] = True
    return node


def _node(g: Group, memo: dict[int, list[str]], hidden: bool = False) -> dict[str, Any]:
    # `hidden` is resolved here, where the tree structure is: a node that never
    # declared one inherits its group's answer, so hiding a subtree is said once
    # at the root and `hidden=False` on a child is a real way back. The group's
    # own answer may come from its default action — hiding what a bare
    # `fm <group>` runs hides the group it speaks for.
    own = g.hidden
    if own is None and g.default_task is not None:
        own = registry.declared_hidden(g.default_task)
    mine = hidden if own is None else own
    node: dict[str, Any] = {
        "help": g.help,
        "tasks": {
            name: _hide(_task_node(fn, memo), registry.declared_hidden(fn), mine)
            for name, fn in g.tasks.items()
        },
        "groups": {name: _node(sub, memo, mine) for name, sub in g.groups.items()},
    }
    if mine:
        node["hidden"] = True  # additive: listings skip it, the address still runs
    # A runnable group (one with `@group.default`) carries the default's option
    # surface — the same `{help, params}` shape a task node has — so the splitter
    # parses a bare `fm <group> [flags]` against it and completion/help render it.
    # The fan-out flag rides beside it (not inside: `_task_node` memoises task
    # specs, and fan-out is a property of this default, not of the function)
    # so listings can *say* what an undocumented default does.
    if g.default_task is not None:
        node["default"] = _hide(
            _task_node(g.default_task, memo),
            registry.declared_hidden(g.default_task),
            mine,
        )
        node["default_fanout"] = registry.fans_out(g.default_task)
    return node


def tree_hash(tree: dict[str, Any]) -> str:
    """Stable hash of the tree's structure (names, params, help)."""
    blob = json.dumps(tree, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    root: Group, *, completion_max_age: int | None = None
) -> dict[str, Any]:
    """Introspect *root* into a serialisable manifest dict.

    Dynamic completers run here (once each, deduped) — this is the execution
    path, so paying to refresh their cached choices is free. *completion_max_age*
    (seconds, or `None` to disable) is baked in so the stdlib-only completion hot
    path can decide whether to trigger a background refresh without reading config.
    """
    tree = _node(root, {})
    memo: dict[int, list[str]] = {}
    tree["globals"] = [_global_spec(opt, memo) for opt in _unique_globals(root)]
    return {
        "schema": SCHEMA_VERSION,
        "hash": tree_hash(tree),
        "completion_max_age": completion_max_age,
        "tree": tree,
    }


_REPLACE_ATTEMPTS = 25
_REPLACE_PAUSE = 0.04  # ≈1s of retries in all


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write *manifest* to *path* atomically (never leave a half file).

    Windows refuses to replace a destination another process holds open, and
    a reader holding it open is the *design* here: a completion poll reads
    this file every few milliseconds while a detached refresh rewrites it.
    Losing that race silently means the rebuild never lands — the caller is
    a background child that swallows its errors — so the file stays stale
    until some later write happens to find a quiet moment.

    Hence a bounded retry: about a second of attempts, then give up without
    leaving the temp file behind. POSIX never takes this path (rename over
    an open file is fine), and no caller waits longer than it already did.
    """
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                # Out of patience: clean up rather than litter the cache
                # directory with `<name>.<pid>.tmp` files nobody reads.
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(_REPLACE_PAUSE)


def load_manifest(path: Path) -> dict[str, Any] | None:
    """Read a cached manifest, or `None` if missing/unreadable/corrupt."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sync_manifest(
    root: Group,
    key_dir: Path,
    *,
    completion_max_age: int | None = None,
    tasks_file: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Build the fresh manifest and rewrite the cache only on a hash change.

    Called on the execution path, which has already paid to import the tree.
    The cache is keyed by *key_dir* (the cwd), since the effective task set is
    the cascade from the repo root down. The hash guard avoids needless disk
    writes (and mtime churn) when nothing about the command surface changed — a
    changed *completion_max_age* also forces a rewrite so a config edit takes
    effect.
    """
    fresh = build_manifest(root, completion_max_age=completion_max_age)
    # The directory this manifest describes, baked in (additive) so the
    # cache collector can tell a deleted project's leftovers from a living
    # one's without guessing from hashes.
    fresh["cwd"] = str(key_dir)
    if tasks_file:
        # Additive, like `cwd`: the background refresh reads it back, so a
        # branded CLI's custom filename survives a refresh it can't attend.
        fresh["tasks_file"] = tasks_file
    # `path` lets a caller key the cache file separately from the baked
    # `key_dir` — a `-f` run caches by (cwd, file) yet still bakes the cwd, so
    # the collector prunes it with the project like any other.
    path = path or _paths.manifest_path(key_dir)
    cached = load_manifest(path)
    if (
        cached is None
        or cached.get("hash") != fresh["hash"]
        or cached.get("completion_max_age") != completion_max_age
        or cached.get("cwd") != fresh["cwd"]
        or cached.get("tasks_file") != fresh.get("tasks_file")
    ):
        write_manifest(fresh, path)
    return fresh
