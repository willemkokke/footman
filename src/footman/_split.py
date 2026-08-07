"""Separator-free chain splitting, driven purely by the manifest.

`fm build lint --fix test` is split into three independent segments with no
separator at all — duty's muscle memory, but with real flags and positionals.
A task's *address* is always a single dotted token (`fm docs.serve`), so the
first word of every segment names its task completely — no group descent.
The manifest gives the splitter exact knowledge of every task's shape, which
makes the split deterministic under six rules (see `NOTES`):

1. params with defaults are options, never positionals (the load-bearing rule);
2. required positionals are consumed by exact arity, eagerly validated;
3. options bind to their own segment, and a value is always `=`-attached
   (`--target=prod`, `-j=4`) — a bare word is a task or a positional, a bare
   `--x`/`-x` is a flag, so every token reads without arity knowledge;
4. list options repeat the flag (`--tag=a --tag=b`) or comma-join (`--tag=a,b`);
5. variadic / `--` passthrough segments are terminal; `+` is the always
   available explicit boundary;
6. globals precede the first task name.

Every error names the task, states the expectation, and proposes the fix —
error messages are product surface here, not diagnostics. The space form of
a nested address (`fm docs serve`) is permanently *taught against*, never
parsed: the error path detects it and answers with the dotted spelling.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footman import _coerce


def _close1(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance exactly 1: one substitution, insertion,
    deletion, or adjacent transposition — the "pyhton" shapes a hurried hand
    actually types. Bounded and cheap; called only on a group default's
    positional values against its child names."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True  # substitution
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )  # adjacent transposition
    short, long_ = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]  # one insertion/deletion


def _did_you_mean(word: str, known: Iterable[str]) -> str:
    """A ` — did you mean 'x'?` suffix when *word* closely matches a known name.

    Empty when nothing is close, so a genuine typo never gets false-confident
    advice. The one idiom behind every not-found message (task, option, choice).
    """
    close = difflib.get_close_matches(word, list(known), n=1)
    return f" — did you mean {close[0]!r}?" if close else ""


def _misplaced_global(token: str) -> str | None:
    """The teaching message when *token* is really one of the GLOBALS.

    A global option found after a task name is a position mistake, not an
    unknown name — so name the real problem and its fix instead of guessing
    at close matches. Only fires for *unknown* task options: a task param
    that shares a global's name still wins by position, as it should.
    """
    name = token.split("=", 1)[0]
    if name not in _GLOBAL_KIND:
        return None
    canon = _CANON.get(name, name)
    label = name if name == canon else f"{name} ({canon})"
    return f"{label} is a global option — it goes before the first task name"


class ChainError(Exception):
    """A malformed command line, carrying a teaching message for the user."""


# Global options bind to `fm` itself and must precede the first task name
# (`--help`/`-h` is the one exception: anywhere before `--`, it wins).
# (canonical, short alias, kind, value-hint, help)
GLOBALS: list[tuple[str, str | None, str, str | None, str]] = [
    ("--help", "-h", "flag", None, "help for {prog}, or the named group/task"),
    ("--version", "-V", "flag", None, "print the version and exit"),
    ("--list", "-l", "flag", None, "list tasks (flat)"),
    ("--tree", None, "flag", None, "list tasks grouped by command group"),
    ("--sort", None, "flag", None, "list tasks alphabetically (default: as defined)"),
    ("--all", "-a", "flag", None, "include hidden tasks in the listings"),
    ("--where", None, "option", "TASK", "print the task's source file:line"),
    # The bracketed hint marks the value optional: bare `--describe` dumps
    # the whole tree's contract; a group address answers for its subtree.
    ("--describe", None, "option", "[ADDR]", "print task contracts as JSON"),
    (
        "--plugins",
        None,
        "flag",
        None,
        "list installed footman.tasks plugins, pulled or not",
    ),
    ("--dry-run", "-n", "flag", None, "rehearse: bodies run, footman's work is faked"),
    ("--keep-going", "-k", "flag", None, "run every branch even if one fails"),
    ("--fail-fast", None, "flag", None, "stop at the first failure"),
    (
        "--sequential",
        "-s",
        "flag",
        None,
        "run one at a time, parallel() blocks included",
    ),
    (
        "--jobs",
        "-j",
        "option",
        "N",
        "max parallel tasks (default: cores - 1, never below 2)",
    ),
    ("--yes", "-y", "flag", None, "assume yes to every confirm() gate"),
    ("--no-input", None, "flag", None, "never prompt; error if input is required"),
    ("--quiet", "-q", "flag", None, "suppress the per-task summary"),
    ("--verbose", "-v", "flag", None, "replay captured output even on success"),
    ("--color", None, "option", "WHEN", "when to colour: always|never|auto (default)"),
    ("--no-color", None, "flag", None, "disable ANSI colour (same as --color=never)"),
    ("--no-progress", None, "flag", None, "no progress bar, eta, or timing capture"),
    ("--json", None, "flag", None, "stdout is one JSON document (captures output)"),
    ("--timings", None, "flag", None, "show per-task durations"),
    ("--directory", "-C", "option", "PATH", "run as if launched from PATH"),
    ("--tasks-file", "-f", "option", "PATH", "only this tasks file, no tasks cascade"),
    ("--config", None, "option", "PATH", "only this config file, no config cascade"),
    # The bracketed hint marks the value optional: bare `--install-completion`
    # / `--setup-completion` detect the invoking shell.
    ("--install-completion", None, "option", "[SHELL]", "install shell completion"),
    ("--setup-completion", None, "option", "[SHELL]", "print completion for eval"),
    (
        "--uninstall-completion",
        None,
        "option",
        "[SHELL]",
        "remove the completion hook",
    ),
]
_GLOBAL_KIND = {name: kind for name, _, kind, _, _ in GLOBALS}
_GLOBAL_KIND.update({alias: kind for _, alias, kind, _, _ in GLOBALS if alias})
_CANON = {alias: name for name, alias, _, _, _ in GLOBALS if alias}
_GLOBAL_HINT = {name: hint for name, _, _, hint, _ in GLOBALS if hint}
# Options whose bare form is itself meaningful (`--install-completion` detects
# the invoking shell), so a missing `=value` is not an error.
_VALUE_OPTIONAL = frozenset(
    name for name, _, _, hint, _ in GLOBALS if hint and hint.startswith("[")
)


@dataclass
class Segment:
    """One resolved task invocation within a chain."""

    task: str  # dotted path, e.g. "docs.build"
    path: list[str]  # ["docs", "build"]
    values: dict[str, Any] = field(default_factory=dict)  # cli-name -> value
    variadic: list[str] = field(default_factory=list)
    passthrough: list[str] | None = None
    # Advisory stderr lines the app prints before running — `{prog}` is
    # substituted there. Notes never change what runs; the grammar stays
    # deterministic (a positional wins), they just say so out loud.
    notes: list[str] = field(default_factory=list)


def _required_label(p: dict[str, Any]) -> str:
    """Label a required option for the missing-option error — a flag teaches
    both its `--x` and `--no-x` forms."""
    name = f"--{p['name']}"
    return f"{name} (or --no-{p['name']})" if p["kind"] == "flag" else name


def _suggest_only(choices: list[str] | None, dynamic: dict[str, Any] | None) -> bool:
    """Whether a completer only *suggests* (never rejects): a soft completer
    (`strict=False`), or a strict one whose candidate list is empty — the
    completer genuinely returned nothing, and a *failing* strict completer
    aborts the manifest build instead, so rejecting every value would brick
    the task."""
    return bool(dynamic and (not dynamic.get("strict") or not choices))


def _check(
    where: str,
    label: str,
    value: str,
    *,
    choices: list[str] | None = None,
    types: list[str] | None = None,
    dynamic: dict[str, Any] | None = None,
    path: str | None = None,
    bounds: tuple[float | None, float | None] | None = None,
) -> None:
    """Validate one string against choices or type tags; raise a taught error."""
    if choices is not None:
        if _suggest_only(choices, dynamic):
            return
        if value in choices:
            return  # an exact choice needs no further type/bounds checks
        # A union like `Literal['fast','slow'] | int` carries both choices and
        # types: accept a value that matches either, and only teach both when
        # neither fits.
        if not (types and _coerce.coerce_scalar(value, types)[0]):
            listing = "|".join(choices) if choices else "(none available)"
            extra = f", or {_coerce.type_phrase(types)}" if types else ""
            hint = _did_you_mean(value, choices)
            raise ChainError(
                f"{where}: {label} must be one of {listing}{extra} "
                f"(got {value!r}){hint}"
            )
        # matched via the type path -> fall through to bounds/path below
    elif types and not _coerce.coerce_scalar(value, types)[0]:
        expected = _coerce.type_phrase(types)
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
    where: str,
    label: str,
    value: str,
    types: list[str] | None,
    bounds: tuple[float | None, float | None],
) -> None:
    ok, number = _coerce.coerce_scalar(value, types or ["int", "float"])
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


def _validate(where: str, p: dict[str, Any], value: str, at: int = 0) -> None:
    """Eagerly validate a choice/typed value; raise a taught error if wrong.

    *at* is the value's index in the stream, which only matters to a grouped
    shape: its positions have a type each, so the check has to know which slot
    this token is filling. `--size=800,tall` is refused before anything runs,
    naming `height` — the same eager treatment every other typed parameter
    gets, rather than a crash from inside binding.
    """
    label = (
        f"<{p['name']}>"
        if p["kind"] in ("positional", "variadic")
        else f"--{p['name']}"
    )
    bounds = (p.get("min"), p.get("max")) if "min" in p or "max" in p else None
    if group := p.get("group"):
        slot = at % group["max"]
        names = group["names"]
        _check(
            where,
            f"{label}: {names[slot] if names else f'value {slot + 1}'}",
            value,
            types=group["types"][slot],
        )
        return
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


def _check_arity(
    where: str, p: dict[str, Any], group: dict[str, Any], count: int
) -> None:
    """A grouped shape's arity, checked once the whole stream is in.

    Values accumulate from commas and repetition, so the count is only final
    at the end of the segment — but it is knowable from the manifest alone,
    which is what makes it a parse-time refusal rather than a binding crash.
    """
    label = f"--{p['name']}"
    size = group["max"]
    if group["many"]:
        if count % size:
            raise ChainError(
                f"{where}: {label} takes values in groups of {size} "
                f"({group['label']}) — got {count}, which leaves "
                f"{count % size} over"
            )
    elif not group["min"] <= count <= size:
        want = (
            group["label"]
            if group["min"] == size
            else f"{group['min']} to {size} values ({group['label']})"
        )
        raise ChainError(f"{where}: {label} takes {want} — got {count}")


def _expects_value(
    where: str | None, given: str, hint: str, follower: str | None
) -> str:
    """The `=`-attachment teaching for a value-bearing option given bare.

    When a bare word follows, name the exact fix with the user's own value
    (`--target prod` → "did you mean --target=prod?") — that word must never
    surface as "unknown task 'prod'". Otherwise state the shape.
    """
    prefix = f"{where}: " if where else ""
    if follower is not None:
        return (
            f"{prefix}{given} takes its value attached — "
            f"did you mean {given}={follower}?"
        )
    return f"{prefix}{given} expects a value, attached: {given}={hint}"


def _parse_globals(
    argv: list[str],
    i: int,
    *,
    plugin: dict[str, str] | None = None,
    lenient: bool = False,
) -> tuple[list[str], int]:
    """Consume the leading globals — purely lexical: every dash token is
    self-contained (a value is `=`-attached), and the first bare word starts
    the task chain.

    *plugin* maps a pulled plugin's long options (`--env-file`) to their
    kinds — `option?` marks one whose bare form is itself meaningful
    (`GlobalOption(bare=…)`), the `[SHELL]`-hint grammar footman's own
    completion installers speak. *lenient* carries an unknown dash token
    through untouched instead of refusing — the pre-discovery walk cannot
    know the plugins yet, so the authoritative post-discovery parse is the
    one that teaches.
    """
    known: dict[str, str] = dict(_GLOBAL_KIND)
    value_optional = set(_VALUE_OPTIONAL)
    if plugin:
        for flag, kind in plugin.items():
            if kind == "option?":
                known[flag] = "option"
                value_optional.add(flag)
            else:
                known[flag] = kind
    globals_: list[str] = []
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        name = argv[i].split("=", 1)[0]
        if name not in known:
            if lenient:
                globals_.append(argv[i])
                i += 1
                continue
            raise ChainError(
                f"unknown global option {name} "
                f"(global options go before the first task)"
            )
        canon = _CANON.get(name, name)
        if known[name] == "flag" and "=" in argv[i]:
            raise ChainError(f"{canon} is a flag and takes no value")
        if (
            known[name] == "option"
            and "=" not in argv[i]
            and canon not in value_optional
        ):
            follower = (
                argv[i + 1]
                if i + 1 < len(argv)
                and argv[i + 1] not in ("--", "+")
                and not argv[i + 1].startswith("-")
                else None
            )
            raise ChainError(
                _expects_value(None, name, _GLOBAL_HINT.get(canon, "VALUE"), follower)
            )
        globals_.append(canon + argv[i][len(name) :])
        i += 1
    return globals_, i


def flat_addresses(tree: dict[str, Any]) -> list[str]:
    """Every runnable dotted address: tasks at any depth, plus runnable groups.

    The one index behind did-you-mean suggestions for a mistyped address —
    everything in it is copy-paste-runnable, so a suggestion can never
    propose a bare namespace group.
    """
    out: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name in node["tasks"]:
            out.append(prefix + name)
        for name, sub in node["groups"].items():
            if "default" in sub:
                out.append(prefix + name)
            walk(sub, f"{prefix}{name}.")

    walk(tree, "")
    return out


def _children(node: dict[str, Any], prefix: str) -> list[str]:
    """A node's children as addresses for a "know:" listing — groups keep a
    trailing dot (`docs.`), the `ls -F` idiom, so descend-vs-run is visible;
    tasks are bare and copy-paste-runnable."""
    return [f"{prefix}{name}." for name in node["groups"]] + [
        f"{prefix}{name}" for name in node["tasks"]
    ]


def _resolve_head(
    tree: dict[str, Any],
    argv: list[str],
    i: int,
    prev_group: tuple[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[str], dict[str, Any] | None, int]:
    """Resolve segment head `argv[i]` — one dotted address — to its task.

    Returns `(task, path, group_node, next_i)`; `group_node` is set when the
    address named a runnable group (its default runs), so the caller can teach
    `group.child` if the *next* head turns out to be a child of it. Every
    failure is a taught `ChainError`; the space form of a nested address is
    detected by lookahead and answered with the dotted spelling.
    """
    token = argv[i]
    parts = token.split(".")
    if "" in parts:
        if token.endswith(".") and "" not in token[:-1].split("."):
            # `docs.` — an address left hanging; resolve the prefix so the
            # answer can list what completes it.
            node, path = tree, []
            for part in token[:-1].split("."):
                if part not in node["groups"]:
                    break
                node = node["groups"][part]
                path.append(part)
            else:
                known = ", ".join(_children(node, f"{'.'.join(path)}."))
                raise ChainError(f"{token!r} is an incomplete address (know: {known})")
        raise ChainError(
            f"{token!r} is not a task address — addresses are dot-separated "
            f"names with no empty segments, like 'docs.serve'"
        )

    node, path = tree, []
    for pos, part in enumerate(parts):
        last = pos == len(parts) - 1
        if part in node["groups"]:
            node = node["groups"][part]
            path.append(part)
            continue
        if part in node["tasks"]:
            path.append(part)
            if not last:
                dotted = ".".join(path)
                raise ChainError(
                    f"{token!r}: {dotted!r} is a task, not a group — "
                    f"nothing lives beneath it"
                )
            return node["tasks"][part], path, None, i + 1
        # Unknown segment. At the very start this may be a misplaced global,
        # or a child of the runnable group that led the previous segment
        # (`fm lint python` — the space form, taught, not parsed).
        if pos == 0:
            if (misplaced := _misplaced_global(token)) is not None:
                raise ChainError(misplaced)
            if (
                i > 0
                and (prev := argv[i - 1]).startswith("-")
                and "=" not in prev
                and (
                    _GLOBAL_KIND.get(prev) == "option"
                    or any(
                        "--" + g["name"] == prev and "bare" in g
                        for g in tree.get("globals", ())
                    )
                )
            ):
                # The word rode behind a bare value-optional global
                # (`--install-completion zsh`, a plugin's `--profile out.json`
                # — required-value globals already refused in _parse_globals):
                # teach the attachment, never "unknown task 'zsh'".
                raise ChainError(
                    _expects_value(None, prev, _GLOBAL_HINT.get(prev, "VALUE"), token)
                )
            if prev_group is not None:
                prev_path, prev_node = prev_group
                if part in prev_node["groups"] or part in prev_node["tasks"]:
                    raise ChainError(
                        f"nested tasks use dots: '{prev_path}.{token}', "
                        f"not '{prev_path} {token}'"
                    )
        bad = ".".join([*path, part])
        hint = _did_you_mean(token, flat_addresses(tree))
        scope = f"{'.'.join(path)} has" if path else "know"
        known = ", ".join(_children(node, f"{'.'.join(path)}." if path else ""))
        raise ChainError(
            f"no task at {bad!r}{hint} ({scope}: {known})"
            if path
            else f"expected a task name, got {token!r}{hint} ({scope}: {known})"
        )

    # The whole token named a group. Runnable — one with `@group.default` —
    # resolves to its default action: `fm lint` / `fm lint --fix` run it,
    # `path` stays the group's. The default's own signature decides what a
    # trailing bare token means: a declared positional consumes it (a value
    # wins — every child keeps its dotted spelling), otherwise it opens a
    # fresh head on the next pass.
    if "default" in node:
        return node["default"], path, node, i + 1

    # A namespace group is never a segment target. Before refusing, look
    # ahead: if the following words walk to something runnable, the user
    # spelled a nested address with spaces — teach the dotted form, longest
    # resolvable path first (`fm tools sync` → 'tools.sync').
    walk_node, walk_path, j = node, list(path), i + 1
    while j < len(argv):
        nxt = argv[j]
        if nxt in walk_node["groups"]:
            walk_node = walk_node["groups"][nxt]
            walk_path.append(nxt)
            j += 1
            continue
        if nxt in walk_node["tasks"]:
            walk_path.append(nxt)
            spaced = " ".join(walk_path)
            raise ChainError(
                f"nested tasks use dots: '{'.'.join(walk_path)}', not '{spaced}'"
            )
        break
    if len(walk_path) > len(path) and "default" in walk_node:
        spaced = " ".join(walk_path)
        raise ChainError(
            f"nested tasks use dots: '{'.'.join(walk_path)}', not '{spaced}'"
        )
    dotted = ".".join(walk_path)
    known = ", ".join(_children(walk_node, f"{dotted}."))
    raise ChainError(
        f"{dotted!r} is a group, not a task — name one of its tasks (know: {known})"
    )


def _default_notes(
    seg: Segment,
    group_node: dict[str, Any],
    fixed: list[dict[str, Any]],
    rest: dict[str, Any] | None,
) -> None:
    """Advisory notes for a runnable group's positional values.

    The grammar is deterministic — a positional wins — but consequence 3
    carves a hole in the teaching error: `fm lint python` is a *valid* parse
    when lint's default takes a positional, so the git-habit space form runs
    instead of teaching. These stderr notes close the hole: an exact child
    name gets the dotted pointer, and an edit-distance-1 near miss (which
    used to be an "unknown task" error and would now silently filter on a
    pattern matching nothing) names the nearest subtask. A path-shaped value
    (`fm lint ./python`) is the documented quiet spelling — a legitimate
    value that happens to equal a child name is not nagged forever.
    """
    children = set(group_node["groups"]) | set(group_node["tasks"])
    if not children:
        return
    values: list[str] = list(seg.variadic)
    params = list(fixed)
    if rest is not None and rest["kind"] == "positional":
        params.append(rest)
    for p in params:
        got = seg.values.get(p["name"])
        if isinstance(got, str):
            values.append(got)
        elif isinstance(got, list):
            values.extend(v for v in got if isinstance(v, str))
    dotted = seg.task
    for value in values:
        if "/" in value or value.startswith((".", "~")):
            continue  # path-shaped: the quiet spelling
        if value in children:
            seg.notes.append(
                f"note: ran {dotted}'s default with {value!r}; "
                f"for the subtask: {{prog}} {dotted}.{value}"
            )
        elif (
            near := next((c for c in sorted(children) if _close1(value, c)), None)
        ) is not None:
            seg.notes.append(
                f"note: {value!r} ran as {dotted}'s positional; "
                f"nearest subtask: {{prog}} {dotted}.{near}"
            )


def split_chain(
    tree: dict[str, Any], argv: list[str]
) -> tuple[list[str], list[Segment]]:
    """Split *argv* into leading globals and a list of resolved segments."""
    plugin = {
        "--" + g["name"]: ("option?" if "bare" in g else g["kind"])
        for g in tree.get("globals", ())
    }
    globals_, i = _parse_globals(argv, 0, plugin=plugin)
    segments: list[Segment] = []
    prev_group: tuple[str, dict[str, Any]] | None = None

    while i < len(argv):
        task, path, group_node, i = _resolve_head(tree, argv, i, prev_group)
        prev_group = (".".join(path), group_node) if group_node is not None else None

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
            if p["kind"] == "positional" and not p.get("multiple")
        ]
        rest = next(
            (
                p
                for p in task["params"]
                if (p["kind"] == "positional" and p.get("multiple"))
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
                    _validate(seg.task, rest, tok)  # eager, like every positional
                    seg.variadic.append(tok)
                else:
                    _consume_positional(seg, tree, rest, tok)
                rest_count += 1
                i += 1
            else:
                break  # arity satisfied: the next word starts a new segment

        missing = [f"<{p['name']}>" for p in fixed[filled:] if not p.get("optional")]
        if rest is not None and rest["kind"] == "positional" and rest_count == 0:
            missing.append(f"<{rest['name']}>")
        if missing:
            raise ChainError(
                f"{seg.task}: missing required positional(s): {', '.join(missing)}"
            )

        # Required options — a mapping or bool with no default. (Dicts are only
        # ever options; a bool is a flag, so teach both --x and --no-x forms.)
        missing_opts = [
            _required_label(p)
            for p in task["params"]
            if p.get("required") and p["name"] not in seg.values
        ]
        if missing_opts:
            raise ChainError(
                f"{seg.task}: missing required option(s): {', '.join(missing_opts)}"
            )
        for spec in task["params"]:
            if (group := spec.get("group")) is None:
                continue
            given = seg.values.get(spec["name"])
            if given is None:
                continue  # absent: the default stands
            _check_arity(seg.task, spec, group, len(given))
        if group_node is not None:
            _default_notes(seg, group_node, fixed, rest)
        segments.append(seg)

    return globals_, segments


def _consume_option(
    seg: Segment, opts: dict[str, dict[str, Any]], argv: list[str], i: int
) -> int:
    tok = argv[i]
    name = tok.split("=", 1)[0]
    negated = False
    p = opts.get(name)
    if p is None and name.startswith("--no-"):
        candidate = "--" + name[len("--no-") :]
        if candidate in opts and opts[candidate]["kind"] == "flag":
            p, negated = opts[candidate], True
    if p is None:
        if (misplaced := _misplaced_global(name)) is not None:
            raise ChainError(f"{seg.task}: {misplaced}")
        forms = list(opts) + [
            f"--no-{opts[k]['name']}" for k in opts if opts[k]["kind"] == "flag"
        ]
        hint = _did_you_mean(name, forms) or (
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

    # value-bearing option: the value is always `=`-attached
    if "=" not in tok:
        follower = (
            argv[i + 1]
            if i + 1 < len(argv)
            and argv[i + 1] not in ("--", "+")
            and not argv[i + 1].startswith("-")
            else None
        )
        raise ChainError(_expects_value(seg.task, name, "VALUE", follower))
    value = tok.split("=", 1)[1]
    i += 1
    if p.get("mapping"):
        for pair in _values(p, value):
            _consume_pair(seg, p, cli, pair)
    elif p.get("multiple"):
        for part in _values(p, value):
            _validate(seg.task, p, part, at=len(seg.values.get(cli, ())))
            seg.values.setdefault(cli, []).append(part)
    else:
        _validate(seg.task, p, value)
        seg.values[cli] = value
    return i


def _values(p: dict[str, Any], value: str) -> list[str]:
    """Comma-split parts of a list/dict value, unless the param opts out.

    Called only for collection params, so splitting is the default; a `nosplit`
    param (values may contain commas) takes the whole token verbatim.
    """
    if p.get("nosplit"):
        return [value]
    return [part for part in value.split(",") if part] or [value]


def _consume_pair(seg: Segment, p: dict[str, Any], cli: str, pair: str) -> None:
    """Parse and validate one `KEY=VALUE` token for a dict parameter."""
    if "=" not in pair:
        raise ChainError(f"{seg.task}: --{cli} expects KEY=VALUE (got {pair!r})")
    key, value = pair.split("=", 1)
    bounds = (p.get("min"), p.get("max")) if "min" in p or "max" in p else None
    _check(seg.task, f"--{cli} key", key, types=p.get("key_types"))
    _check(
        seg.task,
        f"--{cli} value",
        value,
        choices=p.get("value_choices"),
        types=p.get("value_types"),
        path=p.get("path"),
        bounds=bounds,
    )
    seg.values.setdefault(cli, []).append((key, value))


def _is_address(tree: dict[str, Any], tok: str) -> bool:
    """Whether *tok* walks the tree to a task or group — the "looks like the
    next task" peek for choice-positional errors. Loose on purpose: it only
    shapes an error message, so a namespace group counts too."""
    node = tree
    parts = tok.split(".")
    if "" in parts:
        return False
    for pos, part in enumerate(parts):
        if part in node["groups"]:
            node = node["groups"][part]
        elif part in node["tasks"]:
            return pos == len(parts) - 1
        else:
            return False
    return True


def _consume_positional(
    seg: Segment, tree: dict[str, Any], p: dict[str, Any], tok: str
) -> None:
    if (
        "choices" in p
        and tok not in p["choices"]
        and not _suggest_only(p["choices"], p.get("dynamic"))
        and not (p.get("types") and _coerce.coerce_scalar(tok, p["types"])[0])
        and _is_address(tree, tok)
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
