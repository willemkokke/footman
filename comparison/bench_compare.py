"""Head-to-head: completion latency and list latency across task runners.

Cold-process wall time (every TAB pays interpreter startup, so that is the
honest metric). For each runner we run its real completion command and its list
command, at two project-import costs: 0 s and 0.25 s. The *delta* between them
is the decisive, tool-independent answer to "does completion re-import your
project on every TAB?" — no reliance on anyone's prior claims.

Run: uv run --group comparison python comparison/bench_compare.py
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = Path(sys.executable).parent
HERE = Path(__file__).resolve().parent
# Medians over 40 timed runs after 3 discarded warmups. The first samples
# pay one-off costs (filesystem cache, __pycache__ writes on a fresh
# checkout) that belong to the machine, not the runner, and a mean is
# hostage to one slow fork on a machine doing anything else — which is how
# the previously published table failed to reproduce itself.
RUNS = 40
WARMUP = 3

TOOLS = {
    "footman": {
        "cwd": HERE / "footman",
        "prime": [str(BIN / "fm"), "--list"],
        "complete": [str(BIN / "fm"), "--complete", "--", ""],
        "list": [str(BIN / "fm"), "--list"],
        "exec": [str(BIN / "fm"), "noop"],
    },
    "duty": {
        "cwd": HERE / "duty",
        "complete": [str(BIN / "duty"), "--complete", "--", "duty", ""],
        "list": [str(BIN / "duty"), "--list"],
        "exec": [str(BIN / "duty"), "noop"],
    },
    "invoke": {
        "cwd": HERE / "invoke",
        "complete": [str(BIN / "inv"), "--complete", "--", ""],
        "list": [str(BIN / "inv"), "--list"],
        "exec": [str(BIN / "inv"), "noop"],
    },
    "poe": {
        "cwd": HERE / "poe",
        "complete": [str(BIN / "poe"), "_list_tasks"],
        "list": [str(BIN / "poe"), "--help"],
        "exec": [str(BIN / "poe"), "noop"],
    },
    # "just write a typer app" — no cached-manifest completion; its launch cost
    # is what we're measuring (it pays the typer/click/rich import every time).
    "typer": {
        "cwd": HERE / "typer",
        "list": [str(BIN / "python"), "app.py", "--help"],
        "exec": [str(BIN / "python"), "app.py", "noop"],
    },
}


# Every runner here loads exactly one task file: its own, in its own directory.
# footman's default is a *cascade* — every tasks.py from the repo root down —
# and these bench projects live inside footman's own repo, so left alone
# footman would also import the repo's real tasks.py (plugin mounts, a toolroom
# import: ~38 ms no other runner pays here) and the table would be measuring
# this checkout rather than the runner. `cascade = "none"` scopes it to the
# bench directory, which is the like-for-like shape.
ISOLATE = {"FOOTMAN_CASCADE": "none"}


def run_once(cmd, cwd, cost):
    env = dict(os.environ, COMPARISON_IMPORT_COST=str(cost))
    env.update(ISOLATE)
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return (time.perf_counter() - start) * 1000, proc


def mean_ms(cmd, cwd, cost):
    """The cell value: a median with its spread, as `(median, p10, p90)`.

    Kept under its historical name so the call sites read unchanged; the
    median is what the table prints, and the percentiles say whether the
    machine was quiet enough for the number to mean anything.
    """
    for _ in range(WARMUP):
        run_once(cmd, cwd, cost)
    samples = sorted(run_once(cmd, cwd, cost)[0] for _ in range(RUNS))
    return (
        statistics.median(samples),
        samples[len(samples) // 10],
        samples[-1 - len(samples) // 10],
    )


def cell(stat) -> str:
    """`median ±spread` — the spread is (p90 - p10) / 2, printed so a noisy
    machine's table carries its own health warning."""
    median, p10, p90 = stat
    return f"{median:.0f}±{(p90 - p10) / 2:.0f}ms"


def main() -> None:
    print(
        f"interpreter: {sys.executable}\n"
        f"runs per cell: {RUNS} after {WARMUP} warmups (fresh process); "
        f"cells are median ±(p90-p10)/2 — a spread rivalling its median "
        f"means the machine was busy and the table should be rerun\n"
    )

    if prime := TOOLS["footman"].get("prime"):
        run_once(prime, TOOLS["footman"]["cwd"], 0.25)  # build footman's cache first

    # Sanity: show each completion command actually returns candidates.
    print("completion output sanity check (cost=0):")
    for name, spec in TOOLS.items():
        if "complete" not in spec:
            continue
        _, proc = run_once(spec["complete"], spec["cwd"], 0)
        sample = " ".join(proc.stdout.split()[:6])
        print(f"  {name:<8} -> {sample or '(empty!)'}")
    print()

    header = (
        f"{'runner':<9} {'complete@0.25s':>15} {'complete@0s':>13} "
        f"{'Δ import':>10} {'list@0.25s':>12}"
    )
    print(header)
    print("-" * len(header))
    for name, spec in TOOLS.items():
        if "complete" not in spec:
            continue
        c_hot = mean_ms(spec["complete"], spec["cwd"], 0.25)
        c_cold = mean_ms(spec["complete"], spec["cwd"], 0.0)
        lst = mean_ms(spec["list"], spec["cwd"], 0.25)
        print(
            f"{name:<9} {cell(c_hot):>15} {cell(c_cold):>13} "
            f"{c_hot[0] - c_cold[0]:>8.0f}ms {cell(lst):>12}"
        )

    print(
        "\nΔ import ≈ project-import cost paid *during completion*.\n"
        "~250ms => the runner re-imports your tasks on every TAB; ~0 => it does not."
    )

    # --- task execution overhead ------------------------------------------
    # Cold-process wall time to run a no-op task. At cost 0 this is pure
    # framework dispatch overhead (interpreter + import + resolve + call);
    # at cost 0.25 it also includes the project-import cost the Python-based
    # runners pay on every run (poe doesn't import Python tasks at all).
    ehdr = f"\n{'runner':<9} {'exec noop@0 (overhead)':>22} {'exec noop@0.25s':>17}"
    print(ehdr)
    print("-" * (len(ehdr) - 1))
    for name, spec in TOOLS.items():
        e_cold = mean_ms(spec["exec"], spec["cwd"], 0.0)
        e_hot = mean_ms(spec["exec"], spec["cwd"], 0.25)
        print(f"{name:<9} {cell(e_cold):>22} {cell(e_hot):>17}")
    print(
        "\nexec@0 is the runner's own overhead on top of your task's real work.\n"
        "exec@0.25 shows footman/duty/invoke re-importing the project per run; "
        "poe stays flat (its tasks are TOML, not imported Python)."
    )


if __name__ == "__main__":
    main()
