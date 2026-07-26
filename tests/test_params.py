"""Union types, one-or-many values, and dynamic completion (`suggest`)."""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

import pytest

from footman import manifest
from footman._complete import complete
from footman.coerce import peel
from footman.executor import EX_USAGE, run_chain
from footman.params import (
    Arg,
    Exists,
    Forward,
    IsDir,
    IsFile,
    Many,
    NoSplit,
    forward,
    nosplit,
    suggest,
)
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

    run(tasks, "go --x=5")
    assert seen["x"] == 5 and type(seen["x"]) is int
    run(tasks, "go --x=hi")
    assert seen["x"] == "hi"


def test_union_specificity_int_before_float():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: int | float = 0):
            seen["x"] = x

    run(tasks, "go --x=3")
    assert type(seen["x"]) is int and seen["x"] == 3
    run(tasks, "go --x=3.5")
    assert type(seen["x"]) is float and seen["x"] == 3.5


def test_union_validation_error_lists_both():
    def tasks(reg):
        @reg.task
        def bench(n: int | float = 0): ...

    with pytest.raises(ChainError) as exc:
        run(tasks, "bench --n=abc")
    assert "expects an integer or a number" in str(exc.value)


# --- list[union] / Many ------------------------------------------------------


def test_list_union_option_repeatable():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(vals: list[str | int] | None = None):
            seen["vals"] = vals

    run(tasks, "go --vals=a --vals=3 --vals=b")
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


def test_many_single_token_is_still_a_list():
    # D14/F04: Many[T] is exactly list[T] — always a list. A single token does
    # NOT collapse to a scalar (the old doc claim was wrong).
    seen = {}

    def tasks(reg):
        @reg.task
        def build(targets: Many[str]):
            seen["t"] = targets

    run(tasks, "build web")
    assert seen["t"] == ["web"]


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

    run(tasks, "build --tags=a,b,c")
    assert seen["tags"] == ["a", "b", "c"]  # no marker needed


def test_list_also_accepts_repeat_and_mixes():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags=a,b --tags=c")
    assert seen["tags"] == ["a", "b", "c"]


def test_split_coerces_and_validates_each_part():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(nums: list[int] | None = None):
            seen["nums"] = nums

    run(tasks, "build --nums=1,2,3")
    assert seen["nums"] == [1, 2, 3]
    with pytest.raises(ChainError, match="expects an integer"):
        run(tasks, "build --nums=1,x,3")


def test_split_skips_empty_parts():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags=a,,b,")
    assert seen["tags"] == ["a", "b"]


def test_nosplit_keeps_comma_literal():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(names: Annotated[list[str], nosplit] | None = None):
            seen["names"] = names

    run(tasks, "build --names=a,b --names=c")
    assert seen["names"] == ["a,b", "c"]  # nosplit: only the repeated flag adds items


# --- forward marker ----------------------------------------------------------


def test_forward_marker_is_peeled():
    # Both spellings mark the parameter for forwarding; peel surfaces it.
    assert peel(Annotated[bool, forward]).forward is True
    assert peel(Forward[bool]).forward is True
    assert peel(bool).forward is False  # unmarked


def test_forward_alias_expands_to_annotated():
    # `Forward[T]` is exactly `Annotated[T, forward]`, like `Many[T]` is a list.
    assert Forward[bool] == Annotated[bool, forward]
    # A marker rides alongside the type without disturbing the peel of that type.
    peeled = peel(Forward[list[str]])
    assert peeled.multiple is True and peeled.forward is True


def test_bare_marker_aliases_peel_like_their_markers():
    # Terse aliases for the bare markers: generic `NoSplit[T]`, and the
    # Path-fixed `Exists`/`IsFile`/`IsDir` (no subscript needed).
    assert peel(NoSplit[list[str]]).nosplit is True
    assert peel(Exists).path_req == "exists"
    assert peel(IsFile).path_req == "file"
    assert peel(IsDir).path_req == "dir"


# --- dict[K, V] mappings -----------------------------------------------------


def test_dict_str_str():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=A=1 --env=B=2")
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
        run(tasks, "build --nums=a=x")


def test_dict_missing_equals_is_taught():
    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None): ...

    with pytest.raises(ChainError, match="expects KEY=VALUE"):
        run(tasks, "build --env=justkey")


def test_dict_value_may_contain_equals():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=URL=a=b")
    assert seen["env"] == {"URL": "a=b"}  # split on first '=' only


def test_dict_scalar_value_last_wins():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=X=1 --env=X=2")
    assert seen["env"] == {"X": "2"}


