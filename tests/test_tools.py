"""The tools bridge: mechanical flag translation, subcommands, versions."""

from __future__ import annotations

import sys

import pytest

from footman import tools
from footman.testing import recording


def _one(call) -> str:
    with recording() as steps:
        call()
    assert len(steps) == 1
    return steps[0].command


def test_mechanical_flag_translation():
    cmd = _one(
        lambda: tools.ruff.check(
            "src", "tests", fix=True, select=["E", "F"], output_format="github"
        )
    )
    assert cmd == (
        "ruff check src tests --fix --select E --select F --output-format github"
    )


def test_false_and_none_are_omitted():
    assert _one(lambda: tools.ruff.check("src", fix=False, config=None)) == (
        "ruff check src"
    )


def test_single_letter_kwargs_are_short_flags():
    cmd = _one(lambda: tools.pytest_bin("-q", k="markers"))
    assert cmd == "pytest-bin -q -k markers"


def test_trailing_underscore_escapes_keywords():
    assert _one(lambda: tools.bun.add("left-pad", global_=True)) == (
        "bun add left-pad --global"
    )


def test_subcommands_chain():
    assert _one(lambda: tools.docker.compose.up(detach=True)) == (
        "docker compose up --detach"
    )


def test_any_executable_is_a_tool():
    # No declaration needed — the module fallback bridges anything on PATH.
    assert _one(lambda: tools.terraform("plan", out="tf.plan")) == (
        "terraform plan --out tf.plan"
    )


def test_curated_names_map_to_real_executables():
    assert _one(lambda: tools.markdownlint("docs/index.md")) == (
        "markdownlint-cli2 docs/index.md"
    )
    assert _one(lambda: tools.ruff_format("src", check=True)) == (
        "ruff format src --check"
    )


def test_installed_version_is_cached_and_comparable():
    tools._version_cache.clear()
    version = tools.ruff.installed_version()
    assert version >= (0, 1)
    assert tools._version_cache["ruff"] == version  # second read hits the cache
    assert tools.ruff.installed_version() is not None


def test_installed_version_unreadable_is_taught():
    with pytest.raises((ValueError, FileNotFoundError)):
        tools.Tool("no-such-binary-really").installed_version()


# --- in-process execution ---------------------------------------------------


def test_in_process_never_spawns(monkeypatch):
    # coverage ships a console_scripts entry and is installed (pytest-cov);
    # if the subprocess layer is touched, this fails loudly.
    from footman import context

    def boom(*a, **k):
        raise AssertionError("subprocess used for an in-process tool")

    monkeypatch.setattr(context, "_run_subprocess", boom)
    saved_argv = list(sys.argv)
    assert tools.coverage("--version", nofail=True) == 0
    assert sys.argv == saved_argv  # patched argv is always restored


def test_in_process_demand_without_entry_is_taught():
    with pytest.raises(ValueError, match="no installed console_scripts entry"):
        tools.Tool("no-such-python-tool")("--version", in_process=True)


def test_in_process_preference_falls_back_to_subprocess():
    # git has no console_scripts entry; a preference (not a demand) must
    # degrade to the normal spawn.
    with recording() as steps:
        tools.Tool("git", in_process=True)("status", s=True)
    assert steps[0].command == "git status -s"


def test_in_process_tools_run_concurrently_with_separate_capture(monkeypatch):
    """Two argument-accepting in-process tools must overlap (the barrier
    times out if they serialise) and must not cross-contaminate captures."""
    import threading

    from footman import manifest, schedule
    from footman.registry import Group
    from footman.split import split_chain

    barrier = threading.Barrier(2, timeout=5)

    def make_entry(marker):
        def entry(argv=None):  # accepts args -> direct, lock-free path
            barrier.wait()
            print(f"{marker}-OUT")
            return 0

        return entry

    entries = {"fake-a": make_entry("A"), "fake-b": make_entry("B")}
    monkeypatch.setattr(tools, "_console_entry", entries.get)

    reg = Group("root")

    @reg.task
    def a():
        tools.Tool("fake-a", in_process=True)()

    @reg.task
    def b():
        tools.Tool("fake-b", in_process=True)()

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["a", "b"])
    results = {r.task: r for r in schedule.run_plan(reg, segments)}
    assert results["a"].ok and results["b"].ok
    assert "A-OUT" in results["a"].steps[0].output
    assert "B-OUT" not in results["a"].steps[0].output  # no cross-talk
    assert "B-OUT" in results["b"].steps[0].output


def test_zero_arg_entries_fall_back_to_argv_patching(monkeypatch):
    seen = {}

    def zero_arg_entry():  # reads sys.argv like an old argparse main
        seen["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(tools, "_console_entry", {"legacy": zero_arg_entry}.get)
    saved = list(sys.argv)
    assert tools.Tool("legacy", in_process=True)("build", fast=True) == 0
    assert seen["argv"] == ["legacy", "build", "--fast"]
    assert sys.argv == saved


def test_in_process_preference_survives_subcommand_chaining():
    # `.report` chains off the in-process coverage tool and keeps the mode
    # (checked without executing: real coverage mid-test-session would read
    # the live .coverage data and the project's own fail_under).
    assert tools.coverage.report._prefer_in_process is True
    assert tools.mkdocs.build._prefer_in_process is True
    assert tools.git.status._prefer_in_process is False
