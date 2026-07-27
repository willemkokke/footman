"""What each curated tool accepted, release by release.

A stub is a rendering, not a record. The record is one JSON file per tool
under `_history/`: the **newest** observed release stored whole, and every
older one a delta describing how to step back to it.

Pointing the deltas backwards is what makes the format fit the work:

* priming backwards is pure append — an older release adds one delta against
  the current oldest, and nothing already written is touched;
* the current version costs no replay, because it *is* the base;
* midfill rewrites exactly one entry, the inserted release's successor;
* "did anything change in this release" is "is its delta non-empty", which
  is the question a release job actually asks.

`since` / `until` are never stored. They are derived by walking the chain,
so a half-primed file can never assert history it has not looked at —
`observed_from` says how far back the chain reaches, and that is a fact
about what was read rather than a policy about what we meant to read.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from footman._toolspec import Option, ToolSpec, Verb

SCHEMA = 1
"""Bumped when the on-disk shape changes in a way a reader must know about."""

EXTRACTOR = 1
"""The extractor generation that produced an observation.

Recorded per release so improving `_toolhelp`/`_toolspec` — or a tool
flipping between the click and `--help` paths — rewrites state without
counting as the tool having changed. Bump it when extraction starts
producing different words for the same tool.
"""


# --- a surface: one release's option tree, as data ---------------------------
#
# Deliberately not the whole ToolSpec. `version` belongs to the release that
# keyed it, and `in_process` is a fact about the machine that looked (does the
# tool publish a console-script entry point), not about the release — both are
# supplied at render time instead.
#
# The *platform* is neither: it is a fact about the observation, like its
# date, and it rides beside the surface. It is what will let a later
# multi-platform refresh say "absent on Windows, and Windows was read" —
# an exclusion — rather than leaving every option looking universal because
# only one machine ever looked. Until then it records the honest thing: which
# OS this reading came from.


def surface_of(spec: ToolSpec) -> dict[str, Any]:
    """A ToolSpec reduced to what a release *is*, losing nothing else."""
    return {
        "help": spec.help,
        "verbs": {
            verb.name: {
                "help": verb.help,
                "wraps": verb.wraps,
                "positional": verb.positional,
                "lead": verb.lead,
                "options": {
                    option.name: {
                        "flags": list(option.flags),
                        "negation": option.negation,
                        "help": option.help,
                        "type": option.type_name,
                        "default": option.default,
                        "choices": list(option.choices),
                    }
                    for option in verb.options
                },
            }
            for verb in spec.verbs
        },
    }


def spec_from(
    surface: dict[str, Any], *, name: str, version: str = "", in_process: bool = False
) -> ToolSpec:
    """The inverse of `surface_of` — what the stub renderer consumes."""
    return ToolSpec(
        name=name,
        help=surface.get("help", ""),
        version=version,
        in_process=in_process,
        verbs=tuple(
            Verb(
                name=verb_name,
                help=verb.get("help", ""),
                wraps=verb.get("wraps", False),
                positional=verb.get("positional", "any"),
                lead=verb.get("lead", ""),
                options=tuple(
                    Option(
                        name=option_name,
                        flags=tuple(option.get("flags", ())),
                        negation=option.get("negation", ""),
                        help=option.get("help", ""),
                        type_name=option.get("type", "str"),
                        default=option.get("default"),
                        choices=tuple(option.get("choices", ())),
                    )
                    for option_name, option in verb.get("options", {}).items()
                ),
            )
            for verb_name, verb in surface.get("verbs", {}).items()
        ),
    )


# --- the chain ---------------------------------------------------------------


def new(
    tool: str, *, version: str, date: str, surface: dict[str, Any], platform: str = ""
) -> dict:
    """A history of one release. A short history is a valid history — which is
    what lets the store ship before anything has been primed."""
    return {
        "schema": SCHEMA,
        "tool": tool,
        "observed_from": version,
        "base": {
            "version": version,
            "date": date,
            "platform": platform,
            "extractor": EXTRACTOR,
            "surface": surface,
        },
        "deltas": {},
    }


def delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """How to step back from *newer* to *older*, per option.

    Three moves, and an empty delta means the release was observed and
    changed nothing — which is not the same as a release nobody looked at.
    Those are simply absent.
    """
    out: dict[str, Any] = {}
    new_opts, old_opts = _flat(newer), _flat(older)
    if drop := sorted(set(new_opts) - set(old_opts)):
        out["drop"] = drop  # the newer release added these
    if add := sorted(set(old_opts) - set(new_opts)):
        out["add"] = {key: old_opts[key] for key in add}  # ...and removed these
    revert = {
        k: old_opts[k]
        for k in old_opts.keys() & new_opts.keys()
        if old_opts[k] != new_opts[k]
    }
    if revert:
        out["revert"] = dict(sorted(revert.items()))
    if verbs := _verb_delta(newer, older):
        out["verbs"] = verbs
    if older.get("help", "") != newer.get("help", ""):
        out["help"] = older.get("help", "")
    return out


def apply(surface: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """*surface* stepped back by one delta — the inverse of `delta`."""
    out = json.loads(json.dumps(surface))  # a deep copy; surfaces are plain data
    for key in step.get("drop", ()):
        verb, _, option = key.partition("\t")
        out["verbs"][verb]["options"].pop(option, None)
    for key, option in {**step.get("add", {}), **step.get("revert", {})}.items():
        verb, _, name = key.partition("\t")
        out["verbs"].setdefault(verb, _empty_verb())["options"][name] = option
    for name, changed in step.get("verbs", {}).items():
        if changed is None:
            out["verbs"].pop(name, None)
        else:
            verb = out["verbs"].setdefault(name, _empty_verb())
            verb.update({k: v for k, v in changed.items() if k != "options"})
            verb.setdefault("options", {})
    if "help" in step:
        out["help"] = step["help"]
    return {"help": out.get("help", ""), "verbs": _ordered(out["verbs"])}


def extend(
    doc: dict,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platform: str = "",
) -> bool:
    """Append an *older* release to the end of the chain.

    This is what priming does, and why the deltas point backwards: the new
    entry is a delta from the current oldest release to this one, and nothing
    already written moves. Returns whether anything was added — a release the
    chain already holds is skipped, which is what makes a prime resumable
    against a rate limit.
    """
    if version in observed(doc):
        return False
    oldest = doc["observed_from"]
    previous = at(doc, oldest)
    if previous is None:  # pragma: no cover — observed_from is always in the chain
        raise ValueError(f"{oldest} is not in the chain")
    doc["deltas"][version] = {
        "date": date,
        "platform": platform,
        "extractor": EXTRACTOR,
        **delta(previous, surface),
    }
    doc["observed_from"] = version
    return True


def at(doc: dict, version: str) -> dict[str, Any] | None:
    """The surface of *version*, replayed from the base. `None` when that
    release was never observed — which a caller must not read as "empty"."""
    base = doc["base"]
    surface = base["surface"]
    if version == base["version"]:
        return surface
    for older, step in doc["deltas"].items():
        surface = apply(surface, step)
        if older == version:
            return surface
    return None


def union(doc: dict, *, name: str, in_process: bool = False) -> ToolSpec:
    """Every option the tool has *ever* had, each with its interval.

    The stub renders this rather than the newest release alone: a removed
    flag stays completable, because the reader may be running a version that
    still has it, and its docstring says when it went. An option's properties
    come from the newest release that had it — the most recent word the tool
    said about itself.

    `since` is left empty for anything already present at the oldest release
    read. The history reaches only as far as it was primed, and "at or before
    the floor" is not a `since`.
    """
    chain = observed(doc)  # newest first
    floor = chain[-1]
    surfaces = {version: at(doc, version) for version in chain}

    verbs: dict[str, dict[str, Any]] = {}
    first: dict[tuple[str, str], str] = {}
    last: dict[tuple[str, str], str] = {}
    for version in reversed(chain):  # oldest first, so "first" means first
        surface = surfaces[version] or {}
        for verb_name, verb in surface.get("verbs", {}).items():
            verbs.setdefault(verb_name, verb)
            merged = {
                **verbs[verb_name].get("options", {}),
                **verb.get("options", {}),
            }
            # Sorted, so the stub does not reorder itself as the history
            # deepens: merged oldest-first, insertion order would otherwise
            # mean "which release mentioned it first".
            verbs[verb_name] = {**verb, "options": dict(sorted(merged.items()))}
            for option_name in verb.get("options", {}):
                key = (verb_name, option_name)
                first.setdefault(key, version)
                last[key] = version

    newer = {older: new for new, older in itertools.pairwise(chain)}
    spec = spec_from(
        {"help": (surfaces[chain[0]] or {}).get("help", ""), "verbs": verbs},
        name=name,
        version=chain[0],
        in_process=in_process,
    )
    return ToolSpec(
        name=spec.name,
        help=spec.help,
        version=spec.version,
        in_process=spec.in_process,
        verbs=tuple(
            Verb(
                name=verb.name,
                help=verb.help,
                wraps=verb.wraps,
                positional=verb.positional,
                lead=verb.lead,
                options=tuple(
                    Option(
                        **{
                            **option.__dict__,
                            "since": ""
                            if first[(verb.name, option.name)] == floor
                            else first[(verb.name, option.name)],
                            "until": newer.get(last[(verb.name, option.name)], "")
                            if last[(verb.name, option.name)] != chain[0]
                            else "",
                        }
                    )
                    for option in verb.options
                ),
            )
            for verb in spec.verbs
        ),
    )


def observed(doc: dict) -> list[str]:
    """Every release in the chain, newest first."""
    return [doc["base"]["version"], *doc["deltas"]]


def load(path: Path) -> dict | None:
    """A tool's history, or `None` when it has none yet."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(doc: dict, path: Path) -> None:
    """Write *doc* atomically, formatted for a diff a human reads."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)


# --- helpers -----------------------------------------------------------------


def _flat(surface: dict[str, Any]) -> dict[str, Any]:
    """Options keyed `verb\\toption`, so a diff is one flat set operation.

    A tab, because a verb is dotted (`compose.up`) and an option name can
    carry anything a tool's `--help` prints — but neither can hold a tab.
    """
    return {
        f"{verb_name}\t{option_name}": option
        for verb_name, verb in surface.get("verbs", {}).items()
        for option_name, option in verb.get("options", {}).items()
    }


def _verb_delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """Verb-level changes: a verb gained or lost, or its own metadata moved.

    Options ride the flat diff; this carries what hangs off the verb itself —
    its help, whether it wraps another command, its positional shape.
    """
    fields = ("help", "wraps", "positional", "lead")
    out: dict[str, Any] = {}
    new_verbs, old_verbs = newer.get("verbs", {}), older.get("verbs", {})
    for name in set(new_verbs) - set(old_verbs):
        out[name] = None  # the newer release added it; stepping back drops it
    for name, verb in old_verbs.items():
        if name not in new_verbs:
            out[name] = {f: verb.get(f) for f in fields}
        elif changed := {
            f: verb.get(f) for f in fields if verb.get(f) != new_verbs[name].get(f)
        }:
            out[name] = changed
    return out


def _empty_verb() -> dict[str, Any]:
    return {"help": "", "wraps": False, "positional": "any", "lead": "", "options": {}}


def _ordered(verbs: dict[str, Any]) -> dict[str, Any]:
    """Verbs and their options in name order, so a replayed surface compares
    equal to a freshly extracted one however the deltas arrived."""
    return {
        name: {**verb, "options": dict(sorted(verb.get("options", {}).items()))}
        for name, verb in sorted(verbs.items())
    }
