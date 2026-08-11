"""Completion hot path — the code every TAB press runs.

Hard constraints: standard library only, no framework import, no user-code
import. The whole budget is ~50 ms including interpreter startup, so the work
here is one file read + JSON parse + tree walk.

Two ways in:

* `footman --complete [--] WORD [WORD ...]` — the portable path. The console
  script dispatches here *before importing anything else* and derives the cache
  location from the current directory.
* `python _complete.py --manifest PATH -- WORD [WORD ...]` — the baked-in
  path. A generated completion script invokes the interpreter directly on this
  file with the manifest location hard-coded, skipping the console-script shim
  and the `footman` package import entirely.

WORDs are the command line after the program name; the last word is the partial
being completed ("" when the cursor follows a space).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# The literal-False spelling both checkers honour without importing `typing`:
# annotations here are strings (`from __future__ import annotations`), so the
# hot path never pays for the import — a TAB press stays one file read away.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

# Hardcoded mirror of split.GLOBALS — the hot path can't import split (it
# would mount the whole package). `test_completion_globals_mirror_split`
# rebuilds this FROM split.GLOBALS, so renaming a global fails CI. The
# grammar is lexical — every value is `=`-attached, every dash token
# self-contained — so the walk needs no arity: only the names (to suggest)
# and the value surfaces below (to complete).
_GLOBALS = frozenset(
    {
        "--help", "-h", "--version", "-V", "--list", "-l", "--tree", "--sort",
        "--no-sort", "--all", "-a",
        "--where", "--describe", "--plugins", "--dry-run", "-n",
        "--keep-going", "-k",
        "--fail-fast", "--sequential", "-s", "--no-sequential",
        "--jobs", "-j", "--yes", "-y",
        "--no-input", "--input", "--quiet", "-q", "--verbose", "-v", "--color",
        "--no-color", "--no-progress", "--progress", "--no-uv", "--uv",
        "--json", "--timings",
        "--directory", "-C", "--tasks-file", "-f", "--config",
        "--install-completion", "--setup-completion", "--uninstall-completion",
    }
)  # fmt: skip
# Value positions that are file paths. footman can't know the filesystem from a
# cached manifest (and shouldn't try), so the resolver signals these and the
# shell hooks defer to native file completion.
_GLOBAL_FILES = frozenset({"--directory", "-C", "--tasks-file", "-f", "--config"})
_FILES = "\x00files"  # internal sentinel: complete() -> complete_cli()
_EXIT_FILES = 100  # complete_cli exit code the hooks read as "complete files"
_FILES_CSV = "\x00files-csv"  # a comma-splitting path value, mid-list
_EXIT_FILES_CSV = 101  # "complete files after the last comma" exit code


_DYNAMIC = "\x00dynamic"  # internal sentinel: a dynamic completer, recompute fresh
# Mirror of manifest.SCHEMA_VERSION — the hot path can't import manifest.py.
# A cache written by a different footman gets rebuilt, never walked: the first
# TAB after an upgrade serves correct candidates instead of a traceback.
# `test_completion_schema_mirrors_manifest` keeps the two from drifting.
_SCHEMA = 6
_DYNAMIC_TIMEOUT = 2.0  # seconds to wait for a fresh dynamic completer subprocess
# Seconds to wait for a first-time cwd manifest build. A cold build measures
# ~100-150 ms — footman's own fat tasks.py included — so this is roughly
# seven times the slowest thing measured, and the projects it still cannot
# cover are the ones importing something heavy at module level. There the
# shorter bound is the kinder failure: a first TAB that answers nothing and a
# second one that answers instantly (the build was detached and lands
# anyway), rather than a shell that appears to hang for three seconds.
_COLD_TIMEOUT = 1.0
_SHELLS = ("bash", "zsh", "fish", "pwsh", "nushell")
_GLOBAL_CHOICES = {
    "--install-completion": _SHELLS,
    "--setup-completion": _SHELLS,
    "--uninstall-completion": _SHELLS,
    "--color": ("always", "never", "auto"),
}


def _rejoin(words: list[str]) -> tuple[list[str], bool]:
    """Undo bash's `=` word-splitting: `--opt`, `=`, `val` → `--opt=val`.

    bash breaks the completion line on `=` (COMP_WORDBREAKS), so an attached
    value arrives as two or three words; zsh/fish/pwsh/nushell pass the token
    whole. Joining here gives every shell one shape — self-contained tokens,
    exactly the grammar's own reading — with the *partial* folded into its
    option (`--opt=va`), so one value-completion branch serves every shell.

    The second return says whether the final word was folded — i.e. the shell
    split the token, so it is completing the bare value and candidates must
    not carry the `--opt=` prefix; an unsplit shell replaces the whole token
    and needs full `--opt=value` candidates.
    """
    out: list[str] = []
    merged_last = False
    i = 0
    while i < len(words):
        word = words[i]
        if word == "=" and out and out[-1].startswith("-") and "=" not in out[-1]:
            out[-1] += "="
            i += 1
            if i < len(words):
                out[-1] += words[i]
                i += 1
            merged_last = i >= len(words)
            continue
        out.append(word)
        i += 1
        merged_last = False
    return out, merged_last


def _csv_head(p: dict[str, Any], value: str) -> tuple[str, str]:
    """Split *value* at its last comma when *p* comma-splits.

    A collection value completes one comma-separated item at a time: the
    typed items stay in place as a head every candidate re-attaches, and
    matching runs on the tail alone. A scalar or `nosplit` value (commas
    literal) keeps the whole token — empty head, the value as the tail.
    """
    if p.get("multiple") and not p.get("nosplit") and "," in value:
        head, _, tail = value.rpartition(",")
        return head + ",", tail
    return "", value


def _files_sentinel(p: dict[str, Any], head: str) -> str:
    """The hand-off sentinel for a path value, carrying any `matching()` glob.

    footman never touches the filesystem to complete a path — it answers from
    a cached manifest — so the shell's own file completion does the walking.
    The glob rides out with the signal so the shell can narrow what it walks:
    `complete_cli` prints it on stdout beside exit 100/101, and a hook reads
    "one line of stdout with exit 100" as "files matching this". An empty
    stdout is every file, which is what every hook did before there was a
    pattern to send — so an older hook against a newer footman degrades to
    exactly its old behaviour rather than breaking.
    """
    base = _FILES_CSV if head else _FILES
    glob = p.get("glob")
    # Tab-separated, not concatenated: `_FILES_CSV` starts with `_FILES`, so
    # a glob beginning "-csv" would otherwise turn a plain path value into a
    # comma-splitting one. The reader compares the tag exactly.
    return f"{base}\t{glob}" if isinstance(glob, str) and glob else base


def _choice_tokens(p: dict[str, Any], partial: str) -> list[str]:
    """*p*'s choice values as whole completion tokens against *partial*.

    Mid-list in a comma-splitting value, each choice arrives as
    `head+choice` (minus the items already typed), so the caller's
    generic startswith filter keeps working on whole tokens.
    """
    head, _ = _csv_head(p, partial)
    if not head:
        return list(p.get("choices", []))
    given = partial.split(",")[:-1]
    return [head + c for c in p.get("choices", []) if c not in given]


def _attached_value(
    p: dict[str, Any],
    optname: str,
    valpart: str,
    bash_split: bool,
    path: list[str],
) -> list[str]:
    """Candidates for an attached `--opt=value` partial — one answer for a
    task option and a mounted plugin's global (its baked entry has the same
    shapes), which used to be two verbatim copies of this walk.

    A comma-splitting value mid-list completes its tail item alone, the
    typed head riding every candidate; a path value hands off to the
    shell's file completion; a dynamic completer recomputes fresh, its
    emission prefix chosen by the shell's word shape. *path* is the segment
    path a task option rides with — empty for a global, which `_suggest`
    reads as "address the option by name".
    """
    head, cur = _csv_head(p, valpart)
    if "path" in p.get("types", []):
        return [_files_sentinel(p, head)]
    if p.get("dynamic"):  # recompute fresh, never the baked snapshot
        prefix = head if bash_split else f"{optname}={head}"
        return [_DYNAMIC, cur, prefix, p["name"], *path]
    given = valpart.split(",")[:-1] if head else []
    choices = [c for c in p.get("choices", ()) if c.startswith(cur) and c not in given]
    return (
        [head + c for c in choices]
        if bash_split
        else [f"{optname}={head}{c}" for c in choices]
    )


def _consume_globals(prior: list[str]) -> list[str]:
    """Strip the leading global options; the rest is the task chain.

    Purely lexical, like `split._parse_globals`: every dash token is
    self-contained (values are `=`-attached), so the walk is a scan for the
    first bare word — no arity table.
    """
    i = 0
    while i < len(prior) and prior[i].startswith("-") and prior[i] != "--":
        i += 1
    return prior[i:]


class _Segment:
    """Walk state for one chain segment (mirrors the splitter's rules)."""

    def __init__(self, task: dict[str, Any] | None = None) -> None:
        self.task = task
        self.opts: dict[str, dict[str, Any]] = {}
        self.fixed: list[dict[str, Any]] = []
        self.rest: dict[str, Any] | None = None
        self.filled = 0
        self.used: set[str] = set()  # options already given in this segment
        if task is not None:
            params = task["params"]
            self.opts = {
                "--" + p["name"]: p for p in params if p["kind"] in ("flag", "option")
            }
            self.fixed = [
                p for p in params if p["kind"] == "positional" and not p.get("multiple")
            ]
            self.rest = next(
                (
                    p
                    for p in params
                    if (p["kind"] == "positional" and p.get("multiple"))
                    or p["kind"] == "variadic"
                ),
                None,
            )


def _has_any(node: dict[str, Any]) -> bool:
    """Whether a group leads anywhere — whether TAB has something to offer.

    `hidden` is not the question. It keeps a task out of the *listings*, the
    prose a human reads to learn what a repo does; completion is the other
    thing — you are already typing a name, and a machine-facing address is
    exactly the one worth being spelled for you.

    `needs_project` is a different question and does count: the manifest was
    built where there is no project, so a task marked here cannot run from
    here, and completing to it would spell out a name that only refuses. So
    the groups TAB skips are the empty ones and the ones with nothing
    runnable left. Dict reads over the manifest a TAB already loaded: the hot
    path imports no framework.
    """
    if node.get("default") is not None and not node["default"].get("needs_project"):
        return True
    if any(not spec.get("needs_project") for spec in node["tasks"].values()):
        return True
    return any(_has_any(sub) for sub in node["groups"].values())


def _cand(address: str, summary: str) -> str:
    """`address\\tdescription` when there is a help line, else the address.

    The tab is the backward-safe wire format: shells that render descriptions
    (zsh, fish) split on it; bash (and others) keep the first field. Options and
    choice values carry no help, so they pass through bare.
    """
    return f"{address}\t{summary}" if summary else address


def _opt_tokens(name: str, spec: dict[str, Any]) -> list[str]:
    """The spellings of one option, as the menu should offer them.

    A value-taking option has two legal shapes and the menu shows both:
    `--opt`, the bare mention that stands for its default, and `--opt=`,
    which is the *only* way to pass a value, because every value in this
    grammar is attached. Offering the bare name alone left the value path
    undiscoverable — a dynamic completer's candidates could only be reached
    by knowing to type `=` first, which is exactly the internal knowledge
    completion exists to spare you. Offering `--opt=` alone would have hidden
    bare mention, which is a feature, not an accident.

    A flag takes no value at either default — `--fix=true` is a taught
    refusal and `--no-fix` is the off spelling — so it has one shape only.
    """
    return [name, f"{name}="] if spec.get("kind") == "option" else [name]


def _walk_address(
    tree: dict[str, Any], token: str
) -> tuple[str, dict[str, Any], list[str]] | None:
    """Resolve one dotted token to `("task"|"group", node, path)`, or None."""
    parts = token.split(".")
    if "" in parts:
        return None
    node, path = tree, []
    for pos, part in enumerate(parts):
        last = pos == len(parts) - 1
        if part in node["groups"]:
            node = node["groups"][part]
            path.append(part)
            if last:
                return ("group", node, path)
        elif part in node["tasks"] and last:
            return ("task", node["tasks"][part], [*path, part])
        else:
            return None
    return None


def _leaf_fallback(tree: dict[str, Any], partial: str) -> list[str]:
    """Nested candidates whose *last* segment starts with *partial*.

    The rescue for "I know the task, not where it lives": when a typed token
    prefix-matches no top-level segment, complete against last segments over
    the whole tree instead — `serve` → `docs.serve`. Only nested entries
    (a top-level match would have answered already, so the zero-match guard
    means this can never pollute a first tab or a valid descent).
    """
    out: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name, spec in node["tasks"].items():
            if spec.get("needs_project"):
                continue  # nothing here can run it; completing to it teases
            if prefix and name.startswith(partial):
                out.append(_cand(f"{prefix}{name}", spec.get("help", "")))
        for name, sub in node["groups"].items():
            if not _has_any(sub):
                continue  # an empty subtree suggests nothing, at any depth
            if prefix and name.startswith(partial) and "default" in sub:
                out.append(
                    _cand(f"{prefix}{name}", sub["default"].get("help") or sub["help"])
                )
            walk(sub, f"{prefix}{name}.")

    walk(tree, "")
    return out


def _address_candidates(tree: dict[str, Any], partial: str) -> list[str]:
    """Path-style completion over the tree: the `.` is footman's `/`.

    One emission rule, `ls -F` style: candidates sit one segment beyond the
    typed prefix, and a namespace-group candidate always carries its trailing
    dot (the descend-vs-run signal). A runnable group emits itself *plus* its
    dotted children, so the common prefix is the group and the next keystroke
    (space or `.`) is the stop-or-descend choice. On a unique namespace match
    the rule skips ahead to the children — the candidate set stays non-unique,
    so no shell ever forces a space after `docs.`.

    Two generosities, both completion-only (the runtime resolver stays
    strict, so scripts cannot rot): segment-wise abbreviation walks each
    typed segment by unique prefix, `fm t.sy⇥` → `tools.sync`,
    the way zsh expands `/u/l/b`; and on zero top-level matches the
    leaf-name fallback completes against last segments instead
    (`fm serve⇥` → `docs.serve`).
    """
    *bases, leaf = partial.split(".")
    node, prefix = tree, ""
    for seg in bases:
        if seg in node["groups"]:  # an exact name always wins
            node = node["groups"][seg]
            prefix = f"{prefix}{seg}."
            continue
        matches = [n for n in node["groups"] if n.startswith(seg)] if seg else []
        if len(matches) == 1:  # unique abbreviation: expand and keep walking
            node = node["groups"][matches[0]]
            prefix = f"{prefix}{matches[0]}."
            continue
        if len(matches) > 1:
            # Ambiguous segment: expand up to it and list that level's
            # matches — the user picks, then keeps tabbing.
            return [_cand(f"{prefix}{m}.", node["groups"][m]["help"]) for m in matches]
        return []  # a segment that matches nothing: not an address
    while True:
        # `hidden` is a listings word, not a completion one: every address the
        # runtime will accept is offered here, machine-facing ones included.
        # What TAB skips is a group leading nowhere (one dict walk — the hot
        # path holds).
        groups = [
            n
            for n in node["groups"]
            if n.startswith(leaf) and _has_any(node["groups"][n])
        ]
        tasks = [n for n in node["tasks"] if n.startswith(leaf)]
        if (
            len(groups) == 1
            and not tasks
            and "default" not in node["groups"][groups[0]]
        ):
            # Unique namespace match: complete straight through it, the way
            # zsh descends a lone subdirectory.
            prefix = f"{prefix}{groups[0]}."
            node = node["groups"][groups[0]]
            leaf = ""
            continue
        break
    out: list[str] = []
    for name in groups:
        sub = node["groups"][name]
        default = sub.get("default")
        if default is not None:
            # Runnable group: itself (described by what "stop here" runs),
            # then one level of dotted children for the descent.
            out.append(_cand(f"{prefix}{name}", default.get("help") or sub["help"]))
            for child, csub in sub["groups"].items():
                out.append(_cand(f"{prefix}{name}.{child}.", csub["help"]))
            for child, spec in sub["tasks"].items():
                if child == "default":
                    continue  # the bare row above IS this action
                if spec.get("needs_project"):
                    continue
                out.append(_cand(f"{prefix}{name}.{child}", spec.get("help", "")))
        else:
            out.append(_cand(f"{prefix}{name}.", sub["help"]))
    for name in tasks:
        spec = node["tasks"][name]
        if spec.get("needs_project"):
            continue
        out.append(_cand(f"{prefix}{name}", spec.get("help", "")))
    if not out and not bases and leaf:
        return _leaf_fallback(tree, leaf)
    return out


def complete(tree: dict[str, Any], words: list[str]) -> list[str]:
    """Resolve completion candidates for *words* against a manifest *tree*.

    Chain-aware: the walk tracks segments the way the splitter would — a
    dotted address names each segment's task in one word, then exact
    positional arity, then a trailing multiple/variadic consumer, then the
    next bare word starts a new segment from the root. So in
    `fm format lint --fi<TAB>` the options offered are *lint's*, and once a
    task's arity is satisfied a bare TAB offers the next task names too.
    `+` resets a segment explicitly; after `--` everything belongs to the
    passthrough, so there is nothing to offer.
    """
    # bash splits attached values on `=`; rejoin so every shell presents the
    # grammar's own shape — self-contained tokens (`--opt=val` is one word,
    # and a value being typed folds into its option as the partial).
    # `bash_split` remembers the fold: that shell completes the bare value,
    # the others replace the whole token.
    rejoined, bash_split = _rejoin(words or [""])
    *prior, partial = rejoined or [""]

    # Leading global options bind before the task walk, exactly as the
    # splitter consumes them: a lexical scan to the first bare word.
    at_globals = not prior  # nothing typed yet but globals (or nothing)
    prior = _consume_globals(prior)
    at_globals = at_globals or not prior

    # An attached global value in progress (`--color=al<TAB>`, `-C=src/`):
    # the token is self-contained, so the partial says everything.
    if at_globals and partial.startswith("-") and "=" in partial:
        optname, _, valpart = partial.partition("=")
        if optname in _GLOBAL_FILES:
            return [_FILES]  # hand the value part to the shell's file completion
        entry = next(
            (g for g in tree.get("globals", ()) if "--" + g["name"] == optname),
            None,
        )
        if entry is not None:
            # A mounted plugin's global completes from its baked entry — the
            # same shapes a task parameter bakes, answered by the same walk.
            return _attached_value(entry, optname, valpart, bash_split, [])
        choices = [c for c in _GLOBAL_CHOICES.get(optname, ()) if c.startswith(valpart)]
        return choices if bash_split else [f"{optname}={c}" for c in choices]

    node, seg = tree, _Segment()
    path: list[str] = []  # the dotted segments of the current head

    for word in prior:
        if word == "--":
            return []  # passthrough: the words after this aren't ours
        if word == "+":  # explicit segment boundary
            node, seg, path = tree, _Segment(), []
            continue
        if seg.task is None:
            # A head word is one dotted address, resolved from the root —
            # landing on a group parks there (a runnable group's options and
            # the next head both stay reachable); landing on a task opens
            # its tail; anything else is ignored, as the splitter would err.
            resolved = _walk_address(tree, word)
            if resolved is not None:
                kind, hit, path = resolved
                if kind == "task":
                    seg = _Segment(hit)
                else:
                    node = hit
            continue
        # Inside a task's tail: every token is self-contained — an option
        # word (attached value or not) never consumes its neighbour.
        name = word.split("=", 1)[0]
        if name in seg.opts:
            seg.used.add(name)
            continue
        if name.startswith("--no-") and "--" + name[len("--no-") :] in seg.opts:
            seg.used.add("--" + name[len("--no-") :])
            continue
        if word.startswith("-"):
            continue
        # A bare word: a required positional, then the trailing consumer,
        # then — arity satisfied — the start of the next segment (chains
        # always resolve from the root).
        if seg.filled < len(seg.fixed):
            seg.filled += 1
            continue
        if seg.rest is not None:
            continue
        node, seg, path = tree, _Segment(), []
        resolved = _walk_address(tree, word)
        if resolved is not None:
            kind, hit, path = resolved
            if kind == "task":
                seg = _Segment(hit)
            else:
                node = hit

    if seg.task is None:
        if node is not tree:
            # A prior word parked on a group. Runnable: its default's
            # flags/options, plus fresh heads — the default's arity is
            # satisfied, so the next bare word starts a new segment. A
            # namespace group has no valid continuation as a fresh word:
            # stay silent, the way the splitter refuses it.
            if "default" not in node:
                return []
            out = [
                _cand(token, p.get("doc", ""))
                for p in node["default"]["params"]
                if p["kind"] in ("flag", "option")
                and ("--" + p["name"]).startswith(partial)
                for token in _opt_tokens("--" + p["name"], p)
            ]
            if not partial.startswith("-"):
                out += _address_candidates(tree, partial)
            return out
        # Path-style over the whole word: the partial is a dotted address
        # in progress, and candidates sit one segment beyond it.
        out = [] if partial.startswith("-") else _address_candidates(tree, partial)
        # fm's own global options bind before the first task, so offer them when
        # a flag is being typed at the root (`not prior` ⇒ nothing but globals
        # preceded). A bare `<TAB>` still lists only tasks — globals would be
        # noise there.
        if not prior and partial.startswith("-"):
            # The core flags are this module's own frozenset (it may not import
            # `_split`), but their words ride in the manifest — `_manifest`
            # writes them from the one table that declares them.
            said = tree.get("global_help")
            lines: dict[str, str] = said if isinstance(said, dict) else {}
            out += [
                _cand(g, lines.get(g, ""))
                for g in sorted(_GLOBALS)
                if g.startswith(partial)
            ]
            for g in tree.get("globals", ()):
                summary = str(g.get("help", ""))
                if (flag := "--" + g["name"]).startswith(partial):
                    out += [_cand(t, summary) for t in _opt_tokens(flag, g)]
                # A bool answers to `--no-x` too — offer the off spelling
                # exactly as the splitter accepts it, described by what it
                # turns off: one option, read from the other end.
                if g["kind"] == "flag" and (
                    (no := "--no-" + g["name"]).startswith(partial)
                ):
                    out.append(_cand(no, summary))
        return out

    # An attached `--opt=value` partial: the one value position the grammar
    # has. A Path-typed value hands off to file completion; a dynamic
    # completer recomputes fresh, its emission prefix chosen by the shell's
    # word shape; choices likewise come back bare (bash completes the value
    # word) or as full `--opt=choice` tokens (whole-token shells).
    if partial.startswith("-") and "=" in partial:
        optname, _, valpart = partial.partition("=")
        opt = seg.opts.get(optname)
        if opt is not None and opt["kind"] == "option":
            return _attached_value(opt, optname, valpart, bash_split, path)

    # A path-typed positional (or trailing consumer): once the partial is a
    # value being typed rather than an option, hand it to native file
    # completion — the same handoff a Path-typed option value gets above.
    # `-` still reaches the options below, so they stay one keystroke away.
    if not partial.startswith("-"):
        pending = seg.fixed[seg.filled] if seg.filled < len(seg.fixed) else seg.rest
        if pending is not None:
            head, cur = _csv_head(pending, partial)
            if "path" in pending.get("types", []):
                return [_files_sentinel(pending, head)]
            if pending.get("dynamic"):  # recompute fresh, never the baked snapshot
                return [_DYNAMIC, cur, head, pending["name"], *path]

    # Option position: this task's flags/options — minus the ones already
    # given, unless the param legitimately repeats — plus what the next bare
    # word could be: the pending positional's choices, the trailing
    # consumer's choices, or (arity satisfied) the next segment's addresses.
    candidates = [
        name
        for name, p in seg.opts.items()
        if name not in seg.used or p.get("multiple") or p.get("mapping")
    ]
    next_heads: list[str] = []
    if seg.filled < len(seg.fixed):
        candidates += _choice_tokens(seg.fixed[seg.filled], partial)
        if seg.fixed[seg.filled].get("optional") and not partial.startswith("-"):
            # The boundary documents itself: an optional positional's slot
            # offers `+` — run without it, the next word starts a task.
            candidates.append("+")
    elif seg.rest is not None:
        candidates += _choice_tokens(seg.rest, partial)
    elif not partial.startswith("-"):
        next_heads = _address_candidates(tree, partial)
    seen: dict[str, None] = {}
    for c in candidates:
        if c.startswith(partial):
            seen.setdefault(c)
    # An option carries its doc("...") text when the task author wrote one;
    # choice values stay bare; next-segment addresses arrive pre-described.
    # Same tab-separated wire format either way — `_cand` is that format.
    rows: list[str] = []
    for c in seen:
        spec = seg.opts.get(c)
        if spec is None:  # a choice value, `+`, or a next-segment address
            rows.append(_cand(c, ""))
            continue
        rows += [_cand(t, spec.get("doc", "")) for t in _opt_tokens(c, spec)]
    return rows + next_heads


def _load_manifest(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _maybe_refresh(path: str, data: dict[str, Any]) -> None:
    """Stale-while-revalidate: if the manifest is older than its baked
    `completion_max_age`, bump its mtime and spawn a detached rebuild for *next*
    time, then return. Never blocks the TAB (the rebuild imports the package and
    shells completers) and never surfaces an error.
    """
    max_age = data.get("completion_max_age")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        return  # disabled (off, or an in-memory/`-f` manifest with no age baked)
    try:
        if time.time() - os.stat(path).st_mtime <= max_age:
            return
        # Bump the mtime *before* spawning: resets the clock even if the rebuild
        # is a no-op (sync_manifest only writes on change), and storm-guards
        # concurrent TABs so only the first in an aged window spawns.
        os.utime(path)
    except OSError:
        return
    _spawn_refresh()


def _spawn_refresh(override: str | None = None) -> None:
    # override set → rebuild that one -f file's (cwd, file) manifest; else the
    # cwd cascade. The path rides as an argv word (not baked into the -c script),
    # so a path with spaces or quotes needs no escaping.
    # The brand's resolved locations ride along as argv words too: the child
    # inherits the environment but not the brand, and must write this CLI's
    # cache rather than stock footman's.
    from footman import _paths

    where = _paths.child_args()
    if override:
        script = (
            "import sys; from footman import _refresh; "
            "_refresh.refresh_source(*sys.argv[1:])"
        )
        cmd = [sys.executable, "-c", script, override, *where]
    else:
        script = (
            "import sys; from footman import _refresh; "
            "_refresh.refresh_cwd(*sys.argv[1:])"
        )
        cmd = [sys.executable, "-c", script, *where]
    detach(cmd)


def detach(cmd: list[str]) -> None:
    """Spawn *cmd* fully detached, swallowing failure — the one copy of the
    background-child dance, shared with the collector's spawn in `_app` (a
    background child must never break the foreground that spawned it).

    Stdlib-only, like everything here, so both the hot path and the
    execution path may call it.
    """
    null = subprocess.DEVNULL
    try:
        if os.name == "nt":
            # Not DETACHED_PROCESS: with Windows Terminal as the default
            # terminal (Windows 11), a console-less console app is handed a
            # *visible* terminal window — one per spawn, popping over the
            # shell mid-completion — and any console grandchild allocates
            # another. CREATE_NO_WINDOW gives the child a hidden console
            # instead: nothing shows, and its children inherit the hiding.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            subprocess.Popen(
                cmd, stdin=null, stdout=null, stderr=null, creationflags=flags
            )
        else:
            subprocess.Popen(
                cmd, stdin=null, stdout=null, stderr=null, start_new_session=True
            )
    except OSError:
        return  # a detached child must never break what spawned it


def _cold_build(manifest: str, override: str | None) -> dict[str, Any] | None:
    """Build a cold-cache manifest once, then load it.

    The first <kbd>Tab</kbd> in a fresh directory has nothing cached. Rather than
    answer empty, spawn the same builder a real run uses and wait — bounded — for
    it to land, then serve it (now cached for next time). *override* picks the
    tree: a finished `-f <file>` builds that file's (cwd, file) manifest, else the
    cwd cascade. Import-free on the hot path: it spawns rather than imports. A
    slow `tasks.py` degrades to empty, and because the build was detached it still
    finishes for the next TAB, so no keystroke ever hangs on it.
    """
    if override is not None:
        from pathlib import Path

        if not Path(override).expanduser().is_file():
            return None  # a still-being-typed or missing -f value: nothing to build
    _spawn_refresh(override)
    deadline = time.monotonic() + _COLD_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.03)
        data = _load_manifest(manifest)
        # The schema check matters here too: on the post-upgrade TAB the
        # *stale* file is still on disk, and without it the poll would win
        # the race against the rebuild and hand the old tree back.
        if (
            isinstance(data, dict)
            and isinstance(data.get("tree"), dict)
            and data.get("schema") == _SCHEMA
        ):
            return data
    return None


