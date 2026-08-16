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

from footman import _manifest
from footman._coerce import emission_mode, emitted
from footman._executor import EX_USAGE
from footman.params import Secret, Stdout, stdin, stdout
from footman.registry import Group
from footman.testing import Runner


@dataclass
class Report:
    branch: str
    dirty: bool = False


@dataclass
class Credentials:
    """Module level, or `eval_str` cannot resolve `Stdout[Credentials]`."""

    user: str
    token: Secret


def invoke(build, line, **kw):
    reg = Group("root")
    build(reg)
    return Runner().invoke(line, tasks=reg, **kw)


# --- the annotation -----------------------------------------------------------


def test_both_optional_spellings_read_identically():
    # The house spelling is the test; mypy cannot type-apply a runtime
    # union (`dict | None`) to the Annotated alias in expression position.
    house, _ = emitted(Stdout[dict[str, object] | None])  # type: ignore[misc]
    outer, _ = emitted(Stdout[dict[str, object]] | None)
    plain, _ = emitted(Annotated[dict, stdout])
    assert house and outer and plain
    none_at_all, _ = emitted(dict)
    assert not none_at_all


def test_the_mode_follows_the_inner_type():
    assert emission_mode(emitted(Stdout[str])[1]) == "text"
    assert emission_mode(emitted(Stdout[bytes])[1]) == "bytes"
    # In assignment position mypy reads this as a type alias, so the
    # expression-position union limitation from above does not bite here.
    optional_dict = Stdout[dict[str, object] | None]
    assert emission_mode(emitted(optional_dict)[1]) == "json"
    assert emission_mode(emitted(Stdout[int])[1]) == "json"


# --- emission -----------------------------------------------------------------


def test_a_dict_document_lands_on_stdout_compact():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict[str, object]]:
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
        def maybe() -> Stdout[dict[str, object] | None]:
            return None

    result = invoke(tasks, "maybe")
    assert result.ok and result.stdout == ""


def test_prints_replay_on_stderr_not_stdout():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict[str, bool]]:
            print("working...")
            return {"ok": True}

    result = invoke(tasks, "status")
    assert json.loads(result.stdout) == {"ok": True}
    assert "working..." in result.stderr


def test_a_failed_task_emits_nothing():
    def tasks(reg):
        @reg.task
        def broken() -> Stdout[dict[str, object]]:
            raise RuntimeError("boom")

    result = invoke(tasks, "broken")
    assert result.exit_code == 1 and result.stdout == ""


# --- addressing ---------------------------------------------------------------


def test_only_the_addressed_task_emits():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict[str, str]]:
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
        def a() -> Stdout[dict[str, object]]:
            return {}

        @reg.task
        def b() -> Stdout[dict[str, object]]:
            return {}

    result = invoke(tasks, "a b")
    assert result.exit_code == EX_USAGE
    assert "whose document" in result.stderr


def test_json_wins_and_keeps_the_envelope():
    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict[str, str]]:
            return {"branch": "main"}

    result = invoke(tasks, "--json status")
    payload = json.loads(result.stdout)
    assert payload["items"][0]["returned"] == {"branch": "main"}


def test_a_body_call_returns_the_value():
    seen = {}

    def tasks(reg):
        @reg.task
        def status() -> Stdout[dict[str, str]]:
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
    def wizard() -> Stdout[dict[str, object]]:
        return {}

    with pytest.raises(_manifest.SpecError, match="interactive"):
        _manifest.build_manifest(reg)


def test_a_secret_inside_a_document_redacts_on_both_surfaces():
    # `_emit_document` said in its own docstring that `Secret` "redacts
    # identically on both surfaces", and never called the walk that does it —
    # so a document printed the value while `--json` for the same task did
    # not. `Secret` is a `str` subclass, so it rides `json.dumps`' fast path
    # and the `default` hook it shares with `--json` never sees it.
    reg = Group("root")

    @reg.task
    def creds() -> Stdout[Credentials]:
        return Credentials(user="alice", token=Secret("hunter2"))

    document = Runner().invoke("creds", tasks=reg)
    assert document.ok, document.stderr
    assert "hunter2" not in document.stdout
    assert json.loads(document.stdout)["token"] == "***"

    envelope = Runner().invoke("--json creds", tasks=reg)
    assert "hunter2" not in envelope.stdout
    row = next(i for i in json.loads(envelope.stdout)["items"] if i.get("task"))
    assert row["returned"]["token"] == "***"


def test_the_manifest_marks_an_emitting_task():
    reg = Group("root")

    @reg.task
    def status() -> Stdout[dict[str, object]]:
        return {}

    tree = _manifest.build_manifest(reg)["tree"]
    assert tree["tasks"]["status"]["emits"] is True
