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

from footman import _paths, coerce
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

    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        spec["kind"] = "variadic"
        if ann is not empty and (tags := coerce.element_tags(ann)) and tags != ["str"]:
            spec["types"] = tags
        return spec

    has_default = param.default is not empty
    if ann is empty:
        if isinstance(param.default, bool):
            spec["kind"] = "flag"
        else:
            spec["kind"] = "option" if has_default else "argument"
        return spec

    peeled = coerce.peel(ann)
    if peeled.mapping:
        spec["kind"] = "option" if has_default else "argument"
        spec["mapping"] = True
        _marker_keys(spec, peeled, param, has_default)
        if peeled.nosplit:
            spec["nosplit"] = True
        if (ktags := coerce.element_tags(peeled.key)) and ktags != ["str"]:
            spec["key_types"] = ktags
        vchoices, _, _ = coerce.element_choices(peeled.element)
        if vchoices is not None:
            spec["value_choices"] = vchoices
        elif (vtags := coerce.element_tags(peeled.element)) and vtags != ["str"]:
            spec["value_types"] = vtags
        return spec

    element = peeled.element
    if coerce.is_flag(element) and not peeled.multiple:
        # Only a *scalar* bool is a --flag; `list[bool]` stays a repeatable
        # option whose tokens parse as booleans (true/false/1/0/yes/no/on/off).
        spec["kind"] = "flag"
        _marker_keys(spec, peeled, param, has_default)
        return spec

    spec["kind"] = "option" if has_default else "argument"
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

    choices, _, _ = coerce.element_choices(element)
    if choices is not None:
        spec["choices"] = choices
    elif (tags := coerce.element_tags(element)) and tags != ["str"]:
        spec["types"] = tags
    elif not tags and not isinstance(element, type):
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
    """Additive manifest keys for the `Annotated` markers (path/bounds/env).

    `check(fn)` deliberately never lands in the manifest — functions don't
    serialize (the same reason `_finish` strips `_completer`); it runs at
    binding time instead.
    """
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


def _task_node(fn: Any, memo: dict[int, list[str]]) -> dict[str, Any]:
    sig = resolved_signature(fn)
    ctx_name = context_param_name(sig)  # the injected ctx param is not a CLI arg
    return {
        "help": (inspect.getdoc(fn) or "").partition("\n")[0],
        "params": [
            _finish(param_spec(p), memo)
            for p in sig.parameters.values()
            if p.name != ctx_name
        ],
    }


def _node(g: Group, memo: dict[int, list[str]]) -> dict[str, Any]:
    return {
        "help": g.help,
        "tasks": {name: _task_node(fn, memo) for name, fn in g.tasks.items()},
        "groups": {name: _node(sub, memo) for name, sub in g.groups.items()},
    }


def _source_files(g: Group, seen: set[str] | None = None) -> list[str]:
    """Every distinct file that defines a task in the tree (for staleness)."""
    seen = set() if seen is None else seen
    for fn in g.tasks.values():
        code = getattr(fn, "__code__", None)
        if code is not None:
            seen.add(code.co_filename)
    for sub in g.groups.values():
        _source_files(sub, seen)
    return sorted(seen)


def _stat_record(path: str) -> dict[str, Any] | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return {"path": path, "mtime": st.st_mtime, "size": st.st_size}


def tree_hash(tree: dict[str, Any]) -> str:
    """Stable hash of the tree's structure (names, params, help)."""
    blob = json.dumps(tree, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_manifest(root: Group) -> dict[str, Any]:
    """Introspect *root* into a serialisable manifest dict.

    Dynamic completers run here (once each, deduped) — this is the execution
    path, so paying to refresh their cached choices is free.
    """
    tree = _node(root, {})
    sources = [rec for p in _source_files(root) if (rec := _stat_record(p))]
    return {
        "schema": SCHEMA_VERSION,
        "hash": tree_hash(tree),
        "sources": sources,
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


def is_stale(manifest: dict[str, Any]) -> bool:
    """True if any recorded source file has changed since the manifest wrote."""
    sources = manifest.get("sources") or []
    for rec in sources:
        current = _stat_record(rec["path"])
        if current is None:
            return True
        if current["mtime"] != rec["mtime"] or current["size"] != rec["size"]:
            return True
    return False


def sync_manifest(root: Group, key_dir: Path) -> dict[str, Any]:
    """Build the fresh manifest and rewrite the cache only on a hash change.

    Called on the execution path, which has already paid to import the tree.
    The cache is keyed by *key_dir* (the cwd), since the effective task set is
    the cascade from the repo root down. The hash guard avoids needless disk
    writes (and mtime churn) when nothing about the command surface changed.
    """
    fresh = build_manifest(root)
    path = _paths.manifest_path(key_dir)
    cached = load_manifest(path)
    if cached is None or cached.get("hash") != fresh["hash"]:
        write_manifest(fresh, path)
    return fresh
