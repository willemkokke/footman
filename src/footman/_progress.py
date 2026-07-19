"""Duration history and the run estimate behind the progress bar.

Runs of the same invocation shape — the same chain with the same values and
passthrough, serial or parallel, in the same directory — tend to take
similar time. The store keeps the recent wall totals per shape in
`<footman cache>/<cwd-key>.times.json`; the estimator turns them into a
determinate expectation only when the history genuinely supports one:
enough samples, and a controlled right tail. Anything less honest renders
as the indeterminate pulse instead.

Execution-path only, stdlib only. A missing, corrupt, or read-only store
never fails (or even warns about) a run — timing is a nicety, never load
bearing.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from footman import _paths
from footman.split import Segment

SCHEMA = 1
WINDOW = 50  # samples kept per chain — recency policy, sliding
MAX_KEYS = 200  # chains kept per directory
IDLE_DAYS = 60  # a chain not run for this long is forgotten
MIN_SAMPLES = 5  # fewer → indeterminate
TAIL_RATIO = 1.8  # p90 beyond this multiple of p50 → too erratic


def chain_key(segments: list[Segment], *, sequential: bool) -> str:
    """A stable hash of the invocation shape.

    Values and passthrough are part of the shape on purpose — `fm test --
    -k one` is not `fm test` — and serial/parallel runs of the same chain
    keep separate histories (different distributions entirely).
    """
    shape = [
        {
            "task": s.task,
            "values": s.values,
            "variadic": s.variadic,
            "passthrough": s.passthrough,
        }
        for s in segments
    ]
    payload = json.dumps(
        {"sequential": sequential, "chain": shape}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Estimate:
    """A determinate expectation for one run.

    The bar fills against *scale* (p90 — it usually completes just before
    the end and clamps rather than overrunning); the label quotes
    *typical* (p50).
    """

    typical: float
    scale: float


def estimate(runs: list[float]) -> Estimate | None:
    """A determinate estimate from *runs*, or None when honesty forbids one."""
    if len(runs) < MIN_SAMPLES:
        return None
    deciles = statistics.quantiles(runs, n=10, method="inclusive")
    p50, p90 = statistics.median(runs), deciles[8]
    if p50 <= 0 or p90 > TAIL_RATIO * p50:
        return None  # erratic history: an "estimate" would be a guess
    return Estimate(typical=p50, scale=max(p90, 0.1))


# --- the store ---------------------------------------------------------------


def load_runs(cwd: Path, key: str) -> list[float]:
    """The recent durations for *key*, oldest first; [] when unknown."""
    entry = _load(cwd).get("chains", {}).get(key)
    if not isinstance(entry, dict):
        return []
    runs = entry.get("runs")
    if not isinstance(runs, list):
        return []
    return [float(r) for r in runs if isinstance(r, (int, float))]


def record(cwd: Path, key: str, seconds: float) -> None:
    """Append one green run's wall total; prune idle chains, cap sizes.

    Best-effort by contract: an unwritable cache directory must never fail
    the run that just succeeded.
    """
    now = time.time()
    data = _load(cwd)
    chains = data.get("chains")
    if not isinstance(chains, dict):
        chains = {}
    entry = chains.get(key)
    old = entry.get("runs") if isinstance(entry, dict) else None
    runs = [r for r in old if isinstance(r, (int, float))] if old else []
    runs.append(round(float(seconds), 3))
    chains[key] = {"last": now, "runs": runs[-WINDOW:]}

    horizon = now - IDLE_DAYS * 86400
    chains = {
        k: v
        for k, v in chains.items()
        if isinstance(v, dict) and v.get("last", 0) >= horizon
    }
    if len(chains) > MAX_KEYS:
        for stale in sorted(chains, key=lambda k: chains[k].get("last", 0))[
            : len(chains) - MAX_KEYS
        ]:
            del chains[stale]

    path = _paths.times_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema": SCHEMA, "chains": chains})
        path.write_text(payload, encoding="utf-8")
    except OSError:
        pass


def _load(cwd: Path) -> dict:
    try:
        data = json.loads(_paths.times_path(cwd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