def test_dict_of_list_appends_on_repeated_key():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(label: dict[str, list[int]] | None = None):
            seen["label"] = label

    run(tasks, "build --label=ports=8080 --label=ports=8443 --label=mem=512")
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

    run(tasks, f"build --id={value}")
    assert seen["id"] == uuid.UUID(value)


def test_datetime_via_fromisoformat():
    seen = {}

    def tasks(reg):
        @reg.task
        def at(when: datetime.datetime | None = None):
            seen["when"] = when

    run(tasks, "at --when=2020-01-02T03:04:05")
    assert seen["when"] == datetime.datetime(2020, 1, 2, 3, 4, 5)


def test_custom_type_via_constructor():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(v: Version | None = None):
            seen["v"] = v

    run(tasks, "build --v=1.2.3")
    assert seen["v"] == Version("1.2.3")


def test_invalid_custom_value_fails_cleanly():
    def tasks(reg):
        @reg.task
        def build(id: uuid.UUID | None = None): ...

    results = run(tasks, "build --id=not-a-uuid")
    assert results[0].ok is False
    assert isinstance(results[0].error, ValueError)
    assert results[0].code == EX_USAGE  # a binding-time refusal, not a task failure


# --- dynamic completion (suggest) --------------------------------------------


def test_dynamic_choices_baked_but_completion_defers():
    from footman._complete import _DYNAMIC

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha", "beta"])]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["choices"] == ["alpha", "beta"]  # baked as the fallback snapshot
    assert spec["dynamic"] == {"strict": True}
    # Completion no longer serves the baked snapshot: it defers to a fresh
    # recompute (a subprocess, exercised end to end in test_complete),
    # returning a sentinel carrying the partial, the emission prefix ("" for
    # a positional; `--opt=` for an attached value), the param name, and the
    # task path.
    assert complete(tree, ["build", ""]) == [_DYNAMIC, "", "", "project", "build"]
    assert complete(tree, ["build", "al"]) == [
        _DYNAMIC,
        "al",
        "",
        "project",
        "build",
    ]


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


def test_soft_positional_accepts_task_name_collision():
    seen = {}

    def tasks(reg):
        @reg.task
        def lint(): ...

        @reg.task
        def checkout(
            branch: Annotated[str, suggest(lambda: ["main", "dev"], strict=False)],
        ):
            seen["b"] = branch

    run(tasks, "checkout lint")
    assert seen["b"] == "lint"  # a soft completer never hard-rejects a value


# --- required options, Any, bare collections ---------------------------------


def test_required_dict_option_binds_and_is_enforced():
    seen = {}

    def tasks(reg):
        @reg.task
        def env_(vars: dict[str, int | str]):  # trailing _ escapes the env marker
            seen["vars"] = vars

    # The escape underscore is stripped: `env_` is the `env` command, not `env-`.
    run(tasks, "env --vars=port=8080 --vars=name=web")
    assert seen["vars"] == {"port": 8080, "name": "web"}

    with pytest.raises(ChainError, match=r"missing required option\(s\): --vars"):
        run(tasks, "env")


def test_trailing_underscore_param_is_stripped_to_a_clean_flag():
    # `sync_` (escaping a name collision, like the provision task's own param)
    # renders as `--sync`/`--no-sync`, not `--sync-`, and binds back — matching
    # the tools bridge, which already strips the escape underscore.
    seen = {}

    def tasks(reg):
        @reg.task
        def go(sync_: bool = False):
            seen["sync"] = sync_

    run(tasks, "go --sync")
    assert seen["sync"] is True
    run(tasks, "go --no-sync")
    assert seen["sync"] is False


def test_required_bool_must_be_stated():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(prod: bool):
            seen["prod"] = prod

    run(tasks, "deploy --prod")
    assert seen["prod"] is True
    run(tasks, "deploy --no-prod")
    assert seen["prod"] is False

    with pytest.raises(ChainError, match=r"--prod \(or --no-prod\)"):
        run(tasks, "deploy")


def test_any_annotation_passes_through():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(payload: Any = ""):
            seen["p"] = payload

    run(tasks, "deploy --payload=hello")
    assert seen["p"] == "hello"


def test_bare_list_is_a_string_list():
    seen = {}

    def tasks(reg):
        @reg.task
        def release(tags: list):
            seen["tags"] = tags

    run(tasks, "release abc")
    assert seen["tags"] == ["abc"]  # not exploded into ['a','b','c']
    run(tasks, "release a b")
    assert seen["tags"] == ["a", "b"]


