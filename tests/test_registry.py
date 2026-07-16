"""The decorator surface: naming, nesting, and collision detection."""

from __future__ import annotations

import pytest

from footman.registry import Group, RegistrationError


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
