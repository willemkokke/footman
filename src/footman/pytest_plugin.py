"""Pytest fixtures for testing tasks — auto-loaded when footman is installed.

Registered through the `pytest11` entry point, so `pip install footman`
alongside pytest is all it takes; there is nothing to enable. Each fixture is
a thin shim over `footman.testing` — non-pytest users get the same power from
that module directly.

Two deliberate laziness rules: this module is only ever imported by pytest
itself (footman's runtime stays zero-dependency), and it imports nothing from
footman at module level — pytest loads entry-point plugins before coverage
tools start measuring, so an eager import here would make every downstream
project's coverage of footman-adjacent code look worse than it is.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from footman.context import Result
    from footman.testing import Runner

__all__ = ["fm", "fm_project", "fm_record"]


@pytest.fixture
def fm() -> Runner:
    """A `Runner` for the project the test process runs in."""
    from footman.testing import Runner

    return Runner()


@pytest.fixture
def fm_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Runner]:
    """Factory: scaffold an isolated project from tasks-file source.

    ```python
    def test_release(fm_project):
        fm = fm_project('''
            from footman import task, run

            @task
            def release(version: str):
                run(f"git tag v{version}")
        ''')
        assert fm.invoke("--dry-run release 1.2.0").ok
    ```

    Writes a minimal `pyproject.toml` plus the tasks file into `tmp_path`,
    chdirs there for the test, and returns a `Runner`. Pass `name=` to use a
    non-default tasks filename (wired up via `[tool.footman] tasks`).
    """
    from footman.testing import Runner

    def make(source: str, *, name: str = "tasks.py") -> Runner:
        config = "" if name == "tasks.py" else f"\n[tool.footman]\ntasks = '{name}'\n"
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "test"\nversion = "0"\n{config}',
            encoding="utf-8",
        )
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        return Runner()

    return make


@pytest.fixture
def fm_record() -> Iterator[list[Result]]:
    """Recorded steps for the whole test: task code runs, commands don't.

    ```python
    def test_lint_fix(fm_record):
        from tasks import lint
        lint(fix=True)
        assert fm_record[0].command == "ruff check . --fix"
    ```
    """
    from footman.testing import recording

    with recording() as steps:
        yield steps
