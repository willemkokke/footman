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

import hashlib
import inspect
import json
import os
import warnings
from pathlib import Path
from typing import Any

from footman import _describe, _paths, coerce, discover, docstrings, registry
from footman.context import context_param_name
from footman.params import suggest
from footman.registry import Group

SCHEMA_VERSION = 1


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


def param_spec(param: inspect.Parameter) -> dict[str, Any]:
    """Map one function parameter to its CLI shape (one manifest entry).

    Dynamic-completer params get a transient `_completer` key that
    `_finish` replaces with the completer's (cached) choices.
    """
    spec: dict[str, Any] = {"name": param.name.replace("_", "-")}
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
            peeled = coerce.peel(ann)  # unwrap Annotated so markers reach the spec
            tags = coerce.element_tags(peeled.element)
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

    peeled = coerce.peel(ann)
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
        if (ktags := coerce.element_tags(peeled.key)) and ktags != ["str"]:
            spec["key_types"] = ktags
        vchoices = coerce.all_choices(peeled.element)
        vtags = coerce.element_tags(peeled.element)
        if vchoices is not None:
            spec["value_choices"] = vchoices
        if vtags and vtags != ["str"] and coerce.eagerly_checkable(peeled.element):
            spec["value_types"] = vtags
        return spec

    element = peeled.element
    if coerce.is_flag(element) and not peeled.multiple:
        # Only a *scalar* bool is a --flag; `list[bool]` stays a repeatable
        # option whose tokens parse as booleans (true/false/1/0/yes/no/on/off).
        spec["kind"] = "flag"
        if not has_default and peeled.ask is None:  # ask() prompts if absent
            spec["required"] = True  # else state it explicitly: --x or --no-x
        _marker_keys(spec, peeled, param, has_default)
        return spec

    if peeled.ask is not None and not has_default:
        # ask() makes a defaultless parameter a CLI-optional option: absence is
        # filled by prompting (executor.bind), so the splitter must let it be
        # missing rather than enforce it as a required positional.
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

    choices = coerce.all_choices(element)
    tags = coerce.element_tags(element)
    if choices is not None:
        spec["choices"] = choices
    # Emit `types` only when the element is eagerly checkable — a union with a
    # custom member (`UUID | int`) can't be accept/rejected up front, so leave
    # it to binding rather than eagerly rejecting valid values.
    if tags and tags != ["str"] and coerce.eagerly_checkable(element):
        spec["types"] = tags
    elif choices is None and not tags and not isinstance(element, type):
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
    peeled: coerce.Peeled,
    param: inspect.Parameter,
    has_default: bool,
) -> None:
    """Additive manifest keys for the `Annotated` markers (path/bounds/env/doc).

    `check(fn)` deliberately never lands in the manifest — functions don't
    serialize (the same reason `_finish` strips `_completer`); it runs at
    binding time instead.
    """
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
    ctx_name = context_param_name(sig)  # the injected ctx param is not a CLI arg
    parsed = docstrings.parse(inspect.getdoc(fn))
    params = [
        _finish(param_spec(p), memo)
        for p in sig.parameters.values()
        if p.name != ctx_name
    ]
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
    if (previous := discover.shadowed(fn)) is not None:
        # Additive, and only for the rare overridden task: the options of
        # the task this one shadows, so `--help` can show the call
        # `inherited()` will make.
        node["shadows"] = {
            "params": [_finish(param_spec(p), memo) for p in _cli_params(previous)],
            "where": _source_of(previous),
        }
    if infinite:
        node["infinite"] = True  # additive: listings and help say how it ends
    if interactive:
        node["interactive"] = True  # additive: this task owns the terminal
    if confirm:
        node["confirm"] = confirm  # additive: the yes/no gate before it runs
    if parsed.long:
        node["long"] = parsed.long
    # Additive availability annotation (`when=`): the name stays listed and
    # completable either way — execution re-checks the predicate live.
    if (reason := registry.availability(fn)) is not None:
        node["disabled"] = reason
    return node


def _node(g: Group, memo: dict[int, list[str]]) -> dict[str, Any]:
    node: dict[str, Any] = {
        "help": g.help,
        "tasks": {name: _task_node(fn, memo) for name, fn in g.tasks.items()},
        "groups": {name: _node(sub, memo) for name, sub in g.groups.items()},
    }
    # A runnable group (one with `@group.default`) carries the default's option
    # surface — the same `{help, params}` shape a task node has — so the splitter
    # parses a bare `fm <group> [flags]` against it and completion/help render it.
    if g.default_task is not None:
        node["default"] = _task_node(g.default_task, memo)
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
    return {
        "schema": SCHEMA_VERSION,
        "hash": tree_hash(tree),
        "completion_max_age": completion_max_age,
        "tree": tree,
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write *manifest* to *path* atomically (never leave a half file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


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
