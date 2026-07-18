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
    assert payload["schema"] == 1
    assert payload["results"][0]["task"] == "hi"
    assert payload["results"][0]["ok"] is True


def test_failing_task_sets_exit_code(project):
    assert _app.run(["boom"]) == 2


def test_crash_task_exits_1(project):
    assert _app.run(["crash"]) == 1  # a raised exception -> flat 1


def test_unknown_task_is_teaching_error(project, capsys):
    assert _app.run(["nope"]) == 2
    assert "expected a task name" in capsys.readouterr().err


def test_where(project, capsys):
    assert _app.run(["--where", "hi"]) == 0
    out = capsys.readouterr().out.strip()
    # A real pin (not the old `or ":" in out` tautology): the tasks file, and
    # hi's definition line — the decorator (4) on 3.9+, the def (5) on older
    # runtimes, tolerating co_firstlineno variance.
    assert out.startswith(str(project / "tasks.py") + ":")
    assert out.endswith(("tasks.py:4", "tasks.py:5"))


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


def test_missing_tasks_file_with_help_shows_globals(tmp_path, monkeypatch, capsys):
    # F63: `fm --help` with no tasks file shows the globals (so a stuck newcomer
    # learns -f/-C), plus a where-did-I-look note — not a bare one-liner.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "globals" in out and "-f" in out  # global help rendered
    assert "no tasks file found" in out  # with the note


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


def test_install_completion_unknown_shell_teaches(project, capsys):
    assert _app.run(["--install-completion", "tcsh"]) == 2
    assert "bash|zsh|fish" in capsys.readouterr().err


def test_directory_bad(project, capsys):
    assert _app.run(["-C", str(project / "nope"), "hi"]) == 2
    assert "-C" in capsys.readouterr().err


def test_tasks_file_does_not_poison_completion_cache(project):
    # F37: an -f run loads one file; it must not rewrite the cwd's completion
    # manifest (which describes the real cascade), or TAB breaks until the next
    # plain run.
    from pathlib import Path

    assert _app.run(["hi"]) == 0  # plain run writes the cascade's manifest
    cache = _paths.manifest_path(Path.cwd())
    before = cache.read_text()
    assert "hi" in before

    other = project / "other.py"
    other.write_text("from footman import task\n@task\ndef solo(): ...\n")
    assert _app.run(["-f", str(other), "solo"]) == 0
    after = cache.read_text()
    assert after == before  # cache untouched
    assert "solo" not in after


def test_directory_restores_cwd(project):
    # F36: -C must not permanently move the host process (e.g. a test runner).
    import os

    sub = project / "sub"
    sub.mkdir()
    (sub / "tasks.py").write_text("from footman import task\n@task\ndef t(): ...\n")
    before = os.getcwd()
    assert _app.run(["-C", str(sub), "t"]) == 0
    assert os.getcwd() == before


def test_unknown_global(project, capsys):
    assert _app.run(["--nope"]) == 2
    assert "unknown global option" in capsys.readouterr().err


def test_passthrough_without_varargs_is_accepted(project, capsys):
    assert _app.run(["hi", "--", "x"]) == 0  # available via passthrough(), not an error
    assert "hello world" in capsys.readouterr().out


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


# --- --help ------------------------------------------------------------------


def test_help_alone_lists_tasks(project, capsys):
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "hi" in out and "tools echo" in out


def test_help_with_task_shows_usage_without_executing(project, capsys):
    assert _app.run(["--help", "hi"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm hi [--name VALUE]" in out
    assert "Say hello." in out
    assert "hello world" not in out  # the task did not run


def test_help_never_runs_the_chain(project, capsys):
    # `boom` exits 2 when executed; help over it must be a read-only act.
    assert _app.run(["--help", "boom"]) == 0
    assert "Fail on purpose." in capsys.readouterr().out


def test_help_shows_positionals_and_types(project, capsys):
    assert _app.run(["--help", "add"]) == 0
    out = capsys.readouterr().out
    assert "<a>" in out and "<b>" in out
    assert "an integer" in out


def test_help_with_unparseable_chain_degrades_to_listing(project, capsys):
    assert _app.run(["--help", "nope"]) == 0
    assert "hi" in capsys.readouterr().out


def test_help_alone_shows_the_global_options(project, capsys):
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm [globals]" in out
    assert "--dry-run" in out and "--keep-going" in out


def test_help_anywhere_on_the_line_wins(project, capsys):
    # `fm boom --help` must be help, not an execution of `boom` (exit 2) and
    # not an "unknown option" error.
    assert _app.run(["boom", "--help"]) == 0
    assert "Fail on purpose." in capsys.readouterr().out
    assert _app.run(["hi", "-h"]) == 0
    assert "usage: fm hi" in capsys.readouterr().out


def test_help_after_passthrough_is_passthrough(project, capsys):
    # After `--` the token belongs to the task, not to fm.
    assert _app.run(["tools", "echo", "--", "--help"]) == 0
    assert "--help" in capsys.readouterr().out


def test_help_for_a_group(project, capsys):
    assert _app.run(["--help", "tools"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm tools <task>" in out
    assert "Extra tools" in out
    assert "echo" in out


# --- import failures name the culprit ----------------------------------------


def test_tasks_import_failure_names_the_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == 2
    err = capsys.readouterr().err
    assert "failed to import" in err and "tasks.py" in err


def test_tasks_syntax_error_reported_cleanly(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("def broken(:\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == 2
    err = capsys.readouterr().err
    assert "SyntaxError" in err and "tasks.py" in err


def test_duplicate_task_name_is_a_user_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n"
        "@task\n"
        "def build(): ...\n"
        "@task(name='build')\n"
        "def build2(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["build"]) == 2
    err = capsys.readouterr().err
    assert "already has a task named 'build'" in err and "tasks.py" in err
    assert "failed to import" not in err  # a duplicate name, not a crash


# --- config errors are loud ---------------------------------------------------


def test_malformed_cascade_config_warns_and_continues(project, capsys):
    (project / "footman.toml").write_text("this is = not [valid toml\n")
    assert _app.run(["hi"]) == 0
    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert "ignoring malformed config" in captured.err
    assert "footman.toml" in captured.err


def test_malformed_explicit_config_is_an_error(project, capsys):
    (project / "bad.toml").write_text("this is = not [valid toml\n")
    assert _app.run(["--config", "bad.toml", "hi"]) == 2
    err = capsys.readouterr().err
    assert "--config" in err and "bad.toml" in err


def test_missing_explicit_config_is_an_error(project, capsys):
    # F15: a typo'd --config (prod.tmol) must be loud, not silently ignored.
    assert _app.run(["--config", "prod.tmol", "hi"]) == 2
    err = capsys.readouterr().err
    assert "--config" in err and "no such file" in err and "prod.tmol" in err


# --- Ctrl-C ------------------------------------------------------------------


def test_keyboard_interrupt_exits_130(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef stop():\n    raise KeyboardInterrupt\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["stop"]) == 130
    assert "interrupted" in capsys.readouterr().err
    assert _app.run(["--sequential", "stop"]) == 130
    assert "interrupted" in capsys.readouterr().err
