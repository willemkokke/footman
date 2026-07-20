"""The decorator surface: naming, nesting, and collision detection."""

from __future__ import annotations

import pytest

from footman import registry
from footman.registry import Group, RegistrationError


def test_sample_fixture_stays_out_of_the_global_registry(root):
    # F64: the `root` fixture loads the sample tasks under registry.capture(), so
    # they populate the fixture's group but never leak into the global root.
    assert "check" in root.tasks  # the fixture really did load the sample tasks
    assert "check" not in registry.root.tasks  # ...but not into the process global


def test_task_name_normalised_to_hyphens():
    g = Group("root")

    @g.task
    def add_word(): ...

    assert "add-word" in g.tasks


def test_group_name_normalised_and_returned():
    g = Group("root")
    sub = g.group("my_group", help="h")
    assert sub.name == "my-group"
    assert "my-group" in g.groups
    assert sub.help == "h"


def test_explicit_name_override():
    g = Group("root")

    @g.task(name="build")
    def docs_build(): ...

    assert "build" in g.tasks
    assert "docs-build" not in g.tasks


def test_duplicate_task_rejected():
    g = Group("root")

    @g.task
    def a(): ...

    with pytest.raises(ValueError, match="already has a task"):

        @g.task(name="a")
        def other(): ...


def test_task_group_collision_rejected():
    g = Group("root")
    g.group("x")

    with pytest.raises(ValueError, match="already has a group"):

        @g.task(name="x")
        def x(): ...


def test_collision_is_a_registration_error():
    g = Group("root")

    @g.task
    def build(): ...

    with pytest.raises(RegistrationError, match="already has a task"):
        g.task(name="build")(lambda: None)


def test_infinite_implies_no_progress():
    from footman.registry import Group, is_infinite, wants_progress

    g = Group("root")

    @g.task(infinite=True)
    def serve(): ...

    @g.task
    def plain(): ...

    @g.task(progress=False)
    def repl(): ...

    assert is_infinite(serve) and not wants_progress(serve)  # the implication
    assert not is_infinite(plain) and wants_progress(plain)
    assert not is_infinite(repl) and not wants_progress(repl)  # timing-only opt-out
