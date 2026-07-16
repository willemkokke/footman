"""Union types, one-or-many values, and dynamic completion (`suggest`)."""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

import pytest

from footman import manifest
from footman._complete import complete
from footman.executor import run_chain
from footman.params import Many, nosplit, suggest
from footman.registry import Group
from footman.split import ChainError, split_chain

# A module-level completer so `eval_str` can resolve it from a tasks file that
# uses `from __future__ import annotations` (real completers live at module top).
_DEDUP_CALLS: list[int] = []


def _dedup_projects() -> list[str]:
    _DEDUP_CALLS.append(1)
    return ["a", "b"]


class Version:
    """A user type whose constructor takes a string."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and other.text == self.text


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


# --- list | scalar unions collapse to a plain list ---------------------------


def test_list_or_scalar_union_is_always_a_list():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(x: list[str] | str):
            seen["x"] = x

    run(tasks, "build only")
    assert seen["x"] == ["only"]  # always a list (no scalar-collapse)
    run(tasks, "build a b")
    assert seen["x"] == ["a", "b"]


# --- comma-splitting: on by default for collections, `nosplit` opts out ------


def test_list_splits_on_comma_by_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags a,b,c")
    assert seen["tags"] == ["a", "b", "c"]  # no marker needed


def test_list_also_accepts_repeat_and_mixes():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags a,b --tags c")
    assert seen["tags"] == ["a", "b", "c"]


def test_split_coerces_and_validates_each_part():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(nums: list[int] | None = None):
            seen["nums"] = nums

    run(tasks, "build --nums 1,2,3")
    assert seen["nums"] == [1, 2, 3]
    with pytest.raises(ChainError, match="expects an integer"):
        run(tasks, "build --nums 1,x,3")


def test_split_skips_empty_parts():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags a,,b,")
    assert seen["tags"] == ["a", "b"]


def test_nosplit_keeps_comma_literal():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(names: Annotated[list[str], nosplit] | None = None):
            seen["names"] = names

    run(tasks, "build --names a,b --names c")
    assert seen["names"] == ["a,b", "c"]  # nosplit: only the repeated flag adds items


# --- dict[K, V] mappings -----------------------------------------------------


def test_dict_str_str():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env A=1 --env B=2")
    assert seen["env"] == {"A": "1", "B": "2"}


def test_dict_typed_value_union_splits_by_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(opt: dict[str, int | str] | None = None):
            seen["opt"] = opt

    run(tasks, "build --opt=x=1,bla=haha")
    assert seen["opt"] == {"x": 1, "bla": "haha"}  # 1 -> int, haha -> str


def test_dict_value_type_validated():
    def tasks(reg):
        @reg.task
        def build(nums: dict[str, int] | None = None): ...

    with pytest.raises(ChainError, match="value expects an integer"):
        run(tasks, "build --nums a=x")


def test_dict_missing_equals_is_taught():
    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None): ...

    with pytest.raises(ChainError, match="expects KEY=VALUE"):
        run(tasks, "build --env justkey")


def test_dict_value_may_contain_equals():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env URL=a=b")
    assert seen["env"] == {"URL": "a=b"}  # split on first '=' only


def test_dict_scalar_value_last_wins():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env X=1 --env X=2")
    assert seen["env"] == {"X": "2"}


def test_dict_of_list_appends_on_repeated_key():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(label: dict[str, list[int]] | None = None):
            seen["label"] = label

    run(tasks, "build --label ports=8080 --label ports=8443 --label mem=512")
    assert seen["label"] == {"ports": [8080, 8443], "mem": [512]}


def test_dict_manifest_spec():
    def tasks(reg):
        @reg.task
        def build(nums: dict[str, int] | None = None): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["mapping"] is True
    assert "nosplit" not in spec  # collections split by default
    assert spec["value_types"] == ["int"]


def test_nosplit_manifest_spec():
    def tasks(reg):
        @reg.task
        def build(env: Annotated[dict[str, str], nosplit] | None = None): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["mapping"] is True
    assert spec["nosplit"] is True


# --- custom / extended scalar types (coerced via their constructor) ----------


def test_uuid_via_constructor():
    seen = {}
    value = "12345678-1234-5678-1234-567812345678"

    def tasks(reg):
        @reg.task
        def build(id: uuid.UUID | None = None):
            seen["id"] = id

    run(tasks, f"build --id {value}")
    assert seen["id"] == uuid.UUID(value)


def test_datetime_via_fromisoformat():
    seen = {}

    def tasks(reg):
        @reg.task
        def at(when: datetime.datetime | None = None):
            seen["when"] = when

    run(tasks, "at --when 2020-01-02T03:04:05")
    assert seen["when"] == datetime.datetime(2020, 1, 2, 3, 4, 5)


def test_custom_type_via_constructor():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(v: Version | None = None):
            seen["v"] = v

    run(tasks, "build --v 1.2.3")
    assert seen["v"] == Version("1.2.3")


def test_invalid_custom_value_fails_cleanly():
    def tasks(reg):
        @reg.task
        def build(id: uuid.UUID | None = None): ...

    results = run(tasks, "build --id not-a-uuid")
    assert results[0].ok is False
    assert isinstance(results[0].error, ValueError)


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
