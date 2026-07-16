"""End-to-end: the execution path from argv to exit code."""

from __future__ import annotations

import json

import pytest

from footman import _app, _paths

TASKS = '''
from footman import task, group

@task
def hi(name: str = "world"):
    """Say hello."""
    print(f"hello {name}")

@task
def add(a: int, b: int):
    """Print a sum."""
    print(a + b)

@task
def boom():
    """Fail on purpose."""
    raise SystemExit(2)

@task
def flag(fix: bool = False):
    """A flag task."""
    print(f"fix={fix}")

@task
def crash():
    """Raise a real exception."""
    raise RuntimeError("kaboom")

tools = group("tools", help="Extra tools")

@tools.task
def echo(*words: str):
    """Echo words."""
    print(" ".join(words))
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_run_a_task(project, capsys):
    assert _app.run(["hi", "--name", "footman"]) == 0
    assert "hello footman" in capsys.readouterr().out


def test_chain_with_coercion(project, capsys):
    assert _app.run(["add", "2", "3", "hi"]) == 0
    out = capsys.readouterr().out
    assert "5" in out
    assert "hello world" in out


def test_group_task_variadic(project, capsys):
    assert _app.run(["tools", "echo", "a", "b", "c"]) == 0
    assert "a b c" in capsys.readouterr().out


def test_version(project, capsys):
    from footman import __version__

    assert _app.run(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_list_with_no_segments(project, capsys):
    assert _app.run([]) == 0
    out = capsys.readouterr().out
    assert "hi" in out and "tools echo" in out


def test_dry_run_does_not_execute(project, capsys):
    assert _app.run(["--dry-run", "hi", "--name", "x"]) == 0
    out = capsys.readouterr().out
    assert "hi" in out
    assert "hello x" not in out


def test_json_output(project, capsys):
    assert _app.run(["--json", "hi"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["task"] == "hi"
    assert payload[0]["ok"] is True


def test_failing_task_sets_exit_code(project):
    assert _app.run(["boom"]) == 2


def test_unknown_task_is_teaching_error(project, capsys):
    assert _app.run(["nope"]) == 2
    assert "expected a task name" in capsys.readouterr().err


def test_where(project, capsys):
    assert _app.run(["--where", "hi"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("tasks.py:5") or ":" in out


def test_missing_tasks_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == 2
    assert "no tasks file found" in capsys.readouterr().err


def test_missing_tasks_file_with_list_is_soft(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    assert "No tasks file found" in capsys.readouterr().out


def test_tree_output(project, capsys):
    assert _app.run(["--tree"]) == 0
    out = capsys.readouterr().out
    assert "tools/" in out
    assert "echo" in out


def test_timings(project, capsys):
    assert _app.run(["--timings", "hi"]) == 0
    assert "ms)" in capsys.readouterr().out


def test_quiet_suppresses_summary(project, capsys):
    assert _app.run(["--quiet", "hi"]) == 0
    out = capsys.readouterr().out
    assert "hello world" in out  # task output still streams
    assert "ok  hi" not in out  # but the summary line is suppressed


def test_install_completion_is_stub(project, capsys):
    assert _app.run(["--install-completion", "bash"]) == 1
    assert "not wired up yet" in capsys.readouterr().err


def test_directory_bad(project, capsys):
    assert _app.run(["-C", str(project / "nope"), "hi"]) == 2
    assert "-C" in capsys.readouterr().err


def test_unknown_global(project, capsys):
    assert _app.run(["--nope"]) == 2
    assert "unknown global option" in capsys.readouterr().err


def test_passthrough_without_varargs(project, capsys):
    assert _app.run(["hi", "--", "x"]) == 2
    assert "nothing after" in capsys.readouterr().err


def test_where_unknown(project, capsys):
    assert _app.run(["--where", "nope"]) == 2
    assert "unknown task" in capsys.readouterr().err


def test_keep_going_via_cli(project, capsys):
    assert _app.run(["-k", "boom", "hi"]) == 2
    assert "hello world" in capsys.readouterr().out  # hi ran despite boom failing


def test_dry_run_flag_variadic_passthrough(project, capsys):
    assert (
        _app.run(
            ["--dry-run", "flag", "--no-fix", "+", "tools", "echo", "a", "--", "b"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "--no-fix" in out
    assert "*a" in out
    assert "[-- b]" in out


def test_tasks_file_override(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    alt = tmp_path / "custom.py"
    alt.write_text(
        "from footman import task\n\n@task\ndef only():\n    print('only-ran')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["-f", str(alt), "only"]) == 0
    assert "only-ran" in capsys.readouterr().out


def test_config_tasks_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\ntasks = 'custom.py'\n"
    )
    (tmp_path / "custom.py").write_text(
        "from footman import task\n\n@task\ndef only():\n    print('cfg-ran')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["only"]) == 0
    assert "cfg-ran" in capsys.readouterr().out


def test_corrupt_pyproject_falls_back_to_default(project, capsys):
    (project / "pyproject.toml").write_text("this is : not valid toml [[[")
    assert _app.run(["hi"]) == 0  # config lookup fails gracefully, tasks.py used
    assert "hello world" in capsys.readouterr().out


def test_tasks_import_failure(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == 2
    assert "failed to import" in capsys.readouterr().err


def test_exception_is_reported(project, capsys):
    assert _app.run(["crash"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "kaboom" in err


def test_dry_run_shows_true_flag(project, capsys):
    assert _app.run(["--dry-run", "flag", "--fix"]) == 0
    assert "--fix" in capsys.readouterr().out


def test_empty_task_list(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("# no tasks here\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run([]) == 0
    assert "No tasks defined" in capsys.readouterr().out
