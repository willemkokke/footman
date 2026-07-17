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


class _Segment:
    """Walk state for one chain segment (mirrors the splitter's rules)."""

    def __init__(self, task: dict | None = None) -> None:
        self.task = task
        self.opts: dict = {}
        self.fixed: list[dict] = []
        self.rest: dict | None = None
        self.filled = 0
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

    node, seg = tree, _Segment()
    value_opt: dict | None = None  # the option whose value comes next

    for word in prior:
        if word == "--":
            return []  # passthrough: the words after this aren't ours
        if value_opt is not None:
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
            if seg.opts[name]["kind"] == "option" and "=" not in word:
                value_opt = seg.opts[name]
            continue
        if name.startswith("--no-") and "--" + name[len("--no-") :] in seg.opts:
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

    # Value position: the previous word was an option expecting a value.
    if value_opt is not None:
        return [c for c in value_opt.get("choices", []) if c.startswith(partial)]

    if seg.task is None:
        names = list(node["groups"]) + list(node["tasks"])
        return [n for n in names if n.startswith(partial)]

    # Option position: this task's flags/options, plus what the next bare
    # word could be — the pending positional's choices, the trailing
    # consumer's choices, or (arity satisfied) the next segment's names.
    candidates = list(seg.opts)
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
    if args and args[0] == "--":
        args = args[1:]

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
