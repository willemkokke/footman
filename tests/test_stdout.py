"""`Stdout[T]`: the return annotation that owns stdout.

A declaring task is a filter by declaration — no flag at any call site. The
return type decides the bytes (str verbatim, bytes raw, structured JSON);
everything that is not the document replays on stderr; only the addressed
task emits; `--json` keeps the envelope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated

import pytest

from footman import _manifest as manifest
from footman._coerce import emission_mode, emitted
from footman._executor import EX_USAGE
from footman.params import Stdout, stdin, stdout
from footman.registry import Group
from footman.testing import Runner


@dataclass
class Report:
    branch: str
    dirty: bool = False


def invoke(build, line, **kw):
    reg = Group("root")
    build(reg)
    return Runner().invoke(line, tasks=reg, **kw)


# --- the annotation -----------------------------------------------------------


def test_both_optional_spellings_read_identically():
    house, _ = emitted(Stdout[dict | None])
    outer, _ = emitted(Stdout[dict] | None)
    plain, _ = emitted(Annotated[dict, stdout])
    assert house and outer and plain
    none_at_all, _ = emitted(dict)
    assert not none_at_all


def test_the_mode_follows_the_inner_type():
    assert emission_mode(emitted(Stdout[str])[1]) == "text"
    assert emission_mode(emitted(Stdout[bytes])[1]) == "bytes"
    assert emission_mode(emitted(Stdout[dict | None])[1]) == "json"
    assert emission_mode(emitted(Stdout[int])[1]) == "json"


# --- emission -----------------------------------------------------------------


def test_a_dict_document_lands_on_stdout_compact():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict]:
            return {"branch": "main", "dirty": False}

    result = invoke(tasks, "status")
    assert result.ok
    assert json.loads(result.stdout) == {"branch": "main", "dirty": False}
    assert result.stdout.endswith("\n")
    assert result.stdout.count("\n") == 1  # compact into a pipe


def test_a_str_document_is_verbatim_not_json_quoted():
    def tasks(reg):
        @reg.task
        def render() -> Stdout[str]:
            return "line one\nline two"

    result = invoke(tasks, "render")
    assert result.stdout == "line one\nline two\n"  # no quotes, no escapes


def test_a_dataclass_document_serialises():
    def tasks(reg):
        @reg.task
        def report() -> Stdout[Report]:
            return Report(branch="main")

    result = invoke(tasks, "report")
    assert json.loads(result.stdout) == {"branch": "main", "dirty": False}


def test_stdout_int_is_the_document_not_the_exit_code():
    def tasks(reg):
        @reg.task
        def wordcount(text: Annotated[str, stdin] = "") -> Stdout[int]:
            return len(text.split())

    result = invoke(tasks, "wordcount", stdin="three words here")
    assert result.exit_code == 0
    assert result.stdout.strip() == "3"


def test_a_bare_int_return_stays_the_exit_code():
    def tasks(reg):
        @reg.task
        def failing() -> int:
            return 3

    result = invoke(tasks, "failing")
    assert result.exit_code == 3


def test_none_means_empty_stdout_exit_zero():
    def tasks(reg):
        @reg.task
        def maybe() -> Stdout[dict | None]:
            return None

    result = invoke(tasks, "maybe")
    assert result.ok and result.stdout == ""


def test_prints_replay_on_stderr_not_stdout():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict]:
            print("working...")
            return {"ok": True}

    result = invoke(tasks, "status")
    assert json.loads(result.stdout) == {"ok": True}
    assert "working..." in result.stderr


def test_a_failed_task_emits_nothing():
    def tasks(reg):
        @reg.task
        def broken() -> Stdout[dict]:
            raise RuntimeError("boom")

    result = invoke(tasks, "broken")
    assert result.exit_code == 1 and result.stdout == ""


# --- addressing ---------------------------------------------------------------


def test_only_the_addressed_task_emits():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict]:
            return {"from": "dep"}

        @reg.task(pre=[status])
        def wrap():
            print("wrapping")

    result = invoke(tasks, "wrap")
    assert result.ok
    # wrap declares nothing, so this is not a document run: its prints keep
    # today's stdout. The *dependency's* document is suppressed, not printed.
    assert "wrapping" in result.stdout
    assert "dep" not in result.stdout


def test_two_declaring_tasks_in_a_chain_refuse():
    def tasks(reg):
        @reg.task
        def a() -> Stdout[dict]:
            return {}

        @reg.task
        def b() -> Stdout[dict]:
            return {}

    result = invoke(tasks, "a b")
    assert result.exit_code == EX_USAGE
    assert "whose document" in result.stderr


def test_json_wins_and_keeps_the_envelope():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict]:
            return {"branch": "main"}

    result = invoke(tasks, "--json status")
    payload = json.loads(result.stdout)
    assert payload["results"][0]["returned"] == {"branch": "main"}


def test_a_body_call_returns_the_value():
    seen = {}

    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict]:
            return {"branch": "main"}

        @reg.task
        def wrap():
            seen["value"] = status()

    result = invoke(tasks, "wrap")
    assert result.ok and seen["value"] == {"branch": "main"}
    assert result.stdout == ""  # wrap was addressed; it declares nothing


# --- declaration errors -------------------------------------------------------


def test_interactive_and_stdout_cannot_both_hold():
    reg = Group("root")

    @reg.task(interactive=True)
    def wizard() -> Stdout[dict]:
        return {}

    with pytest.raises(manifest.SpecError, match="interactive"):
        manifest.build_manifest(reg)


def test_the_manifest_marks_an_emitting_task():
    reg = Group("root")

    @reg.task
    def status() -> Stdout[dict]:
        return {}

    tree = manifest.build_manifest(reg)["tree"]
    assert tree["tasks"]["status"]["emits"] is True