def _leading_global_value(args: list[str], names: tuple[str, ...]) -> str | None:
    """The value of the first of *names* among the leading globals, or None.

    Walks only the leading globals — stopping at the first bare word — the
    same lexical scan the resolver uses; values are always `=`-attached
    (rejoined first, so bash's split forms read the same).
    """
    for tok in _rejoin(list(args))[0]:
        if not tok.startswith("-") or tok == "--":
            break  # the first bare word — a task name (or its partial)
        if any(tok.startswith(n + "=") for n in names):
            return tok.split("=", 1)[1]
    return None


def _tasks_file_from(args: list[str]) -> str | None:
    """The `-f`/`--tasks-file` value among the leading globals, or None."""
    return _leading_global_value(args, ("-f", "--tasks-file"))


def _emit(lines: list[str]) -> None:
    """Write completion candidates, one per line, LF-terminated.

    LF, always. The completion protocol is footman's own, and on Windows
    text-mode stdout translates every "\\n" to "\\r\\n": a shell that reads lines
    literally (git-bash's `read`) keeps the carriage return and completes
    `--fix\\r`, planting a stray CR at the cursor. Writing bytes to the
    underlying buffer skips the translation and pins UTF-8; captured stdout
    (tests, some wrappers) has no buffer, so fall back.
    """
    if not lines:
        return
    payload = "\n".join(lines) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(payload)
    else:
        buffer.write(payload.encode("utf-8"))
        buffer.flush()


