"""Parameter forwarding: a `forward`-marked value threads to dispatched tasks."""

from __future__ import annotations

from typing import Annotated

import pytest

from footman import manifest
from footman.executor import forward_map, run_chain
from footman.params import Forward, forward
from footman.registry import Group
from footman.split import ChainError, Segment, split_chain


def drive(build, line):
    reg = Group("root")
    build(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    run_chain(reg, segments)


def test_forward_threads_into_a_prerequisite():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(fix: bool = False):
            seen["build"] = fix

        @reg.task(pre=[build])
        def check(fix: Annotated[bool, forward] = False):
            seen["check"] = fix

    drive(tasks, "check --fix")
    assert seen == {"build": True, "check": True}


def test_forward_of_the_default_overrides_the_callee_default():
    # No CLI value given: the caller forwards its *own* default, which still
    # overrides the callee's default (forward supplies a value either way).
    seen = {}

    def tasks(reg):
        @reg.task
        def build(fix: bool = True):  # callee would default True…
            seen["build"] = fix

        @reg.task(pre=[build])
        def check(fix: Forward[bool] = False):  # …but the caller's False wins
            pass

    drive(tasks, "check")
    assert seen["build"] is False


def test_forward_is_partial_untaken_prereqs_run_defaulted():
    seen = {}

    def tasks(reg):
        @reg.task
        def spelling():  # no `fix` parameter — untouched
            seen["spelling"] = "ran"

        @reg.task
        def pyfix(fix: bool = False):
            seen["pyfix"] = fix

        @reg.task(pre=[pyfix, spelling])
        def lint(fix: Forward[bool] = False):
            pass

    drive(tasks, "lint --fix")
    assert seen == {"pyfix": True, "spelling": "ran"}


def test_forward_reaches_post_prerequisites_too():
    seen = {}

    def tasks(reg):
        @reg.task
        def notify(fix: bool = False):
            seen["notify"] = fix

        @reg.task(post=[notify])
        def deploy(fix: Forward[bool] = False):
            pass

    drive(tasks, "deploy --fix")
    assert seen["notify"] is True


def test_conflicting_forwards_to_a_shared_prereq_is_taught():
    def tasks(reg):
        @reg.task
        def shared(fix: bool = False):
            pass

        @reg.task(pre=[shared])
        def a(fix: Forward[bool] = True):
            pass

        @reg.task(pre=[shared])
        def b(fix: Forward[bool] = False):
            pass

    with pytest.raises(ChainError, match=r"forwarded with conflicting values"):
        drive(tasks, "a b")


def test_forward_map_reads_cli_or_default_and_skips_required():
    # `forward_map` is side-effect free and only defaulted params contribute:
    # a required one is never forwarded (the conservative rule).
    reg = Group("root")

    @reg.task
    def deploy(target: Forward[str], fix: Forward[bool] = False):
        pass

    # even with a CLI value present, the required `target` is excluded.
    seg = Segment(
        task="deploy", path=["deploy"], values={"target": "prod", "fix": True}
    )
    assert forward_map(deploy, seg) == {"fix": True}
    # bare segment: the defaulted `fix` contributes its default.
    assert forward_map(deploy, Segment(task="deploy", path=["deploy"])) == {
        "fix": False
    }
