"""Head-to-head: composite-task orchestration (`check`) across task runners.

Every tool runs the same composite: four simulated check steps, each an
in-process 0.5 s sleep — the stand-in for an I/O-bound tool run, which
releases the GIL exactly like the subprocess a real lint/test step spawns.
Each tool composes them *idiomatically*, and fairness cuts both ways: a tool
that supports parallel execution gets to use it (footman's pre-deps are
parallel by default; poe 0.48 has a `parallel` task type), a tool that
doesn't runs its native serial form (duty and invoke pre-tasks are serial;
typer has no orchestration at all, so its composite is four calls in a row).

The floor is 0.5 s for a parallel runner and 2.0 s for a serial one; whatever
a tool reports above its floor is startup + dispatch overhead.

Run: uv run --group comparison python comparison/bench_check.py
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(sys.executable).parent
HERE = Path(__file__).resolve().parent
RUNS = 5
STEPS, STEP_S = 4, 0.5

TOOLS = {
    "footman": {
        "cwd": HERE / "footman",
        "cmd": [str(BIN / "fm"), "bench-check"],
        "mode": "parallel (pre-deps, default)",
    },
    "poe": {
        "cwd": HERE / "poe",
        "cmd": [str(BIN / "poe"), "bench-check"],
        "mode": "parallel (`parallel` task)",
    },
    "duty": {
        "cwd": HERE / "duty",
        "cmd": [str(BIN / "duty"), "bench-check"],
        "mode": "serial (pre-duties)",
    },
    "invoke": {
        "cwd": HERE / "invoke",
        "cmd": [str(BIN / "inv"), "bench-check"],
        "mode": "serial (pre-tasks)",
    },
    "typer": {
        "cwd": HERE / "typer",
        "cmd": [str(BIN / "python"), "app.py", "bench-check"],
        "mode": "serial (no orchestration)",
    },
}


def run_once(cmd: list[str], cwd: Path) -> float:
    env = dict(os.environ, COMPARISON_IMPORT_COST="0")
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = (time.perf_counter() - start) * 1000
    if proc.returncode != 0:
        raise SystemExit(f"{cmd} failed:\n{proc.stdout}\n{proc.stderr}")
    return elapsed


def main() -> None:
    serial_floor = STEPS * STEP_S * 1000
    parallel_floor = STEP_S * 1000
    print(
        f"composite of {STEPS} steps x {STEP_S:.1f}s in-process sleep "
        f"(I/O-bound stand-in), cold process, {RUNS} runs each\n"
        f"floors: parallel {parallel_floor:.0f} ms · serial {serial_floor:.0f} ms\n"
    )
    header = f"{'runner':<9} {'composition':<32} {'wall (mean)':>12} {'overhead':>10}"
    print(header)
    print("-" * len(header))
    for name, spec in TOOLS.items():
        wall = statistics.mean(run_once(spec["cmd"], spec["cwd"]) for _ in range(RUNS))
        floor = parallel_floor if "parallel" in spec["mode"] else serial_floor
        print(f"{name:<9} {spec['mode']:<32} {wall:>10.0f}ms {wall - floor:>8.0f}ms")
    print(
        "\noverhead = wall time above the tool's own floor "
        "(startup + import + dispatch).\n"
        "The gap that matters is parallel vs serial: the same four steps, "
        f"{serial_floor / parallel_floor:.0f}x apart before overhead."
    )


if __name__ == "__main__":
    main()
