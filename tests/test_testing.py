"""The public testing surface: Runner, recording(), use_context, fixtures.

The pytest fixtures (`fm_project`, `fm_record`) are exercised directly here —
footman's own suite consuming its own plugin is the point.
"""

from __future__ import annotations

from footman import Context, run, use_context
from footman.app import App
from footman.registry import Group
from footman.testing import Runner, recording

# --- recording() / use_context ------------------------------------------------


def test_recording_captures_without_executing(tmp_path):
    marker = tmp_path / "should-not-exist"

    def deploy():
        run(f"touch {marker}")
        run("git push --tags")

    with recording() as steps:
        deploy()
    assert [s.command for s in steps] == [f"touch {marker}", "git push --tags"]
    assert not marker.exists()


def test_recording_is_silent(capsys):
    with recording():
        run("echo NOPE")
    assert capsys.readouterr().out == ""


def test_use_context_installs_and_restores():
    ctx = Context(env={"MODE": "test"})
    with use_context(ctx) as installed:
        assert installed is ctx
        run("echo hi", silent=True)
    assert ctx.steps[0].command == "echo hi"
    # Outside the block a fresh default context applies again.
    with recording() as steps:
        run("echo bye")
    assert steps[0].command == "echo bye"
    assert len(ctx.steps) == 1


# --- Runner with an in-memory Group --------------------------------------------


def _demo_group() -> Group:
    g = Group("root")

    @g.task
    def greet(name: str = "world"):
        """Say hello."""
        print(f"hello {name}")

    @g.task
    def fail():
        """Exit non-zero."""
        raise SystemExit(3)

    return g


def test_runner_group_invoke():
    result = Runner().invoke("greet --name tester", tasks=_demo_group())
    assert result.ok
    assert "hello tester" in result.stdout
    assert [r.task for r in result.results] == ["greet"]


def test_runner_group_failure_is_returned_not_raised():
    result = Runner().invoke("fail", tasks=_demo_group())
    assert result.exit_code == 3
    assert not result.ok


def test_runner_group_chain_error_teaches():
    result = Runner().invoke("nope", tasks=_demo_group())
    assert result.exit_code == 2
    assert "expected a task name" in result.stderr


def test_runner_group_dry_run_matches_cli_semantics():
    result = Runner().invoke("--dry-run greet --name x", tasks=_demo_group())
    assert result.ok
    assert "greet" in result.stdout
    assert "hello x" not in result.stdout  # planned, not executed


# --- Runner against a project on disk ------------------------------------------

TASKS = """
from footman import task, run

@task
def hi(name: str = "world"):
    "Say hello."
    print(f"hello {name}")
"""


def test_runner_tasks_path_uses_single_file(tmp_path):
    tasks = tmp_path / "mytasks.py"
    tasks.write_text(TASKS)
    result = Runner().invoke("hi --name path", tasks=tasks, cwd=tmp_path)
    assert result.ok
    assert "hello path" in result.stdout
    assert result.results[0].task == "hi"


def test_runner_discovers_cascade_from_cwd(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(TASKS)
    result = Runner().invoke("hi", cwd=tmp_path)
    assert result.ok
    assert "hello world" in result.stdout


def test_runner_branded_app_prefixes_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(TASKS)
    acme = Runner(App(name="Acme", prog="acme", version="9.9.9"))
    result = acme.invoke("nope", cwd=tmp_path)
    assert result.exit_code == 2
    assert result.stderr.startswith("acme:")
    version = acme.invoke("--version", cwd=tmp_path)
    assert "Acme 9.9.9" in version.stdout


# --- the pytest fixtures (dogfooding the plugin) --------------------------------


def test_fm_project_fixture_scaffolds_and_runs(fm_project):
    fm = fm_project(
        """
        from footman import task

        @task
        def ping():
            "Pong."
            print("pong")
        """
    )
    result = fm.invoke("ping")
    assert result.ok
    assert "pong" in result.stdout


def test_fm_project_fixture_custom_tasks_filename(fm_project):
    fm = fm_project(
        """
        from footman import task

        @task
        def jobs_only():
            print("via-jobs")
        """,
        name="jobs.py",
    )
    result = fm.invoke("jobs-only")
    assert result.ok
    assert "via-jobs" in result.stdout


def test_fm_record_fixture_captures_steps(fm_record):
    def build():
        run("cargo build --release")

    build()
    assert fm_record[0].command == "cargo build --release"
