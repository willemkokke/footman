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
import os
import shutil
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from footman import _paths
from footman.split import Segment

SCHEMA = 1
WINDOW = 50  # samples kept per chain — recency policy, sliding
MAX_KEYS = 200  # chains kept per directory
IDLE_DAYS = 60  # a chain not run for this long is forgotten
MIN_SAMPLES = 5  # fewer → indeterminate
TAIL_RATIO = 1.8  # p90 beyond this multiple of p50 → too erratic


def default_jobs() -> int:
    """The parallel width when nobody chose one: cores - 1, never below 2 —
    the machine stays responsive, the fan-out stays real."""
    return max(2, (os.cpu_count() or 3) - 1)


def chain_key(segments: list[Segment], *, sequential: bool, jobs: int) -> str:
    """A stable hash of the invocation shape.

    Values and passthrough are part of the shape on purpose — `fm test --
    -k one` is not `fm test` — and serial/parallel/width variants of the
    same chain keep separate histories (different distributions entirely).
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
        {"sequential": sequential, "jobs": jobs, "chain": shape},
        sort_keys=True,
        default=str,
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
        # Atomic, like the manifest write: concurrent fm runs (a hook's
        # `fm check` racing yours) may lose each other's *sample* — a
        # nicety — but a torn file would lose the whole history.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"schema": SCHEMA, "chains": chains}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError:
        pass


def _load(cwd: Path) -> dict:
    try:
        data = json.loads(_paths.times_path(cwd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- the status line ---------------------------------------------------------

_CLEAR = "\r\033[K"
_BAR_CELLS = 15
_PULSE_CELLS = 3


def fmt_secs(t: float) -> str:
    """`4.1s`, `42s`, `1m10s`, `4h35m` — as short as honesty allows."""
    if t >= 3600:
        return f"{int(t) // 3600}h{(int(t) % 3600) // 60:02d}m"
    if t >= 60:
        return f"{int(t) // 60}m{int(t) % 60:02d}s"
    return f"{t:.0f}s" if t >= 9.5 else f"{t:.1f}s"


class StatusLine:
    """The one live line for a run, drawn on the real stderr.

    Fed by *both* parallel engines — scheduler nodes and `parallel()`
    children are the same kind of unit — so a chain and a task-body
    fan-out present identically. Determinate runs fill a bar against the
    estimate's p90 (clamped at 98% until the run truly ends); anything
    else pulses, with elapsed time either way.

    Coexistence contract: the output routers report every real-terminal
    write via `notify()`, which clears a painted line first and tracks
    whether the terminal now sits at column 0. The ticker (and every
    event repaint) paints **only at column 0**, so `run()`'s in-place
    step rewrites and flushed blocks are never corrupted. The line writes
    straight to the real stream, never through the routers.
    """

    def __init__(
        self, err: TextIO, est: Estimate | None, *, color: bool = True
    ) -> None:
        self.err = err
        self.est = est
        self.color = color
        self.lock = threading.RLock()
        self.started = time.perf_counter()
        self.total = 0
        self.done = 0
        self.failed = 0
        self.running: list[str] = []  # a list: anonymous thunks may collide
        self.painted = False
        self.at_col0 = True
        self.ticks = 0
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    # -- lifecycle
    def open(self) -> None:
        """Start the repaint ticker (elapsed must move without events)."""
        self._ticker = threading.Thread(target=self._run_ticker, daemon=True)
        self._ticker.start()

    def close(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=1.0)
        with self.lock:
            self._clear_locked()

    def _run_ticker(self) -> None:
        while not self._stop.wait(0.2):
            self.paint()

    # -- the engine feed (scheduler nodes and parallel() children alike)
    def unit_added(self, count: int = 1) -> None:
        with self.lock:
            self.total += count

    def unit_started(self, name: str) -> None:
        with self.lock:
            self.running.append(name)
        self.paint()

    def unit_finished(self, name: str, ok: bool) -> None:
        with self.lock:
            if name in self.running:
                self.running.remove(name)
            self.done += 1
            self.failed += 0 if ok else 1
        self.paint()

    def unit_skipped(self, name: str) -> None:
        """A dependent of a failure never ran: done for counting purposes —
        the failure that caused it already counted itself."""
        with self.lock:
            self.done += 1
        self.paint()

    # -- the router feed
    def notify(self, s: str) -> None:
        """A real-terminal write is about to land: get out of its way."""
        if not s:
            return
        with self.lock:
            self._clear_locked()
            self.at_col0 = s.endswith("\n")

    # -- painting
    def paint(self) -> None:
        with self.lock:
            if not self.at_col0:
                return  # someone's mid-line (a live `→ step`): stay away
            self.ticks += 1
            self.err.write(f"{_CLEAR}{self._render()}")
            self.err.flush()
            self.painted = True

    def _clear_locked(self) -> None:
        if self.painted:
            self.err.write(_CLEAR)
            self.err.flush()
            self.painted = False

    def _render(self) -> str:
        elapsed = time.perf_counter() - self.started
        if self.est is not None:
            frac = min(elapsed / self.est.scale, 0.98)
            filled = int(frac * _BAR_CELLS)
            bar = "█" * filled + "░" * (_BAR_CELLS - filled)
            label = f" {fmt_secs(elapsed)} ~{fmt_secs(self.est.typical)}"
        else:  # indeterminate: a bouncing pulse
            span = _BAR_CELLS - _PULSE_CELLS
            bounce = self.ticks % (2 * span)
            pos = bounce if bounce <= span else 2 * span - bounce
            bar = "░" * pos + "█" * _PULSE_CELLS + "░" * (span - pos)
            label = f" {fmt_secs(elapsed)}"
        line = f"[{bar}]{label}"
        if self.total > 1:
            line += f"  {self.done}/{self.total}"
        if self.failed:
            text = f"{self.failed} failed"
            if self.color:
                text = f"\033[31m{text}\033[0m"
            line += f" ({text})"
        if self.running:
            names = ", ".join(list(self.running)[:4])
            if len(self.running) > 4:
                names += " ..."
            line += f"  running: {names}"
        width = shutil.get_terminal_size((80, 24)).columns - 1
        return line if len(line) <= width else line[:width]
