"""footman's own tasks — dogfooding the runner it defines.

Run with ``fm <task>`` (or ``uv run fm <task>`` before it is installed).
Chaining works: ``fm format lint --fix test``.
"""

from __future__ import annotations

import subprocess

from footman import group, task

SRC = ("src", "tests")


def _run(*cmd: str) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(["uv", "run", *cmd]).returncode


@task
def lint(fix: bool = False):
    """Lint with ruff."""
    cmd = ["ruff", "check", *SRC]
    if fix:
        cmd.append("--fix")
    return _run(*cmd)


@task
def format(check: bool = False):
    """Format with ruff."""
    cmd = ["ruff", "format", *SRC]
    if check:
        cmd.append("--check")
    return _run(*cmd)


@task
def typecheck():
    """Type-check with basedpyright."""
    return _run("basedpyright")


@task
def test(*pytest_args: str):
    """Run the test suite (extra pytest args after --)."""
    return _run("pytest", *pytest_args)


@task
def check():
    """Run format --check, lint, typecheck, and tests in sequence."""
    for step in (lambda: format(check=True), lint, typecheck, test):
        code = step()
        if code:
            return code
    return 0


dist = group("dist", help="Build and publish")


@dist.task
def build():
    """Build the sdist and wheel."""
    return _run("python", "-m", "uv", "build")


@dist.task
def clean():
    """Remove build artifacts."""
    return _run("python", "-c", "import shutil; shutil.rmtree('dist', True)")