def test_bare_dict_is_a_required_mapping():
    seen = {}

    def tasks(reg):
        @reg.task
        def envs(vars: dict):
            seen["vars"] = vars

    run(tasks, "envs --vars=A=1")
    assert seen["vars"] == {"A": "1"}


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


def test_broken_strict_completer_fails_the_build():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: 1 / 0)]): ...

    with pytest.raises(manifest.CompleterError, match="ZeroDivisionError"):
        build_tree(tasks)


def test_broken_soft_completer_degrades_to_no_candidates():
    def tasks(reg):
        @reg.task
        def build(
            project: Annotated[str, suggest(lambda: 1 / 0, strict=False)],
        ): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["choices"] == []  # empty -> soft (validation allows anything)


# --- bool as a real token type (collections, dicts, unions) ------------------


def test_dict_bool_values_coerce():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(flags: dict[str, bool] | None = None):
            seen["flags"] = flags

    run(tasks, "deploy --flags=cache=false,retry=1 --flags=verbose=off")
    assert seen["flags"] == {"cache": False, "retry": True, "verbose": False}


def test_dict_bool_value_validated_eagerly():
    def tasks(reg):
        @reg.task
        def deploy(flags: dict[str, bool] | None = None): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="true or false"):
        split_chain(tree, ["deploy", "--flags=cache=maybe"])


def test_list_of_bool_is_a_repeatable_option_not_a_flag():
    seen = {}

    def tasks(reg):
        @reg.task
        def toggles(switches: list[bool] | None = None):
            seen["switches"] = switches

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["toggles"]["params"][0]
    assert spec["kind"] == "option" and spec.get("multiple") is True
    assert spec["types"] == ["bool"]
    run(tasks, "toggles --switches=true,false --switches=yes")
    assert seen["switches"] == [True, False, True]


def test_scalar_bool_is_still_a_flag():
    def tasks(reg):
        @reg.task
        def lint(fix: bool = False): ...

    _, tree = build_tree(tasks)
    assert tree["tasks"]["lint"]["params"][0]["kind"] == "flag"


def test_bool_in_union_coerces_tokens():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: bool | str = "d"):
            seen["x"] = x

    run(tasks, "go --x=true")
    assert seen["x"] is True
    run(tasks, "go --x=nope")
    assert seen["x"] == "nope"


def test_unicode_digit_lookalikes_are_taught_errors():
    # "²".isdigit() is true but int("²") raises; the guard must reject it
    # eagerly with a teaching message, not crash at binding time.
    def tasks(reg):
        @reg.task
        def add(a: int, b: int): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="an integer"):
        split_chain(tree, ["add", "²", "3"])


# --- Arg[T]: the optional trailing positional --------------------------------


def test_arg_fills_from_one_token(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

    run(tasks, "files src")
    assert seen["p"] == "src"


def test_arg_absent_runs_on_the_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

    run(tasks, "files")
    assert seen["p"] == "*"


def test_arg_is_greedy_and_never_peeks_at_task_names():
    # `files build` gives the token to files — deterministically, even
    # though a task named build exists. The grammar never guesses.
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

        @reg.task
        def build():
            seen["built"] = True

    run(tasks, "files build")
    assert seen["p"] == "build"
    assert "built" not in seen


def test_arg_plus_boundary_says_absent_next_task():
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

        @reg.task
        def build():
            seen["built"] = True

    run(tasks, "files + build")
    assert seen["p"] == "*"  # the boundary spelled "without it"
    assert seen["built"] is True


def test_arg_coerces_and_caps_at_one():
    seen = {}

    def tasks(reg):
        @reg.task
        def scale(n: Arg[int] = 1):
            seen["n"] = n

        @reg.task
        def other():
            seen["other"] = True

    run(tasks, "scale 5 other")
    assert seen["n"] == 5  # one token consumed, coerced
    assert seen["other"] is True  # the next word started a segment


def test_arg_needs_a_default():
    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str]):
            pass

    with pytest.raises(manifest.SpecError, match="needs a default"):
        build_tree(tasks)


def test_arg_must_trail_required_positionals():
    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*", where: str = ""): ...

    # `where` defaults, so it's an option — fine. The illegal shape is a
    # *positional* after the Arg:
    def bad(reg):
        @reg.task
        def files2(pattern: Arg[str] = "*", *rest: str): ...

    build_tree(tasks)  # option-after-Arg is legal
    with pytest.raises(manifest.SpecError, match="must come last"):
        build_tree(bad)


def test_arg_completion_offers_the_boundary():
    from footman._complete import complete

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"): ...

        @reg.task
        def build(): ...

    _, tree = build_tree(tasks)
    assert "+" in complete(tree, ["files", ""])  # the boundary documents itself
