"""invoke equivalent of the shared task surface.

Notes vs footman:
- every task takes an explicit ``c``/``ctx`` first parameter;
- invoke DOES have real ``--flags`` (closest incumbent on grammar);
- nested namespaces work but must be assembled by hand into a Collection;
- ``bool`` params become ``--fix``, but there is no eager choice validation.
"""

# invoke's public re-exports and its @task decorator confuse the type-checker;
# this is idiomatic invoke, so quiet those two rules for this demo file.
# pyright: reportPrivateImportUsage=false, reportArgumentType=false

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _project  # noqa: F401  (simulated project import cost)
from invoke import Collection, task

SRC = "src tests"


@task
def lint(c, fix=False):
    """Lint with ruff."""
    c.run(f"ruff check {SRC}" + (" --fix" if fix else ""))


@task
def format(c, check=False):
    """Format with ruff."""
    c.run(f"ruff format {SRC}" + (" --check" if check else ""))


@task
def typecheck(c):
    """Type-check with basedpyright."""
    c.run("basedpyright")


@task
def test(c, args=""):
    """Run the test suite."""
    c.run(f"pytest {args}")


@task
def check(c):
    """Run format, lint, typecheck, and test."""
    format(c, check=True)
    lint(c)
    typecheck(c)
    test(c)


@task
def noop(c):
    """No-op (execution-overhead benchmark)."""


# --- orchestration benchmark: four identical I/O-bound steps ------------------
# invoke runs pre-tasks serially (no parallel option exists), so its composite
# is the serial sum.
import time  # noqa: E402


@task
def bw1(c):
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw2(c):
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw3(c):
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task
def bw4(c):
    """Simulated check step (0.5 s)."""
    time.sleep(0.5)


@task(pre=[bw1, bw2, bw3, bw4])
def bench_check(c):
    """Composite check over the four simulated steps (benchmark)."""


@task(name="build")
def dist_build(c):
    """Build the sdist and wheel."""
    c.run("uv build")


@task(name="clean")
def dist_clean(c):
    """Remove build artifacts."""
    c.run("rm -rf dist")


dist = Collection("dist")
dist.add_task(dist_build)
dist.add_task(dist_clean)

ns = Collection()
for t in (lint, format, typecheck, test, check, noop, bw1, bw2, bw3, bw4, bench_check):
    ns.add_task(t)
ns.add_collection(dist)
