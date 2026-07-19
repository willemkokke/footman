"""Cache garbage collection — the detached child a run spawns at most daily.

The cache is derived state top to bottom (completion manifests rebuild on
any execution-path run; timing history regrows), which is what makes
collecting it casually safe: the worst possible outcome of any deletion is
a rebuild. Two rules, in order:

1. **The directory is gone.** Manifests bake in the ``cwd`` they describe;
   if that path no longer exists, the pair (manifest + timing history) is
   leftovers from a deleted project — collected at any age.
2. **The pair is idle.** Untouched for `IDLE_DAYS`, nobody even TAB-completes
   there any more (background refreshes keep a visited manifest's mtime
   fresh) — collected. Manifests from before the ``cwd`` key rely on this
   rule alone.

The invoking directory's own pair is never touched, and every failure is
silent — a concurrently-reading completion child on Windows may hold a file
open, and a collector must never be louder than what it collects.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

IDLE_DAYS = 90
STAMP = "gc.stamp"


def collect(cache_dir: Path, skip_stem: str = "") -> int:
    """Apply the rules to *cache_dir*; returns the number of files removed.

    *skip_stem* is the invoking directory's manifest stem (its hash) — that
    pair is in active use and never touched.
    """
    now = time.time()
    removed = 0

    def unlink(path: Path) -> None:
        nonlocal removed
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass

    for manifest_path in cache_dir.glob("*.json"):
        name = manifest_path.name
        if name.endswith(".times.json"):
            continue  # handled with its manifest, or as an orphan below
        stem = name[: -len(".json")]
        if stem == skip_stem:
            continue
        times_path = cache_dir / f"{stem}.times.json"
        try:
            data = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            data = None
        cwd = data.get("cwd") if isinstance(data, dict) else None
        if isinstance(cwd, str) and cwd and not Path(cwd).exists():
            doomed = True  # rule 1: leftovers of a deleted project
        else:
            doomed = _idle(now, manifest_path, times_path)  # rule 2
        if doomed:
            unlink(manifest_path)
            unlink(times_path)

    # Timing files whose manifest twin is already gone: age them alone.
    for times_path in cache_dir.glob("*.times.json"):
        stem = times_path.name[: -len(".times.json")]
        if stem == skip_stem or (cache_dir / f"{stem}.json").exists():
            continue
        if _idle(now, times_path):
            unlink(times_path)

    return removed


def _idle(now: float, *paths: Path) -> bool:
    """Whether the newest of *paths* is older than the idle window."""
    newest = 0.0
    for path in paths:
        with contextlib.suppress(OSError):
            newest = max(newest, path.stat().st_mtime)
    return newest > 0 and (now - newest) > IDLE_DAYS * 86400


def main() -> None:
    """Entry for the detached child: argv is (cache_dir, skip_stem)."""
    if len(sys.argv) < 2:
        return
    skip = sys.argv[2] if len(sys.argv) > 2 else ""
    collect(Path(sys.argv[1]), skip)