def _fresh_dynamic(param: str, path: list[str], args: list[str]) -> list[str] | None:
    """Run *param*'s completer fresh in a subprocess; None on timeout/failure.

    Isolated on purpose: the subprocess imports the framework and the user's
    code, which the hot path must never do. A timeout or non-zero exit returns
    None, and the caller shows nothing rather than a stale snapshot. An empty
    *path* addresses a plugin's global option by name — a task parameter
    always rides with the segment path that reached it.
    """
    from footman import _paths

    # Same reasoning as the refresh child: it is told this CLI's locations
    # rather than re-deriving them from an environment it shares with others.
    # Length-prefixed, so the flags that follow can never be eaten when the
    # word count grows — the arity drifted once per release until it was.
    where = _paths.child_args()
    cmd = [
        sys.executable,
        "-m",
        "footman._suggest",
        "--where",
        str(len(where)),
        *where,
    ]
    if path:
        cmd += ["--param", param]
        for name in path:
            cmd += ["--path", name]
    else:
        cmd += ["--global", param]
    prior = args[:-1]
    if (tf := _leading_global_value(prior, ("-f", "--tasks-file"))) is not None:
        cmd += ["--tasks-file", tf]
    if (cf := _leading_global_value(prior, ("--config",))) is not None:
        cmd += ["--config", cf]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_DYNAMIC_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [v for v in proc.stdout.splitlines() if v]


