"""Benchmark the completion hot path.

Completion pays interpreter startup on every TAB press, so cold-process wall
time is the honest metric (not in-process timing). We compare, fresh process
each run:

* interpreter startup — the floor nothing can beat;
* the standalone resolver invoked directly with ``-S`` (the baked-in path a
  generated completion script would use);
* ``python -m footman --complete`` (the portable path through the package).

All read a cached JSON manifest and never import the framework or your tasks.

Run with: ``uv run python scripts/bench_completion.py``
"""

from __future__ import annotations

import importlib.util
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_tasks.py"
RESOLVER = ROOT / "src" / "footman" / "_complete.py"
PY = sys.executable
RUNS = 25


def build_manifest() -> Path:
    from footman import manifest, registry

    registry.reset()
    spec = importlib.util.spec_from_file_location("sample_tasks", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = Path(tempfile.gettempdir()) / "footman_bench_manifest.json"
    manifest.write_manifest(manifest.build_manifest(registry.root), path)
    return path


def measure(label: str, cmd: list[str]) -> None:
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        subprocess.run(cmd, cwd=ROOT, capture_output=True, check=False)
        times.append((time.perf_counter() - start) * 1000)
    lo, mean = min(times), statistics.mean(times)
    print(f"{label:<48} min {lo:6.1f} ms   mean {mean:6.1f} ms")


def main() -> None:
    manifest_path = build_manifest()
    words = ["workspace", ""]
    print(f"interpreter: {PY}\nruns per variant: {RUNS} (fresh process each)\n")

    measure("interpreter startup (python -c pass)", [PY, "-c", "pass"])
    measure("interpreter startup -S", [PY, "-S", "-c", "pass"])
    measure(
        "standalone resolver -S (baked-in path)",
        [PY, "-S", str(RESOLVER), "--manifest", str(manifest_path), "--", *words],
    )
    package_cmd = [PY, "-m", "footman", "--complete"]
    package_cmd += ["--manifest", str(manifest_path), "--", *words]
    measure("package path (python -m footman --complete)", package_cmd)
    print("\nHot path: one file read + JSON parse + walk. No framework, no user code.")


if __name__ == "__main__":
    main()
