"""The document binder: JSON on stdin into dataclasses, lists and dicts.

Structural rules under test: unknown keys are ignored, missing keys follow
the dataclass, recursion covers nested dataclasses / `list[T]` / `T | None`,
scalar leaves reuse the one coercion pipeline, and every refusal names the
JSON path. A dataclass parameter is boundary-only: no flag, no positional —
the pipe is its only source.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest

from footman import context, manifest
from footman._describe import param_detail, usage_fragment
from footman.executor import EX_USAGE, run_chain
from footman.params import stdin
from footman.registry import Group
from footman.split import ChainError, split_chain
from footman.testing import Runner


# Payload shapes live at module level, where `eval_str` can see them.
@dataclass
class ToolInput:
    file_path: str = ""
    command: str = ""


@dataclass
class Event:
    tool_name: str
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False
    cwd: Path | None = None


@dataclass
class Row:
    name: str
    score: float
    kind: Literal["unit", "functional"] = "unit"


@dataclass
class Stamped:
    when: datetime.datetime
    note: str | None = None


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


@pytest.fixture
def piped(monkeypatch):
    def _set(data: bytes | str | None):
        payload = data.encode() if isinstance(data, str) else data
        monkeypatch.setattr(context, "_stdin_payload", payload)

    _set(None)
    return _set


# --- the dataclass shape ------------------------------------------------------


def test_a_document_binds_a_dataclass(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]):
            seen["event"] = event

    piped(
        '{"tool_name": "Edit", "tool_input": {"file_path": "a.py"},'
        ' "prompt_id": "ignored-unknown-key"}'
    )
    run(tasks, "hook")
    event = seen["event"]
    assert event.tool_name == "Edit"
    assert event.tool_input.file_path == "a.py"  # nested, no dotted paths
    assert event.tool_input.command == ""  # nested default filled
    assert event.stop_hook_active is False  # missing key follows the default
    assert event.cwd is None


def test_leaves_coerce_like_cli_tokens(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]):
            seen["event"] = event

    piped('{"tool_name": "x", "cwd": "/tmp/build", "stop_hook_active": true}')
    run(tasks, "hook")
    assert seen["event"].cwd == Path("/tmp/build")
    assert seen["event"].stop_hook_active is True


def test_a_missing_defaultless_field_names_its_path(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped('{"stop_hook_active": false}')
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "'tool_name'" in str(results[0].error)
    assert "Event.tool_name" in str(results[0].error)


def test_a_wrong_type_names_the_json_path(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped('{"tool_name": "x", "tool_input": {"file_path": 3}}')
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "event.tool_input.file_path" in str(results[0].error)


def test_an_array_where_an_object_belongs_refuses(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped("[1, 2]")
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE and "expected an object" in str(results[0].error)


def test_datetime_and_null_leaves(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def stamp(s: Annotated[Stamped, stdin]):
            seen["s"] = s

    piped('{"when": "2026-07-26T12:00:00", "note": null}')
    run(tasks, "stamp")
    assert seen["s"].when == datetime.datetime(2026, 7, 26, 12, 0, 0)
    assert seen["s"].note is None


def test_a_bad_literal_choice_names_the_path(piped):
    def tasks(reg):
        @reg.task
        def rows(items: Annotated[list[Row], stdin] = ()):  # type: ignore[assignment]
            ...

    piped('[{"name": "a", "score": 1.5, "kind": "imaginary"}]')
    results = run(tasks, "rows")
    assert results[0].code == EX_USAGE
    assert "items[0].kind" in str(results[0].error)
    assert "unit|functional" in str(results[0].error)


def test_a_list_of_dataclasses_binds(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def rows(items: Annotated[list[Row], stdin] = ()):  # type: ignore[assignment]
            seen["items"] = items

    piped('[{"name": "a", "score": 1}, {"name": "b", "score": 2.5}]')
    run(tasks, "rows")
    assert [r.name for r in seen["items"]] == ["a", "b"]
    assert seen["items"][0].score == 1.0  # a JSON int fits a float field


# --- dict and list shapes -----------------------------------------------------


def test_an_unshaped_dict_passes_the_document_through(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def raw(doc: Annotated[dict[str, Any], stdin] = {}):
            seen["doc"] = doc

    piped('{"anything": [1, {"nested": true}]}')
    run(tasks, "raw")
    assert seen["doc"] == {"anything": [1, {"nested": True}]}


def test_typed_dict_values_recurse(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def scores(by_name: Annotated[dict[str, int], stdin] = {}):
            seen["by_name"] = by_name

    piped('{"a": 1, "b": 2}')
    run(tasks, "scores")
    assert seen["by_name"] == {"a": 1, "b": 2}


def test_a_json_array_binds_a_list_parameter(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def audit(names: Annotated[list[str], stdin] = ()):  # type: ignore[assignment]
            seen["names"] = names

    piped('["a", "b"]')
    run(tasks, "audit")
    assert seen["names"] == ["a", "b"]


def test_cli_still_beats_a_json_list(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def audit(names: Annotated[list[str], stdin] = ()):  # type: ignore[assignment]
            seen["names"] = names

    piped('["piped"]')
    run(tasks, "audit --names=explicit")
    assert seen["names"] == ["explicit"]


# --- boundary-only: no CLI surface --------------------------------------------


def test_a_dataclass_parameter_is_not_a_flag(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="--event"):
        split_chain(tree, ["hook", "--event", "x"])


def test_the_manifest_records_the_shape():
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["hook"]["params"][0]
    assert spec["kind"] == "stdin"
    assert spec["stdin"] == "json"
    assert spec["shape"] == "Event"
    assert usage_fragment(spec) == ""  # no token spelling in usage
    assert "reads stdin (JSON document → Event)" in param_detail(spec)


def test_required_document_without_a_pipe_teaches_the_fixture(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "payload.json" in str(results[0].error)


def test_a_local_dataclass_is_a_taught_spec_error():
    @dataclass
    class Local:
        x: int = 0

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Local, stdin] = None): ...  # type: ignore[assignment]

    with pytest.raises(manifest.SpecError, match="module-level"):
        build_tree(tasks)


# --- end to end ---------------------------------------------------------------


def test_runner_replays_a_fixture_payload():
    reg = Group("root")

    @reg.task
    def hook(event: Annotated[Event, stdin]):
        print("active" if event.stop_hook_active else "quiet")

    result = Runner().invoke(
        "hook", tasks=reg, stdin='{"tool_name": "Stop", "stop_hook_active": true}'
    )
    assert result.ok and "active" in result.stdout
