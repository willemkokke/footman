"""Failure policy: the tri-state keep-going resolution and `--fail-fast`."""

from __future__ import annotations

from footman import manifest
from footman.registry import Group
from footman.schedule import resolve_keep_going
from footman.split import _parse_globals, split_chain


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def _segs(tree, line):
    return split_chain(tree, line.split())[1]


def test_cli_choice_wins_over_a_declared_default():
    def tasks(reg):
        @reg.task(keep_going=True)
        def check(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "check")
    assert resolve_keep_going(reg, segs, None) is True  # unspecified -> declared
    assert resolve_keep_going(reg, segs, False) is False  # --fail-fast overrides
    assert resolve_keep_going(reg, segs, True) is True  # -k overrides


def test_no_declaration_falls_back_to_built_in_fail_fast():
    def tasks(reg):
        @reg.task
        def plain(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "plain")
    assert resolve_keep_going(reg, segs, None) is False
    assert resolve_keep_going(reg, segs, True) is True


def test_fail_fast_is_a_recognised_global():
    # It parses as a leading global (before the first task), like --keep-going.
    globals_, i = _parse_globals(["--fail-fast", "check"], 0)
    assert globals_ == ["--fail-fast"] and i == 1
