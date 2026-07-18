"""Separator-free chain splitting, driven purely by the manifest.

`fm build lint --fix test` is split into three independent segments with no
separator at all — duty's muscle memory, but with real flags and positionals.
The manifest gives the splitter exact knowledge of every task's shape, which
makes the split deterministic under six rules (see `NOTES`):

1. params with defaults are options, never positionals (the load-bearing rule);
2. required positionals are consumed by exact arity, eagerly validated;
3. options bind to their own segment;
4. list options repeat the flag (`--tag a --tag b`);
5. variadic / `--` passthrough segments are terminal; `+` is the always
   available explicit boundary;
6. globals precede the first task name.

Every error names the task, states the expectation, and proposes the fix —
error messages are product surface here, not diagnostics.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footman import coerce


class ChainError(Exception):
    """A malformed command line, carrying a teaching message for the user."""


# Global options bind to `fm` itself and must precede the first task name
# (`--help`/`-h` is the one exception: anywhere before `--`, it wins).
# (canonical, short alias, kind, value-hint, help)
GLOBALS: list[tuple[str, str | None, str, str | None, str]] = [
    ("--help", "-h", "flag", None, "help for fm, or the named group/task"),
    ("--version", "-V", "flag", None, "print the version and exit"),
    ("--list", "-l", "flag", None, "list tasks (flat)"),
    ("--tree", None, "flag", None, "list tasks grouped by command group"),
    ("--where", None, "option", "TASK", "print the task's source file:line"),
    ("--dry-run", "-n", "flag", None, "print the parsed plan without running"),
    ("--keep-going", "-k", "flag", None, "run every branch even if one fails"),
    ("--sequential", "-s", "flag", None, "run one at a time (default: parallel)"),
    ("--quiet", "-q", "flag", None, "suppress the per-task summary"),
    ("--verbose", "-v", "flag", None, "replay captured output even on success"),
    ("--no-color", None, "flag", None, "disable ANSI colour"),
    ("--json", None, "flag", None, "machine-readable results (captures output)"),
    ("--timings", None, "flag", None, "show per-task durations"),
    ("--directory", "-C", "option", "PATH", "run as if launched from PATH"),
    ("--tasks-file", "-f", "option", "PATH", "use exactly one tasks file, no cascade"),
    ("--config", None, "option", "PATH", "override config with a single TOML file"),
    # "option?": the value is optional — bare `--install-completion` detects
    # the invoking shell.
    ("--install-completion", None, "option?", "[SHELL]", "install shell completion"),
]
_GLOBAL_KIND = {name: kind for name, _, kind, _, _ in GLOBALS}
_GLOBAL_KIND.update({alias: kind for _, alias, kind, _, _ in GLOBALS if alias})
_CANON = {alias: name for name, alias, _, _, _ in GLOBALS if alias}


@dataclass
class Segment:
    """One resolved task invocation within a chain."""

    task: str  # dotted path, e.g. "docs.build"
    path: list[str]  # ["docs", "build"]
    values: dict[str, Any] = field(default_factory=dict)  # cli-name -> value
    variadic: list[str] = field(default_factory=list)
    passthrough: list[str] | None = None


_TYPE_PHRASE = {
    "bool": "true or false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


def _suggest_only(choices: list | None, dynamic: dict | None) -> bool:
    """Whether a completer only *suggests* (never rejects): a soft completer
    (`strict=False`), or a strict one whose candidate list is empty — the
    completer genuinely returned nothing, and a *failing* strict completer
    aborts the manifest build instead, so rejecting every value would brick
    the task."""
    return bool(dynamic) and (not dynamic.get("strict") or not choices)


def _check(
    where: str,
    label: str,
    value: str,
    *,
    choices: list | None = None,
    types: list | None = None,
    dynamic: dict | None = None,
    path: str | None = None,
    bounds: tuple | None = None,
) -> None:
    """Validate one string against choices or type tags; raise a taught error."""
    if choices is not None:
        if _suggest_only(choices, dynamic):
            return
        if value not in choices:
            listing = "|".join(choices) if choices else "(none available)"
            close = difflib.get_close_matches(value, choices, n=1)
            hint = f" — did you mean {close[0]!r}?" if close else ""
            raise ChainError(
                f"{where}: {label} must be one of {listing} (got {value!r}){hint}"
            )
        return
    if types and not coerce.coerce_scalar(value, types)[0]:
        expected = " or ".join(str(_TYPE_PHRASE.get(t, t)) for t in types)
        raise ChainError(f"{where}: {label} expects {expected} (got {value!r})")
    if path is not None:
        _check_path(where, label, value, path)
    if bounds is not None:
        _check_bounds(where, label, value, types, bounds)


_PATH_PHRASE = {
    "exists": ("an existing path", Path.exists),
    "file": ("an existing file", Path.is_file),
    "dir": ("an existing directory", Path.is_dir),
}


def _check_path(where: str, label: str, value: str, req: str) -> None:
    phrase, test = _PATH_PHRASE[req]
    if not test(Path(value)):
        raise ChainError(f"{where}: {label} must be {phrase} (got {value!r})")


def _check_bounds(
    where: str, label: str, value: str, types: list | None, bounds: tuple
) -> None:
    ok, number = coerce.coerce_scalar(value, types or ["int", "float"])
    if not ok or isinstance(number, bool) or not isinstance(number, (int, float)):
        return  # the types check above already taught the type error
    lo, hi = bounds
    # Negated comparisons so NaN (which compares False to everything, so `< lo`
    # and `> hi` are both False) is rejected, not silently accepted; identical
    # to the plain comparisons for every real number.
    if (lo is not None and not (number >= lo)) or (
        hi is not None and not (number <= hi)
    ):
        expect = (
            f"at least {lo}"
            if hi is None
            else f"at most {hi}"
            if lo is None
            else f"between {lo} and {hi}"
        )
        raise ChainError(f"{where}: {label} must be {expect} (got {value!r})")


def _validate(where: str, p: dict, value: str) -> None:
    """Eagerly validate a choice/typed value; raise a taught error if wrong."""
    label = f"<{p['name']}>" if p["kind"] == "argument" else f"--{p['name']}"
    bounds = (p.get("min"), p.get("max")) if "min" in p or "max" in p else None
    _check(
        where,
        label,
        value,
        choices=p.get("choices"),
        types=p.get("types"),
        dynamic=p.get("dynamic"),
        path=p.get("path"),
        bounds=bounds,
    )


def _parse_globals(argv: list[str], i: int) -> tuple[list[str], int]:
    globals_: list[str] = []
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        name = argv[i].split("=", 1)[0]
        if name not in _GLOBAL_KIND:
            raise ChainError(
                f"unknown global option {name} "
                f"(global options go before the first task)"
            )
        globals_.append(_CANON.get(name, name) + argv[i][len(name) :])
        i += 1
        kind = _GLOBAL_KIND[name]
        if kind == "option" and "=" not in globals_[-1]:
            if i >= len(argv):
                raise ChainError(f"{name} expects a value")
            globals_.append(argv[i])
            i += 1
        elif kind == "option?" and "=" not in globals_[-1]:
            # Optional value: consume the next word only when one is present
            # and not option-shaped; normalise to --name=value so downstream
            # can tell "given with value" from "given bare".
            if i < len(argv) and not argv[i].startswith("-"):
                globals_[-1] += f"={argv[i]}"
                i += 1
    return globals_, i


def split_chain(tree: dict, argv: list[str]) -> tuple[list[str], list[Segment]]:
    """Split *argv* into leading globals and a list of resolved segments."""
    globals_, i = _parse_globals(argv, 0)
    segments: list[Segment] = []

    while i < len(argv):
        node, path = tree, []
        while i < len(argv) and argv[i] in node["groups"]:
            path.append(argv[i])
            node = node["groups"][argv[i]]
            i += 1
        if i >= len(argv) or argv[i] not in node["tasks"]:
            got = argv[i] if i < len(argv) else "(end of line)"
            scope = " ".join(path)
            where = f"{scope}: " if scope else ""
            known = ", ".join(list(node["groups"]) + list(node["tasks"]))
            raise ChainError(
                f"{where}expected a task name, got {got!r} (know: {known})"
            )
        task = node["tasks"][argv[i]]
        path.append(argv[i])
        i += 1

        opts = {
            "--" + p["name"]: p
            for p in task["params"]
            if p["kind"] in ("flag", "option")
        }
        # Exact-arity positionals, then a single trailing consumer for the rest:
        # a typed multiple/one-or-many positional, or a `*args` variadic.
        fixed = [
            p
            for p in task["params"]
            if p["kind"] == "argument" and not p.get("multiple")
        ]
        rest = next(
            (
                p
                for p in task["params"]
                if (p["kind"] == "argument" and p.get("multiple"))
                or p["kind"] == "variadic"
            ),
            None,
        )
        seg = Segment(task=".".join(path), path=list(path))
        filled = 0
        rest_count = 0

        while i < len(argv):
            tok = argv[i]
            if tok == "+":  # explicit segment boundary
                i += 1
                break
            if tok == "--":  # passthrough is terminal for the whole line
                seg.passthrough = argv[i + 1 :]
                i = len(argv)
                break
            if tok.startswith("--"):
                i = _consume_option(seg, opts, argv, i)
            elif filled < len(fixed):
                _consume_positional(seg, tree, fixed[filled], tok)
                filled += 1
                i += 1
            elif rest is not None:
                if rest["kind"] == "variadic":
                    seg.variadic.append(tok)
                else:
                    _consume_positional(seg, tree, rest, tok)
                rest_count += 1
                i += 1
            else:
                break  # arity satisfied: the next word starts a new segment

        missing = [f"<{p['name']}>" for p in fixed[filled:]]
        if rest is not None and rest["kind"] == "argument" and rest_count == 0:
            missing.append(f"<{rest['name']}>")
        if missing:
            raise ChainError(
                f"{seg.task}: missing required argument(s): {', '.join(missing)}"
            )
        segments.append(seg)

    return globals_, segments


def _consume_option(seg: Segment, opts: dict, argv: list[str], i: int) -> int:
    tok = argv[i]
    name = tok.split("=", 1)[0]
    negated = False
    p = opts.get(name)
    if p is None and name.startswith("--no-"):
        candidate = "--" + name[len("--no-") :]
        if candidate in opts and opts[candidate]["kind"] == "flag":
            p, negated = opts[candidate], True
    if p is None:
        hint = (
            " (task options come right after their task; "
            "globals go before the first task)"
        )
        raise ChainError(f"{seg.task}: unknown option {name}{hint}")

    cli = p["name"]
    if p["kind"] == "flag":
        if "=" in tok:
            raise ChainError(f"{seg.task}: --{cli} is a flag and takes no value")
        seg.values[cli] = not negated
        return i + 1

    # value-bearing option
    if "=" in tok:
        value = tok.split("=", 1)[1]
        i += 1
    else:
        i += 1
        if i >= len(argv):
            raise ChainError(f"{seg.task}: {name} expects a value")
        value = argv[i]
        i += 1
    if p.get("mapping"):
        for pair in _values(p, value):
            _consume_pair(seg, p, cli, pair)
    elif p.get("multiple"):
        for part in _values(p, value):
            _validate(seg.task, p, part)
            seg.values.setdefault(cli, []).append(part)
    else:
        _validate(seg.task, p, value)
        seg.values[cli] = value
    return i


def _values(p: dict, value: str) -> list[str]:
    """Comma-split parts of a list/dict value, unless the param opts out.

    Called only for collection params, so splitting is the default; a `nosplit`
    param (values may contain commas) takes the whole token verbatim.
    """
    if p.get("nosplit"):
        return [value]
    return [part for part in value.split(",") if part] or [value]


def _consume_pair(seg: Segment, p: dict, cli: str, pair: str) -> None:
    """Parse and validate one `KEY=VALUE` token for a dict parameter."""
    if "=" not in pair:
        raise ChainError(f"{seg.task}: --{cli} expects KEY=VALUE (got {pair!r})")
    key, value = pair.split("=", 1)
    _check(seg.task, f"--{cli} key", key, types=p.get("key_types"))
    _check(
        seg.task,
        f"--{cli} value",
        value,
        choices=p.get("value_choices"),
        types=p.get("value_types"),
    )
    seg.values.setdefault(cli, []).append((key, value))


def _consume_positional(seg: Segment, tree: dict, p: dict, tok: str) -> None:
    if (
        "choices" in p
        and tok not in p["choices"]
        and not _suggest_only(p["choices"], p.get("dynamic"))
        and (tok in tree["tasks"] or tok in tree["groups"])
    ):
        raise ChainError(
            f"{seg.task}: <{p['name']}> must be one of "
            f"{'|'.join(p['choices'])} — {tok!r} looks like the next task; "
            f"did you forget <{p['name']}>?"
        )
    if p.get("multiple"):
        for part in _values(p, tok):
            _validate(seg.task, p, part)
            seg.values.setdefault(p["name"], []).append(part)
    else:
        _validate(seg.task, p, tok)
        seg.values[p["name"]] = tok