def complete_cli(args: list[str]) -> int:
    """Entry for `footman --complete` and the standalone resolver."""
    manifest = None
    if args and args[0] == "--manifest":
        if len(args) < 2:
            return 0
        manifest, args = args[1], args[2:]
    # WinPS 5.1 and pwsh 7.0-7.2 drop empty-string args to native commands, so
    # the hook can't pass the trailing "" partial itself — it flags the empty
    # position and we append the "" here instead.
    empty_partial = False
    if args and args[0] == "--empty-partial":
        empty_partial, args = True, args[1:]
    if args and args[0] == "--":
        args = args[1:]
    if empty_partial:
        args = [*args, ""]

    derived = manifest is None
    override: str | None = None
    if manifest is None:
        # Only the derive branch needs the package; keep the standalone
        # --manifest path free of any `footman` import. The cache is keyed by
        # cwd — the effective task set is the cascade from the repo root down —
        # unless `-f <file>` names one file, which has its own (cwd, file) key.
        from pathlib import Path

        from footman import _paths

        # The last word is the partial being completed: `fm -f <TAB>` is a file
        # being typed, not a finished override — so read the override from the
        # prior words only, leaving `-f`'s own value to native file completion
        # (the resolver signals it below). A finished `-f file <TAB>` still keys
        # by the pair.
        override = _tasks_file_from(args[:-1])
        if override:
            manifest = str(_paths.source_manifest_path(Path.cwd(), Path(override)))
        else:
            manifest = str(_paths.cwd_manifest_path())
            if not Path(manifest).is_file():
                # No warm cwd manifest: one walk decides what this directory
                # even is. A project's first TAB builds its own cascade
                # below. A project-less one is global mode — whose manifest
                # every such directory shares, cold once per brand version
                # rather than once per directory — when the brand has
                # built-ins or the user keeps a tasks file; with neither
                # there is nothing here to complete, and saying so instantly
                # beats spawning a build that can only come back empty after
                # the full cold bound. Only on the miss path: a warm
                # directory never pays the walk.
                cwd = Path.cwd()
                name = _paths.tasks_file_name()
                files = _paths.task_files(cwd, _paths.find_repo_root(cwd), name)
                if not files:
                    if (
                        not _paths.builtin()
                        and not _paths.user_tasks_file(name).is_file()
                    ):
                        return 0
                    manifest = str(_paths.global_manifest_path())

    data = _load_manifest(manifest)
    if (
        data is None
        or not isinstance(data.get("tree"), dict)
        or data.get("schema") != _SCHEMA
    ):
        # Cold cache, or one baked by a different footman: rather than answer
        # empty (or walk a reshaped tree into a traceback), build the manifest
        # once (bounded) and serve it, so the first TAB in a fresh directory —
        # or right after an upgrade — is accurate. Covers the cwd cascade and
        # a finished `-f <file>` alike.
        data = _cold_build(manifest, override) if derived else None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("tree"), dict)
            or data.get("schema") != _SCHEMA
        ):
            return 0  # cold and couldn't build in time — stay silent and fast
    out = complete(data["tree"], args)
    if len(out) == 1 and out[0].split("\t", 1)[0] in (_FILES, _FILES_CSV):
        # A path value: signal the hook to complete files. `_FILES_CSV` means
        # after the last comma. (Only ever raised with a comma already typed,
        # so a hook from an older install — which knows just 100 — degrades to
        # the silence it always showed there.)
        #
        # Whatever follows the sentinel is a `matching()` glob for the shell
        # to filter by; nothing follows it when the parameter declared none,
        # which is the every-file hand-off every hook already knew.
        tag, _, glob = out[0].partition("\t")
        csv = tag == _FILES_CSV
        if glob:
            sys.stdout.write(glob + "\n")
        _maybe_refresh(manifest, data)
        return _EXIT_FILES_CSV if csv else _EXIT_FILES
    if out and out[0] == _DYNAMIC:
        # A dynamic completer: recompute it fresh in a subprocess rather than
        # serve the manifest's baked snapshot — a build-critical answer must not
        # be stale. Empty on timeout or failure, never the old values. The
        # prefix (`--opt=` for an attached value, "" for a positional) rides
        # along so emitted candidates replace the shell's whole word.
        partial, prefix, param, seg_path = out[1], out[2], out[3], out[4:]
        fresh = _fresh_dynamic(param, seg_path, args)
        if fresh is not None:
            _emit([prefix + c for c in fresh if c.startswith(partial)])
        _maybe_refresh(manifest, data)
        return 0
    _emit(out)
    _maybe_refresh(manifest, data)  # SWR: refresh the baked fallback + structural set
    return 0


def main() -> int:
    return complete_cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
