"""Runnable groups: `@group.default` makes `fm <group>` run an action."""

from __future__ import annotations

import pytest

from footman import manifest
from footman.executor import run_chain
from footman.params import Forward
from footman.registry import Group
from footman.split import ChainError, split_chain


def drive(build, line):
    reg = Group("root")
    build(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    run_chain(reg, segments)
    return [s.task for s in segments]


def _lint(reg):
    seen = reg._seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        seen["default"] = fix

    return seen


def test_bare_group_runs_its_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint"])
    run_chain(reg, segs)
    assert seen == {"default": False}
    assert [s.task for s in segs] == ["lint"]


def test_group_flag_reaches_the_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    assert seen == {"default": True}


def test_targeting_a_child_runs_the_child_not_the_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "markdown", "--fix"])
    run_chain(reg, segs)
    assert seen == {"markdown": True}  # the default never ran
    assert [s.task for s in segs] == ["lint.markdown"]


def test_a_trailing_target_opens_a_new_segment_after_the_default():
    ran = []

    def tasks(reg):
        lint = reg.group("lint")

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            ran.append("lint")

        @reg.task
        def test():
            ran.append("test")

    segs = drive(tasks, "lint test")
    assert segs == ["lint", "test"]
    assert ran == ["lint", "test"]


def test_a_group_without_a_default_is_still_a_taught_error():
    def tasks(reg):
        plain = reg.group("plain")

        @plain.task
        def sub(): ...

    with pytest.raises(ChainError, match=r"expected a task name"):
        drive(tasks, "plain")
