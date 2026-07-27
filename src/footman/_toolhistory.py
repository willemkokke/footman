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
from collections.abc import Iterable
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
# The *platforms* are neither: they are a fact about the observation, like
# its date, and ride beside the surface. A list rather than one name, because
# a release read on three platforms is **one** observation of a merged
# surface — storing it three times would triple a store whose options are
# nearly all universal, to carry the rare one that is not. The list says who
# looked; a per-option `not_on` will later say who disagreed, which is the
# efficient way round. Until a refresh runs a matrix there is one name in it,
# and that is the honest record: only this OS ever looked.


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
    tool: str,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
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
            "platforms": sorted(platforms or []),
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
    platforms: list[str] | None = None,
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
        "platforms": sorted(platforms or []),
        "extractor": EXTRACTOR,
        **delta(previous, surface),
    }
    doc["observed_from"] = version
    return True


def promote(
    doc: dict,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> bool:
    """Make *version* the new base, demoting the old one to a delta.

    The forward counterpart of `extend`, and the other half of why the deltas
    point backwards: a newer release touches exactly two entries, the new
    base and the one it displaces, whatever the chain's length.

    Returns **whether anything changed** — which is the release gate's whole
    question. An empty delta means this release was observed and altered
    nothing, so there is a new version to record and nothing to release for.
    """
    previous = doc["base"]
    step = delta(surface, previous["surface"])
    doc["deltas"] = {
        previous["version"]: {
            "date": previous["date"],
            "platforms": previous.get("platforms", []),
            "extractor": previous["extractor"],
            **step,
        },
        **doc["deltas"],
    }
    doc["base"] = {
        "version": version,
        "date": date,
        "platforms": sorted(platforms or []),
        "extractor": EXTRACTOR,
        "surface": surface,
    }
    return bool(step)


def insert(
    doc: dict,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> bool:
    """Place a release at its own position in the chain, wherever that is.

    `extend` appends below the floor and `promote` replaces the head; this is
    the third case the format was designed for and the one neither covers — a
    release that belongs *between* two the chain already holds. Exactly one
    entry is recomputed, the inserted release's successor, because that is the
    only delta whose starting point moved. Nothing else is touched, however
    long the chain.

    What it buys is that **gathering need not be ordered**. Installing a
    release and reading its `--help` does not depend on any other release
    having been read; only the arithmetic afterwards does, and that is a dict
    diff over surfaces already in hand. So a walk can fetch in whatever order
    it likes — in parallel, or across several runs — and assemble as results
    arrive. It also means a release that would not install stops being fatal
    to a tool's whole walk: the gap is filled by a later run.

    A gap costs precision until it is filled, not correctness. An option that
    arrived in the missing release reads as arriving at the next release that
    *was* read, which is the same honest imprecision the chain already carries
    wherever an index has no build to offer.

    Returns whether the release was added; a release the chain already holds
    is left exactly as it is.
    """
    from footman.tools import version_tuple

    if version in observed(doc):
        return False
    chain = observed(doc)  # newest first

    def placed(name: str) -> tuple[tuple[int, ...], str]:
        entry = doc["base"] if name == doc["base"]["version"] else doc["deltas"][name]
        return version_tuple(name), entry.get("date", "")

    mine = (version_tuple(version), date)
    if mine > placed(chain[0]):
        promote(doc, version=version, date=date, surface=surface, platforms=platforms)
        return True
    if mine < placed(chain[-1]):
        return extend(
            doc, version=version, date=date, surface=surface, platforms=platforms
        )

    older = next(name for name in chain if placed(name) < mine)
    newer = chain[chain.index(older) - 1]
    before, after = at(doc, newer), at(doc, older)
    if before is None or after is None:  # pragma: no cover — both are in the chain
        raise ValueError(f"{newer} or {older} is not in the chain")

    rebuilt: dict[str, Any] = {}
    for name, entry in doc["deltas"].items():
        if name == older:
            rebuilt[version] = {
                "date": date,
                "platforms": sorted(platforms or []),
                "extractor": EXTRACTOR,
                **delta(before, surface),
            }
            # The one recomputed entry: `older` used to step back from
            # `newer`, and now steps back from the release between them.
            rebuilt[name] = {
                "date": entry.get("date", ""),
                "platforms": entry.get("platforms", []),
                "extractor": entry.get("extractor", EXTRACTOR),
                **delta(surface, after),
            }
        else:
            rebuilt[name] = entry
    doc["deltas"] = rebuilt
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


def changes(doc: dict, *, since: str, until: str = "") -> dict[str, Any]:
    """What changed between two observed releases, as one step.

    The *net* effect, not a concatenation of the steps between: an option a
    tool added and then withdrew across the span cancels out, which is what
    someone reading a release note wants to know. Computed by replaying both
    ends and taking one delta, so it cannot disagree with the chain.

    Returned in the same shape as `delta` and read the same way round —
    `drop` is what the newer release *added*, `add` is what it removed —
    because it is a step back from *until* to *since*.
    """
    newer = at(doc, until or doc["base"]["version"])
    older = at(doc, since)
    if newer is None or older is None:
        return {}
    return delta(newer, older)


def spellings(doc: dict, version: str, keys: Iterable[str]) -> dict[str, str]:
    """How *version* spells each option key on the command line.

    A delta records the option's Python-side name, which is what the surface
    is keyed by; a reader of a release note recognises `--all-files`. The
    flags live in the surface, so the spelling is a lookup rather than
    something the delta has to carry.
    """
    surface = at(doc, version) or {}
    found: dict[str, str] = {}
    for key in keys:
        verb, _, option = key.partition("\t")
        entry = surface.get("verbs", {}).get(verb, {}).get("options", {}).get(option)
        flags = (entry or {}).get("flags") or []
        # The long spelling when there is one: `--all-files` over `-a`.
        found[key] = max(flags, key=len) if flags else option
    return found


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
    """Write *doc* atomically, formatted for a diff a human reads.

    The temp name carries the thread id beside the pid: assembly is
    single-threaded by design, but a rule enforced by a filename is cheaper
    than one enforced by remembering, and two threads that ever do write one
    tool's file will each replace whole documents instead of corrupting a
    shared temp.
    """
    import os
    import threading

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
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
