"""The pytest fixtures footman ships: `fm`, `fm_project`, `fm_record`.

These are published API — a user's `def test_x(fm_project)` is the whole
point of the `pytest11` entry point — so they are driven the way a user
gets them: pytest loading the installed plugin in a subprocess project,
not by calling the fixture functions directly. `pytester` runs a real
pytest inside the test, which also proves the entry point itself works.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


def test_fm_project_scaffolds_and_runs(pytester: pytest.Pytester):
    """The factory writes a project, chdirs into it, and hands back a
    Runner whose invoke() drives the real CLI."""
    pytester.makepyfile(
        """
        def test_release(fm_project):
            fm = fm_project('''
                from footman import task, run

                @task
                def release(version: str):
                    "Cut a release."
                    run(f"git tag v{version}")
            ''')
            result = fm.invoke("--dry-run release 1.2.0")
            assert result.ok
            assert "release" in result.stdout
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_project_honours_a_custom_tasks_filename(pytester: pytest.Pytester):
    """`name=` writes that filename *and* wires `[tool.footman] tasks` so
    the cascade actually finds it."""
    pytester.makepyfile(
        """
        def test_named(fm_project):
            fm = fm_project('''
                from footman import task

                @task
                def ship():
                    "Ship it."
            ''', name="acmetasks.py")
            assert "ship" in fm.invoke("--list").stdout
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_record_captures_commands_without_running_them(pytester: pytest.Pytester):
    """The recording fixture spans the whole test: task code runs, the
    commands it would issue are captured instead of executed."""
    pytester.makepyfile(
        """
        from footman import run, task

        @task
        def lint(fix: bool = False):
            "Lint."
            run("ruff check ." + (" --fix" if fix else ""))

        def test_lint_fix(fm_record):
            lint(fix=True)
            assert fm_record[0].command == "ruff check . --fix"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_runner_targets_the_current_project(pytester: pytest.Pytester):
    """The bare `fm` fixture drives whatever project the test runs in."""
    pytester.makepyfile(
        tasks="""
        from footman import task

        @task
        def hello():
            "Say hello."
            print("hi from the project")
        """
    )
    pytester.makepyfile(
        """
        def test_hello(fm):
            result = fm.invoke("hello")
            assert result.ok
            assert "hi from the project" in result.stdout
        """
    )
    pytester.makepyprojecttoml('[project]\nname = "demo"\nversion = "0"\n')
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_plugin_loads_from_its_entry_point(pytester: pytest.Pytester):
    """A genuinely separate pytest process, no `-p` flag: the fixtures
    arrive through the `pytest11` entry point, which is the only path an
    installing project ever uses. (Subprocess, so coverage cannot see it —
    the point here is the install path, not the lines.)"""
    pytester.makepyfile(
        """
        def test_fixtures_exist(fm, fm_record):
            assert fm is not None and fm_record == []
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=1)


def test_importing_footman_stays_lazy_under_the_plugin():
    """The plugin module must not import footman at module level: pytest
    loads entry-point plugins before coverage starts, so an eager import
    would make every downstream project's footman coverage look wrong."""
    import subprocess
    import sys

    probe = (
        "import footman.pytest_plugin, sys; "
        "print('footman.testing' in sys.modules or 'footman.context' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
