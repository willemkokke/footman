"""Measure bare import cost: `import footman` vs `import typer`.

Backs the comparison page's import-cost table with a committed, reproducible
script (typer is optional — install the `comparison` dependency group to
measure it). Each variant runs in a fresh process; the reported number is the
cost *over* a bare interpreter, so machine speed mostly cancels out.

    uv run --group comparison python scripts/bench_import.py
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time

RUNS = 15


def _cold(code: str) -> float:
    start = time.perf_counter()
    subprocess.run([sys.executable, "-c", code], check=True)
    return (time.perf_counter() - start) * 1000


def measure(code: str) -> float:
    return statistics.mean(_cold(code) for _ in range(RUNS))


def main() -> None:
    base = measure("pass")
    print(f"runs per variant: {RUNS} (fresh process each)\n")
    print(f"{'interpreter startup (floor)':<34}{base:>8.1f} ms")
    for label, module in (("import footman", "footman"), ("import typer", "typer")):
        try:
            cost = measure(f"import {module}") - base
        except subprocess.CalledProcessError:
            print(f"{label:<34}{'not installed':>14}")
            continue
        print(f"{label:<34}{'+':>5}{cost:>6.1f} ms over the floor")


if __name__ == "__main__":
    main()
