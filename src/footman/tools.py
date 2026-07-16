"""Typed wrappers around common dev tools, built on `footman.run`.

Each wrapper builds the command and runs it through the current task context, so
it inherits capture, replay-on-failure, dry-run, and progress. Tools that ship a
Python entry point (pytest) run **in-process**; binaries (ruff, basedpyright,
uv) run as subprocesses — either way you get typed, autocompletable options and
a typo-proof command line. Importing this module is cheap; a tool's own package
is only imported if you ask for its in-process path.
"""

from __future__ import annotations

import sys

from footman.context import run


def ruff(*args: str, fix: bool = False, nofail: bool = False) -> int:
    """Run ruff (the linter). `ruff("check", "src", fix=True)`."""
    cmd = ["ruff", *args]
    if fix:
        cmd.append("--fix")
    return run(cmd, nofail=nofail)


def ruff_format(*args: str, check: bool = False, nofail: bool = False) -> int:
    """Run `ruff format`. `ruff_format("src", check=True)`."""
    cmd = ["ruff", "format", *args]
    if check:
        cmd.append("--check")
    return run(cmd, nofail=nofail)


def basedpyright(*args: str, nofail: bool = False) -> int:
    """Run basedpyright (the type checker)."""
    return run(["basedpyright", *args], nofail=nofail)


def pytest(*args: str, in_process: bool = True, nofail: bool = False) -> int:
    """Run pytest — in-process via `pytest.main` when available (no subprocess)."""
    if in_process:
        try:
            import pytest as _pytest
        except ImportError:
            pass
        else:
            title = " ".join(["pytest", *args])
            return run(_pytest.main, list(args), title=title, nofail=nofail)
    return run(["pytest", *args], nofail=nofail)


def uv(*args: str, nofail: bool = False) -> int:
    """Run a uv subcommand. `uv("build")`, `uv("sync")`."""
    return run(["uv", *args], nofail=nofail)


def python(*args: str, nofail: bool = False) -> int:
    """Run the current interpreter. `python("-m", "build")`."""
    return run([sys.executable, *args], nofail=nofail)


def sh(command: str, nofail: bool = False) -> int:
    """Run a command line given as a single string."""
    return run(command, nofail=nofail)
