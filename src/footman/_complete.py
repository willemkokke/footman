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

# Hardcoded mirror of split.GLOBALS — the hot path can't import split (it
# would pull the whole package). `test_completion_globals_mirror_split`
# rebuilds this FROM split.GLOBALS, so renaming a global fails CI. The
# grammar is lexical — every value is `=`-attached, every dash token
# self-contained — so the walk needs no arity: only the names (to suggest)
# and the value surfaces below (to complete).
_GLOBALS = frozenset(
    {
        "--help", "-h", "--version", "-V", "--list", "-l", "--tree", "--sort",
        "--where", "--plugins", "--dry-run", "-n", "--keep-going", "-k",
        "--fail-fast", "--sequential", "-s", "--jobs", "-j", "--yes", "-y",
        "--no-input", "--quiet", "-q", "--verbose", "-v", "--color",
        "--no-color", "--no-progress", "--json", "--timings",
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
_SCHEMA = 2
_DYNAMIC_TIMEOUT = 2.0  # seconds to wait for a fresh dynamic completer subprocess
_COLD_TIMEOUT = 3.0  # seconds to wait for a first-time cwd manifest build
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


def _csv_head(p: dict, value: str) -> tuple[str, str]:
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


def _choice_tokens(p: dict, partial: str) -> list[str]:
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

    def __init__(self, task: dict | None = None) -> None:
        self.task = task
        self.opts: dict = {}
        self.fixed: list[dict] = []
        self.rest: dict | None = None
        self.filled = 0
        self.used: set[str] = set()  # options already given in this segment
        if task is not None:
            params = task["params"]
            self.opts = {
                "--" + p["name"]: p for p in params if p["kind"] in ("flag", "option")
            }
            self.fixed = [
                p for p in params if p["kind"] == "argument" and not p.get("multiple")
            ]
            self.rest = next(
                (
                    p
                    for p in params
                    if (p["kind"] == "argument" and p.get("multiple"))
                    or p["kind"] == "variadic"
                ),
                None,
            )


def _has_visible(node: dict) -> bool:
    """Whether anything under a group is offered to a human.

    The completion twin of `_describe.has_listed`, spelled again here because
    the hot path imports no framework: dict reads over the manifest a TAB
    already loaded. Hiding a group hides everything that inherits from it,
    but a child that answered `hidden=False` still completes — and can only
    be reached by descending through its parent.
    """
    if node.get("default") is not None and not node["default"].get("hidden"):
        return True
    if any(not spec.get("hidden") for spec in node["tasks"].values()):
        return True
    return any(_has_visible(sub) for sub in node["groups"].values())


def _cand(address: str, summary: str) -> str:
    """`address\\tdescription` when there is a help line, else the address.

    The tab is the backward-safe wire format: shells that render descriptions
    (zsh, fish) split on it; bash (and others) keep the first field. Options and
    choice values carry no help, so they pass through bare.
    """
    return f"{address}\t{summary}" if summary else address


def _walk_address(tree: dict, token: str) -> tuple[str, dict, list[str]] | None:
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


def _leaf_fallback(tree: dict, partial: str) -> list[str]:
    """Nested candidates whose *last* segment starts with *partial*.

    The rescue for "I know the task, not where it lives": when a typed token
    prefix-matches no top-level segment, complete against last segments over
    the whole tree instead — `serve` → `docs.serve`. Only nested entries
    (a top-level match would have answered already, so the zero-match guard
    means this can never pollute a first tab or a valid descent).
    """
    out: list[str] = []

    def walk(node: dict, prefix: str) -> None:
        for name, spec in node["tasks"].items():
            if prefix and name.startswith(partial) and not spec.get("hidden"):
                out.append(_cand(f"{prefix}{name}", spec.get("help", "")))
        for name, sub in node["groups"].items():
            if not _has_visible(sub):
                continue  # a hidden subtree suggests nothing, at any depth
            if (
                prefix
                and name.startswith(partial)
                and "default" in sub
                and not sub["default"].get("hidden")
            ):
                out.append(
                    _cand(f"{prefix}{name}", sub["default"].get("help") or sub["help"])
                )
            walk(sub, f"{prefix}{name}.")

    walk(tree, "")
    return out


def _address_candidates(tree: dict, partial: str) -> list[str]:
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
        # `hidden` nodes are typed, never suggested: a task nobody is meant to
        # reach for stays out of the menu (one dict read — the hot path holds).
        # A hidden *group* is still offered when something under it opted back
        # in with `hidden=False`, because that child is listed and TAB has to
        # be able to reach it — completion offers exactly what `--list` shows.
        groups = [
            n
            for n in node["groups"]
            if n.startswith(leaf) and _has_visible(node["groups"][n])
        ]
        tasks = [
            n
            for n in node["tasks"]
            if n.startswith(leaf) and not node["tasks"][n].get("hidden")
        ]
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
            if not default.get("hidden"):
                out.append(_cand(f"{prefix}{name}", default.get("help") or sub["help"]))
            for child, csub in sub["groups"].items():
                if not csub.get("hidden"):
                    out.append(_cand(f"{prefix}{name}.{child}.", csub["help"]))
            for child, spec in sub["tasks"].items():
                if not spec.get("hidden"):
                    out.append(_cand(f"{prefix}{name}.{child}", spec.get("help", "")))
        else:
            out.append(_cand(f"{prefix}{name}.", sub["help"]))
    for name in tasks:
        out.append(_cand(f"{prefix}{name}", node["tasks"][name].get("help", "")))
    if not out and not bases and leaf:
        return _leaf_fallback(tree, leaf)
    return out


def complete(tree: dict, words: list[str]) -> list[str]:
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
            # A pulled plugin's global completes from its baked entry — the
            # same shapes a task parameter bakes, answered the same way. A
            # comma-splitting value mid-list completes its tail item alone,
            # the typed head riding every candidate.
            head, cur = _csv_head(entry, valpart)
            if "path" in entry.get("types", []):
                return [_FILES_CSV] if head else [_FILES]
            if entry.get("dynamic"):  # recompute fresh, never the baked snapshot
                prefix = head if bash_split else f"{optname}={head}"
                return [_DYNAMIC, cur, prefix, entry["name"]]
            given = valpart.split(",")[:-1] if head else []
            choices = [
                c
                for c in entry.get("choices", ())
                if c.startswith(cur) and c not in given
            ]
            return (
                [head + c for c in choices]
                if bash_split
                else [f"{optname}={head}{c}" for c in choices]
            )
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
                "--" + p["name"]
                for p in node["default"]["params"]
                if p["kind"] in ("flag", "option")
                and ("--" + p["name"]).startswith(partial)
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
            out += [g for g in sorted(_GLOBALS) if g.startswith(partial)]
            out += [
                flag
                for g in tree.get("globals", ())
                if (flag := "--" + g["name"]).startswith(partial)
            ]
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
            # A comma-splitting value mid-list completes its tail item
            # alone; the typed head rides every candidate back.
            head, cur = _csv_head(opt, valpart)
            if "path" in opt.get("types", []):
                return [_FILES_CSV] if head else [_FILES]
            if opt.get("dynamic"):  # recompute fresh, never the baked snapshot
                prefix = head if bash_split else f"{optname}={head}"
                return [_DYNAMIC, cur, prefix, opt["name"], *path]
            given = valpart.split(",")[:-1] if head else []
            choices = [
                c
                for c in opt.get("choices", [])
                if c.startswith(cur) and c not in given
            ]
            return (
                [head + c for c in choices]
                if bash_split
                else [f"{optname}={head}{c}" for c in choices]
            )

    # A path-typed positional (or trailing consumer): once the partial is a
    # value being typed rather than an option, hand it to native file
    # completion — the same handoff a Path-typed option value gets above.
    # `-` still reaches the options below, so they stay one keystroke away.
    if not partial.startswith("-"):
        pending = seg.fixed[seg.filled] if seg.filled < len(seg.fixed) else seg.rest
        if pending is not None:
            head, cur = _csv_head(pending, partial)
            if "path" in pending.get("types", []):
                return [_FILES_CSV] if head else [_FILES]
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
    # Same tab-separated wire format either way.
    out = []
    for c in seen:
        p = seg.opts.get(c)
        if p is not None and p.get("doc"):
            out.append(f"{c}\t{p['doc']}")
        else:
            out.append(c)
    return out + next_heads


def _load_manifest(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _maybe_refresh(path: str, data: dict) -> None:
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
    if override:
        script = (
            "import sys; from footman import _refresh; "
            "_refresh.refresh_source(sys.argv[1])"
        )
        cmd = [sys.executable, "-c", script, override]
    else:
        script = "from footman import _refresh; _refresh.refresh_cwd()"
        cmd = [sys.executable, "-c", script]
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
        return  # a background refresh must never break completion


def _cold_build(manifest: str, override: str | None) -> dict | None:
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
    cmd = [sys.executable, "-m", "footman._suggest"]
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
        manifest = str(
            _paths.source_manifest_path(Path.cwd(), Path(override))
            if override
            else _paths.cwd_manifest_path()
        )

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
    if out == [_FILES]:
        # A path value: print nothing, and signal the hook to complete files.
        _maybe_refresh(manifest, data)
        return _EXIT_FILES
    if out == [_FILES_CSV]:
        # A comma-splitting path value mid-list: signal the hook to complete
        # files after the last comma. (Only ever raised with a comma already
        # typed, so a hook from an older install — which knows just 100 —
        # degrades to the silence it always showed there.)
        _maybe_refresh(manifest, data)
        return _EXIT_FILES_CSV
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
