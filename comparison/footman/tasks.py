"""footman equivalent of the shared task surface."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _project  # noqa: F401  (simulated project import cost)

from footman import group, task

SRC = ("src", "tests")


def _run(*cmd: str) -> int:
    return subprocess.run(cmd).returncode


@task
def lint(fix: bool = False):
    """Lint with ruff."""
    return _run("ruff", "check", *SRC, *(["--fix"] if fix else []))


@task
def format(check: bool = False):
    """Format with ruff."""
    return _run("ruff", "format", *SRC, *(["--check"] if check else []))


@task
def typecheck():
    """Type-check with basedpyright."""
    return _run("basedpyright")


@task
def test(*pytest_args: str):
    """Run the test suite (extra args after --)."""
    return _run("pytest", *pytest_args)


@task
def check():
    """Run format --check, lint, typecheck, and test."""
    for step in (lambda: format(check=True), lint, typecheck, test):
        if code := step():
            return code
    return 0


@task
def noop():
    """No-op (execution-overhead benchmark)."""


# --- orchestration benchmark: four identical I/O-bound steps ------------------
# Each sleeps 0.5 s in-process (the stand-in for a tool run, which releases the
# GIL exactly like a subprocess). footman composes them as pre-deps, so its
# parallel-by-default scheduler runs them concurrently.
import time  # noqa: E402


@task
def bw1():
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw2():
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw3():
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw4():
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task(pre=[bw1, bw2, bw3, bw4])
def bench_check():
    """Composite check over the four simulated steps (benchmark)."""


dist = group("dist", help="Build and publish")


@dist.task
def build():
    """Build the sdist and wheel."""
    return _run("uv", "build")


@dist.task
def clean():
    """Remove build artifacts."""
    return _run("rm", "-rf", "dist")
