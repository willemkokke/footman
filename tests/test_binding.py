"""The executor: coercion, variadic/passthrough, and chain semantics."""

from __future__ import annotations

import enum
import uuid
from pathlib import Path
from typing import Literal

import pytest

from footman import manifest
from footman.executor import run_chain
from footman.registry import Group
from footman.split import ChainError, split_chain


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


def _run(build_tasks, line):
    reg = Group("root")
    build_tasks(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return reg, run_chain(reg, segments)


def test_scalar_coercion():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(
            n: int = 1, ratio: float = 0.5, out: Path = Path("."), fix: bool = False
        ):
            seen.update(n=n, ratio=ratio, out=out, fix=fix)

    _, results = _run(tasks, "build --n 5 --ratio 2.5 --out /tmp/x --fix")
    assert results[0].ok
    assert seen == {"n": 5, "ratio": 2.5, "out": Path("/tmp/x"), "fix": True}


def test_literal_and_list_coercion():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(mode: Literal["a", "b"] = "a", nums: list[int] | None = None):
            seen.update(mode=mode, nums=nums)

    _run(tasks, "go --mode b --nums 1 --nums 2")
    assert seen == {"mode": "b", "nums": [1, 2]}


def test_enum_coercion():
    seen = {}

    def tasks(reg):
        @reg.task
        def paint(colour: Colour = Colour.RED):
            seen["colour"] = colour

    _run(tasks, "paint --colour blue")
    assert seen == {"colour": Colour.BLUE}


def test_required_positionals():
    seen = {}

    def tasks(reg):
        @reg.task
        def render(template: Path, output: Path):
            seen.update(template=template, output=output)

    _run(tasks, "render a.j2 out.html")
    assert seen == {"template": Path("a.j2"), "output": Path("out.html")}


def test_variadic_plus_passthrough():
    seen = {}

    def tasks(reg):
        @reg.task
        def run(*cmd: str):
            seen["cmd"] = cmd

    _run(tasks, "run pytest -x -- --maxfail 1")
    assert seen["cmd"] == ("pytest", "-x", "--maxfail", "1")


def test_passthrough_without_varargs_reaches_context():
    from footman import passthrough

    seen = {}

    def tasks(reg):
        @reg.task
        def build(x: int = 1):
            seen["pt"] = passthrough()

    _run(tasks, "build -- a b")
    assert seen["pt"] == ["a", "b"]  # available even with no *args


def test_failure_stops_chain():
    ran = []

    def tasks(reg):
        @reg.task
        def a():
            ran.append("a")
            raise RuntimeError("boom")

        @reg.task
        def b():
            ran.append("b")

    _, results = _run(tasks, "a b")
    assert ran == ["a"]
    assert results[0].ok is False
    assert isinstance(results[0].error, RuntimeError)
    assert len(results) == 1


def test_keep_going_runs_everything():
    ran = []
    reg = Group("root")

    @reg.task
    def a():
        ran.append("a")
        return 1  # non-zero exit code

    @reg.task
    def b():
        ran.append("b")

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["a", "b"])
    results = run_chain(reg, segments, keep_going=True)
    assert ran == ["a", "b"]
    assert results[0].code == 1 and results[0].ok is False
    assert results[1].ok is True


def test_int_return_is_exit_code():
    def tasks(reg):
        @reg.task
        def a():
            return 3

    _, results = _run(tasks, "a")
    assert results[0].ok is False
    assert results[0].code == 3


def test_raised_exception_is_exit_code_1():
    def tasks(reg):
        @reg.task
        def a():
            raise RuntimeError("boom")

    _, results = _run(tasks, "a")
    assert results[0].ok is False
    assert results[0].code == 1  # a raised error carries no code -> flat 1
    assert isinstance(results[0].error, RuntimeError)


def test_positional_only_parameter_binds():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(target: str, /):
            seen["target"] = target

    _, results = _run(tasks, "build web")
    assert results[0].ok
    assert seen["target"] == "web"


def test_positional_only_mixed_with_regular():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(a: str, /, b: int = 2):
            seen["ab"] = (a, b)

    _, results = _run(tasks, "f hello --b 5")
    assert results[0].ok
    assert seen["ab"] == ("hello", 5)


def test_positional_only_default_hole_is_filled():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(a: str = "x", b: str = "y", /):
            seen["ab"] = (a, b)

    _, results = _run(tasks, "f --b z")
    assert results[0].ok
    assert seen["ab"] == ("x", "z")  # skipped `a` filled from its default


# --- mixed unions (choices + types) ------------------------------------------


def test_union_literal_and_int_accepts_either():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1):
            seen["x"] = x

    _run(tasks, "go --x fast")
    assert seen["x"] == "fast"
    _run(tasks, "go --x 7")
    assert seen["x"] == 7 and type(seen["x"]) is int


def test_union_literal_and_int_rejects_neither():
    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1): ...

    with pytest.raises(ChainError, match=r"one of fast\|slow, or an integer"):
        _run(tasks, "go --x nope")


def test_union_literal_and_int_manifest_carries_both():
    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1): ...

    reg = Group("root")
    tasks(reg)
    spec = manifest.build_manifest(reg)["tree"]["tasks"]["go"]["params"][0]
    assert spec["choices"] == ["fast", "slow"]
    assert spec["types"] == ["int"]


def test_union_literal_value_coerces_to_int():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(x: Literal[5] | str = "a"):
            seen["x"] = x

    _run(tasks, "f --x 5")
    assert seen["x"] == 5 and type(seen["x"]) is int


def test_union_enum_member_binds():
    seen = {}

    def tasks(reg):
        @reg.task
        def paint(c: Colour | int = 0):
            seen["c"] = c

    _run(tasks, "paint --c red")
    assert seen["c"] is Colour.RED


def test_union_custom_type_binds_and_is_not_rejected():
    identifier = "550e8400-e29b-41d4-a716-446655440000"
    seen = {}

    def tasks(reg):
        @reg.task
        def rec(id: uuid.UUID | int = 0):
            seen["id"] = id

    _run(tasks, f"rec --id {identifier}")
    assert seen["id"] == uuid.UUID(identifier)
