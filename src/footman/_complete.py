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
import sys

# Hardcoded mirror of split.GLOBALS arity — the hot path can't import split (it
# would pull the whole package). `test_completion_globals_mirror_split` rebuilds
# these FROM split.GLOBALS, so renaming or re-typing a global fails CI.
_GLOBAL_FLAG = frozenset(
    {
        "--help", "-h", "--version", "-V", "--list", "-l", "--tree",
        "--dry-run", "-n", "--keep-going", "-k", "--sequential", "-s",
        "--quiet", "-q", "--verbose", "-v", "--no-color", "--json", "--timings",
    }
)  # fmt: skip
_GLOBAL_VALUE = frozenset(
    {"--where", "--directory", "-C", "--tasks-file", "-f", "--config"}
)  # consume the next word as the value
_GLOBAL_MAYBE = frozenset({"--install-completion"})  # value optional
_GLOBAL_CHOICES = {"--install-completion": ("bash", "zsh", "fish", "pwsh", "nushell")}


def _consume_globals(prior: list[str]) -> tuple[list[str], str | None]:
    """Strip leading global options (mirroring `split._parse_globals`).

    Returns the remaining words (the task chain) and, when the partial itself is
    a value-bearing global's value, that global's name — so `fm -C docs <TAB>`
    treats `docs` as `-C`'s value instead of descending into a `docs` group.
    """
    i = 0
    while i < len(prior):
        word = prior[i]
        name = word.split("=", 1)[0]
        if name in _GLOBAL_FLAG:
            i += 1
        elif name in _GLOBAL_VALUE:
            i += 1
            if "=" in word:
                continue
            if i >= len(prior):
                return prior[i:], name  # the value is the partial (no choices)
            i += 1  # consume the value word
        elif name in _GLOBAL_MAYBE:
            i += 1
            if "=" in word:
                continue
            if i >= len(prior):
                return prior[i:], name  # the partial completes its choices
            if not prior[i].startswith("-"):
                i += 1  # optional value present
        else:
            break  # first non-global word: the task chain starts here
    return prior[i:], None


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


def complete(tree: dict, words: list[str]) -> list[str]:
    """Resolve completion candidates for *words* against a manifest *tree*.

    Chain-aware: the walk tracks segments the way the splitter would — exact
    positional arity first, then a trailing multiple/variadic consumer, then
    the next bare word starts a new segment from the root. So in
    `fm format lint --fi<TAB>` the options offered are *lint's*, and once a
    task's arity is satisfied a bare TAB offers the next task names too.
    `+` resets a segment explicitly; after `--` everything belongs to the
    passthrough, so there is nothing to offer.
    """
    *prior, partial = words or [""]

    # Leading global options bind before the task walk, exactly as the splitter
    # consumes them — so `-C docs` reads `docs` as the value, not a group.
    prior, value_global = _consume_globals(prior)

    node, seg = tree, _Segment()
    value_opt: dict | None = None  # the option whose value comes next

    for word in prior:
        if word == "--":
            return []  # passthrough: the words after this aren't ours
        if value_opt is not None:
            if word == "=":  # bash splits `--opt=val` into `--opt`, `=`, `val`;
                continue  # the `=` is a separator — stay armed for the value
            value_opt = None
            continue
        if word == "+":  # explicit segment boundary
            node, seg = tree, _Segment()
            continue
        if seg.task is None:
            if word in node["groups"]:
                node = node["groups"][word]
            elif word in node["tasks"]:
                seg = _Segment(node["tasks"][word])
            continue
        # Inside a task's tail: options and their values first.
        name = word.split("=", 1)[0]
        if name in seg.opts:
            seg.used.add(name)
            if seg.opts[name]["kind"] == "option" and "=" not in word:
                value_opt = seg.opts[name]
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
        node, seg = tree, _Segment()
        if word in tree["groups"]:
            node = tree["groups"][word]
        elif word in tree["tasks"]:
            seg = _Segment(tree["tasks"][word])

    # A leading global expecting a value (`fm --install-completion <TAB>`):
    # offer its choices, if any (a PATH-valued global has none — the shell's
    # default file completion covers it).
    if value_global is not None:
        return [
            c for c in _GLOBAL_CHOICES.get(value_global, ()) if c.startswith(partial)
        ]

    # Value position: the previous word was an option expecting a value. A bash
    # `--opt=<TAB>` can leave the `=` as the partial — strip it.
    if value_opt is not None:
        if partial.startswith("="):
            partial = partial[1:]
        return [c for c in value_opt.get("choices", []) if c.startswith(partial)]

    if seg.task is None:
        names = list(node["groups"]) + list(node["tasks"])
        return [n for n in names if n.startswith(partial)]

    # An attached `--opt=value` partial (zsh/fish don't split on `=`): offer the
    # option's choices as full `--opt=choice` tokens.
    if partial.startswith("-") and "=" in partial:
        optname, _, valpart = partial.partition("=")
        opt = seg.opts.get(optname)
        if opt is not None and opt["kind"] == "option":
            choices = opt.get("choices", [])
            return [f"{optname}={c}" for c in choices if c.startswith(valpart)]

    # Option position: this task's flags/options — minus the ones already
    # given, unless the param legitimately repeats — plus what the next bare
    # word could be: the pending positional's choices, the trailing
    # consumer's choices, or (arity satisfied) the next segment's names.
    candidates = [
        name
        for name, p in seg.opts.items()
        if name not in seg.used or p.get("multiple") or p.get("mapping")
    ]
    if seg.filled < len(seg.fixed):
        candidates += seg.fixed[seg.filled].get("choices", [])
    elif seg.rest is not None:
        candidates += seg.rest.get("choices", [])
    elif not partial.startswith("-"):
        candidates += list(tree["groups"]) + list(tree["tasks"])
    seen: dict[str, None] = {}
    for c in candidates:
        if c.startswith(partial):
            seen.setdefault(c)
    return list(seen)


def _load_tree(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            return json.load(fh)["tree"]
    except (OSError, ValueError, KeyError):
        return None


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

    if manifest is None:
        # Only the derive branch needs the package; keep the standalone
        # --manifest path free of any `footman` import. The cache is keyed by
        # cwd — the effective task set is the cascade from the repo root down.
        from footman import _paths

        manifest = str(_paths.cwd_manifest_path())

    tree = _load_tree(manifest)
    if tree is None:
        return 0  # nothing cached yet — stay silent and fast
    out = complete(tree, args)
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    return 0


def main() -> int:
    return complete_cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
