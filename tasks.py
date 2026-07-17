"""footman's own tasks — dogfooding the runner and its run()/tools helpers.

Run with ``fm <task>`` (or ``uv run fm <task>`` before it is installed).
Chaining works: ``fm format lint --fix test``.
"""

from __future__ import annotations

from footman import group, parallel, run, task, tools

SRC = ("src", "tests")


@task
def lint(fix: bool = False):
    """Lint with ruff."""
    tools.ruff.check(*SRC, fix=fix)


@task
def format(check: bool = False):
    """Format with ruff."""
    tools.ruff_format(*SRC, check=check)


@task
def typecheck():
    """Type-check with basedpyright."""
    tools.basedpyright()


@task
def test(*pytest_args: str):
    """Run the test suite (extra pytest args after --)."""
    tools.pytest(*pytest_args, in_process=False)


@task
def check():
    """Run format --check, lint, typecheck, and test — in parallel."""
    parallel(lambda: format(check=True), lint, typecheck, test)


docs = group("docs", help="Documentation site (Zensical)")


@docs.task
def serve():
    """Build and serve the docs with live reload."""
    run("zensical serve")


@docs.task
def coverage():
    """Generate the coverage HTML report into docs/htmlcov (embedded in the site)."""
    run("pytest --cov=footman --cov-report=html:docs/htmlcov -q")


@docs.task(name="build")
def docs_build(check: bool = False):
    """Build the docs site into ./site (strict on --check)."""
    # A conditional flag needs no ternary: strict=check is --strict when
    # check is true, omitted otherwise (strict is off by default in zensical).
    tools.zensical.build(clean=True, strict=check, in_process=False)


dist = group("dist", help="Build and publish")


@dist.task
def build():
    """Build the sdist and wheel."""
    tools.uv("build")


@dist.task
def clean():
    """Remove build artifacts."""
    run("rm -rf dist")
