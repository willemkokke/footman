"""Completion hot path — the code every TAB press runs.

Hard constraints: standard library only, no framework import, no user-code
import. The whole budget is ~50 ms including interpreter startup, so the work
here is one file read + JSON parse + tree walk.

Two ways in:

* ``footman --complete [--] WORD [WORD ...]`` — the portable path. The console
  script dispatches here *before importing anything else* and derives the cache
  location from the current directory.
* ``python _complete.py --manifest PATH -- WORD [WORD ...]`` — the baked-in
  path. A generated completion script invokes the interpreter directly on this
  file with the manifest location hard-coded, skipping the console-script shim
  and the ``footman`` package import entirely.

WORDs are the command line after the program name; the last word is the partial
being completed ("" when the cursor follows a space).
"""

from __future__ import annotations

import json
import sys


def complete(tree: dict, words: list[str]) -> list[str]:
    """Resolve completion candidates for *words* against a manifest *tree*."""
    *prior, partial = words or [""]

    # Walk group nodes until we reach a task name (or run out of words).
    node, task = tree, None
    for word in prior:
        if task is None and word in node["groups"]:
            node = node["groups"][word]
        elif task is None and word in node["tasks"]:
            task = node["tasks"][word]

    if task is None:
        names = list(node["groups"]) + list(node["tasks"])
        return [n for n in names if n.startswith(partial)]

    opts = {
        "--" + p["name"]: p for p in task["params"] if p["kind"] in ("flag", "option")
    }

    # Value position: the previous word is an option expecting a value.
    prev = prior[-1] if prior else ""
    if prev in opts and opts[prev]["kind"] == "option":
        return [c for c in opts[prev].get("choices", []) if c.startswith(partial)]

    # Option position: offer flags/options plus any positional choices.
    candidates = list(opts)
    for p in task["params"]:
        if p["kind"] == "argument":
            candidates += p.get("choices", [])
    return [c for c in candidates if c.startswith(partial)]


def _load_tree(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            return json.load(fh)["tree"]
    except (OSError, ValueError, KeyError):
        return None


def complete_cli(args: list[str]) -> int:
    """Entry for ``footman --complete`` and the standalone resolver."""
    manifest = None
    if args and args[0] == "--manifest":
        if len(args) < 2:
            return 0
        manifest, args = args[1], args[2:]
    if args and args[0] == "--":
        args = args[1:]

    if manifest is None:
        # Only the derive branch needs the package; keep the standalone
        # --manifest path free of any ``footman`` import. The cache is keyed by
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
