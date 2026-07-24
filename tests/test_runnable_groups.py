"""Runnable groups: `@group.default` makes `fm <group>` run an action."""

from __future__ import annotations

import pytest

from footman import manifest
from footman.executor import run_chain
from footman.params import Forward
from footman.registry import (
    Group,
    RegistrationError,
    is_atomic,
    is_interactive,
    keeps_going,
    pre_tasks,
    task_confirm,
)
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


# --- empty-body fan-out + forward threading ----------------------------------


def _surfaces(reg):
    seen = {}
    lint = reg.group("lint")

    @lint.task
    def python(fix: bool = False):
        seen["python"] = fix

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.task
    def spelling():  # no fix parameter
        seen["spelling"] = "ran"

    @lint.default
    def lint_all(fix: Forward[bool] = False):  # empty body -> fan out
        """Lint everything."""

    return seen


def test_empty_body_default_fans_out_the_groups_tasks():
    reg = Group("root")
    seen = _surfaces(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint"])
    run_chain(reg, segs)
    assert seen == {"python": False, "markdown": False, "spelling": "ran"}


def test_fan_out_threads_the_flag_only_to_surfaces_that_declare_it():
    reg = Group("root")
    seen = _surfaces(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    # fix reaches python/markdown; spelling has no such parameter and just runs.
    assert seen == {"python": True, "markdown": True, "spelling": "ran"}


def test_a_custom_body_default_does_not_auto_fan_out():
    ran = []

    def tasks(reg):
        lint = reg.group("lint")

        @lint.task
        def python(fix: bool = False):
            ran.append("python")

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            ran.append("custom")  # a real body is the escape hatch

    drive(tasks, "lint")
    assert ran == ["custom"]  # the surfaces did not run implicitly


def test_forward_chains_through_a_group_used_as_a_prerequisite():
    # `check` forwards --fix into the `lint` group (a pre= target); lint's
    # default re-forwards it to the surfaces that declare it. Declarative check.
    seen = {}

    def tasks(reg):
        lint = reg.group("lint")

        @lint.task
        def python(fix: bool = False):
            seen["python"] = fix

        @lint.task
        def spelling():
            seen["spelling"] = "ran"

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            """Lint everything."""

        @reg.task
        def test():
            seen["test"] = "ran"

        @reg.task(pre=[lint, test])
        def check(fix: Forward[bool] = False):
            """Format, lint, test."""

    drive(tasks, "check --fix")
    assert seen == {"python": True, "spelling": "ran", "test": "ran"}


def test_completion_offers_the_default_flags_alongside_children():
    from footman._complete import complete

    reg = Group("root")
    _surfaces(reg)  # lint with python/markdown/spelling + a fix default
    tree = manifest.build_manifest(reg)["tree"]
    offered = {c.split("\t")[0] for c in complete(tree, ["lint", ""])}
    assert "--fix" in offered  # the default's flag
    assert {"python", "markdown", "spelling"} <= offered  # and the children
    assert complete(tree, ["lint", "--f"]) == ["--fix"]


# --- @group.default options ---------------------------------------------------


def test_default_takes_task_policy_options():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default(keep_going=True, atomic=True, confirm="lint everything?")
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    assert keeps_going(lint_all) is True
    assert is_atomic(lint_all) is True
    assert task_confirm(lint_all) == "lint everything?"


def test_default_pre_runs_before_the_default():
    ran = []

    def tasks(reg):
        @reg.task
        def bootstrap():
            ran.append("bootstrap")

        lint = reg.group("lint")

        @lint.default(pre=[bootstrap])
        def lint_all():
            ran.append("lint")

    drive(tasks, "lint")
    assert ran == ["bootstrap", "lint"]


def test_bare_default_still_registers_with_no_options():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    assert reg.groups["lint"].default_task is lint_all
    assert keeps_going(lint_all) is None
    assert pre_tasks(lint_all) == []


def test_interactive_on_an_empty_body_default_is_rejected():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def python(fix: bool = False): ...

    with pytest.raises(RegistrationError, match=r"empty body.*own the terminal"):

        @lint.default(interactive=True)
        def lint_all(fix: Forward[bool] = False):
            """Empty body -> fans out; cannot own the terminal."""


def test_interactive_on_a_custom_body_default_is_allowed():
    reg = Group("root")
    shell = reg.group("shell")

    @shell.default(interactive=True)
    def repl():
        print("would drop into a REPL")

    assert is_interactive(repl) is True


def test_default_still_rejects_a_positional_parameter():
    reg = Group("root")
    lint = reg.group("lint")

    with pytest.raises(RegistrationError, match=r"positional parameter"):

        @lint.default(keep_going=True)
        def lint_all(path: str):
            """A positional is a child name, not a value."""


# --- body-callability: a runnable group is callable from a task body ----------


def test_empty_body_group_is_callable_from_a_body_and_fans_out():
    reg = Group("root")
    seen = _surfaces(reg)  # lint: python/markdown (fix) + spelling (no fix)
    reg.groups["lint"](fix=True)  # the imperative echo of `fm lint --fix`
    # Partial reach, by name: fix reaches the surfaces that declare it; spelling
    # runs bare. Same result as the CLI fan-out, driven from a body.
    assert seen == {"python": True, "markdown": True, "spelling": "ran"}


def test_custom_body_group_call_runs_the_body_only():
    reg = Group("root")
    seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        seen["default"] = fix  # a real body is the escape hatch

    lint(fix=True)
    assert seen == {"default": True}  # the body ran; the surface stayed untouched


def test_calling_a_group_without_a_default_is_a_taught_error():
    reg = Group("root")
    docs = reg.group("docs")

    @docs.task
    def build(): ...

    with pytest.raises(TypeError, match=r"not runnable"):
        docs()


def test_a_task_body_runs_a_group_and_forwards_through_the_runner():
    # End-to-end: `check --fix` runs through the scheduler, and check's body
    # calls the lint group, which fans out with the forwarded flag.
    reg = Group("root")
    seen = _surfaces(reg)
    lint = reg.groups["lint"]

    @reg.task
    def check(fix: bool = False):
        lint(fix=fix)
        seen["check"] = fix

    tree = manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["check", "--fix"])
    run_chain(reg, segs)
    assert seen == {
        "python": True,
        "markdown": True,
        "spelling": "ran",
        "check": True,
    }
