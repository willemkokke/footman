"""Union types, one-or-many values, and dynamic completion (`suggest`)."""

from __future__ import annotations

from typing import Annotated

import pytest

from footman import manifest
from footman._complete import complete
from footman.executor import run_chain
from footman.params import Many, suggest
from footman.registry import Group
from footman.split import ChainError, split_chain

# A module-level completer so `eval_str` can resolve it from a tasks file that
# uses `from __future__ import annotations` (real completers live at module top).
_DEDUP_CALLS: list[int] = []


def _dedup_projects() -> list[str]:
    _DEDUP_CALLS.append(1)
    return ["a", "b"]


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


# --- union scalar coercion (specificity order) -------------------------------


def test_union_scalar_coercion():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: str | int = "d"):
            seen["x"] = x

    run(tasks, "go --x 5")
    assert seen["x"] == 5 and type(seen["x"]) is int
    run(tasks, "go --x hi")
    assert seen["x"] == "hi"


def test_union_specificity_int_before_float():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: int | float = 0):
            seen["x"] = x

    run(tasks, "go --x 3")
    assert type(seen["x"]) is int and seen["x"] == 3
    run(tasks, "go --x 3.5")
    assert type(seen["x"]) is float and seen["x"] == 3.5


def test_union_validation_error_lists_both():
    def tasks(reg):
        @reg.task
        def bench(n: int | float = 0): ...

    with pytest.raises(ChainError) as exc:
        run(tasks, "bench --n abc")
    assert "expects an integer or a number" in str(exc.value)


# --- list[union] / Many ------------------------------------------------------


def test_list_union_option_repeatable():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(vals: list[str | int] | None = None):
            seen["vals"] = vals

    run(tasks, "go --vals a --vals 3 --vals b")
    assert seen["vals"] == ["a", 3, "b"]


def test_many_positional_variadic():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(targets: Many[str | int]):
            seen["t"] = targets

    run(tasks, "build a 3 b")
    assert seen["t"] == ["a", 3, "b"]


def test_many_positional_requires_at_least_one():
    def tasks(reg):
        @reg.task
        def build(targets: Many[str]): ...

    with pytest.raises(ChainError, match="missing required argument"):
        run(tasks, "build")


# --- one-or-many: single -> scalar, many -> list -----------------------------


def test_one_or_many_collapses_single():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(x: list[str] | str):
            seen["x"] = x

    run(tasks, "build only")
    assert seen["x"] == "only"  # scalar, not ["only"]
    run(tasks, "build a b")
    assert seen["x"] == ["a", "b"]


# --- dynamic completion (suggest) --------------------------------------------


def test_dynamic_choices_filled_and_completed():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha", "beta"])]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["choices"] == ["alpha", "beta"]
    assert spec["dynamic"] == {"strict": True}
    assert set(complete(tree, ["build", ""])) == {"alpha", "beta"}
    assert complete(tree, ["build", "al"]) == ["alpha"]


def test_dynamic_strict_validation_rejects_unknown():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha"])]): ...

    with pytest.raises(ChainError, match="must be one of alpha"):
        run(tasks, "build nope")


def test_dynamic_soft_allows_anything():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha"], strict=False)]):
            seen["p"] = project

    run(tasks, "build anything")
    assert seen["p"] == "anything"


def test_dynamic_did_you_mean():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["myproject", "core"])]): ...

    with pytest.raises(ChainError, match="did you mean 'myproject'"):
        run(tasks, "build myprojet")


def test_bare_callable_is_treated_as_suggest():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, (lambda: ["x"])]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["choices"] == ["x"]
    assert spec["dynamic"] == {"strict": True}


def test_completer_deduped_per_build():
    _DEDUP_CALLS.clear()

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(_dedup_projects)]): ...

        @reg.task
        def deploy(target: Annotated[str, suggest(_dedup_projects)]): ...

    reg = Group("root")
    tasks(reg)
    manifest.build_manifest(reg)
    assert _DEDUP_CALLS == [1]  # one call despite two params sharing the completer


def test_broken_completer_does_not_break_build():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: 1 / 0)]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["choices"] == []  # empty -> soft (validation allows anything)
