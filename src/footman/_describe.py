"""Phrase manifest nodes for humans: labels, usage lines, examples.

The one home for turning a task's manifest entry into words, shared by the
help renderer (`_app`) and the markdown exporter (`markdown`) so the two can
never drift. Everything here is a pure function over manifest dicts — no
registry, no I/O.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import json
import uuid
from pathlib import PurePath
from typing import Any

TYPE_WORD = {
    "bool": "true/false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


def value_hint(p: dict) -> str:
    """The value placeholder shown for an option/argument in help output."""
    if p.get("mapping"):
        return "KEY=VALUE"
    choices = p.get("choices")
    if choices:
        return "{" + "|".join(choices) + "}"
    types = p.get("types")
    if types:
        return "|".join(t.upper() for t in types)
    return "VALUE"


def usage_fragment(p: dict) -> str:
    kind = p["kind"]
    required = p.get("required")
    if kind == "flag":
        return f"--{p['name']}" if required else f"[--{p['name']}]"
    if kind == "option":
        core = f"--{p['name']} {value_hint(p)}"
        if p.get("multiple") or p.get("mapping"):
            core += " ..."
        return core if required else f"[{core}]"
    if kind == "variadic":
        return f"[<{p['name']}> ...]"
    suffix = "..." if p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def param_label(p: dict) -> str:
    kind = p["kind"]
    if kind == "flag":
        return f"--{p['name']}"
    if kind == "option":
        return f"--{p['name']} {value_hint(p)}"
    suffix = "..." if kind == "variadic" or p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def param_detail(p: dict) -> str:
    bits: list[str] = []
    if p.get("doc"):  # the author's own words lead; mechanics follow
        bits.append(p["doc"])
    if p["kind"] == "flag":
        bits.append(f"flag (--no-{p['name']} to disable)")
    choices = p.get("choices")
    if choices:
        bits.append("one of " + "|".join(choices))
    elif p.get("types"):
        bits.append(" or ".join(TYPE_WORD.get(str(t), str(t)) for t in p["types"]))
    if p.get("mapping"):
        bits.append("KEY=VALUE pairs (repeat appends)")
    if p.get("multiple") or p.get("mapping"):
        bits.append("repeatable" if p.get("nosplit") else "repeatable/comma-split")
    if p["kind"] == "variadic":
        bits.append("extra arguments (also receives everything after --)")
    if p.get("required"):
        bits.append("required")
    return "; ".join(bits)


def sample_value(p: dict) -> str:
    """A realistic value for a param in a synthesised example: its first choice
    when it has one, else an `<name>` placeholder."""
    choices = p.get("choices")
    return choices[0] if choices else f"<{p['name']}>"


def example(path: list[str], task: dict, prog: str) -> str:
    """A realistic invocation synthesised straight from the signature — required
    positionals and options with sample values, plus one representative flag.

    Derived, never written, so it can't drift from the task's actual parameters.
    Optional options are skipped as noise; the shape teaches the invocation.
    """
    parts = [prog, *path]
    flag_shown = False
    for p in task["params"]:
        kind = p["kind"]
        if kind in ("argument", "variadic"):
            parts.append(sample_value(p))
        elif kind == "option" and p.get("required"):
            parts.append(f"--{p['name']} {sample_value(p)}")
        elif kind == "flag" and (p.get("required") or not flag_shown):
            parts.append(f"--{p['name']}")
            flag_shown = True
    return " ".join(parts)


def task_line(task: dict) -> str:
    """A task's one-line description, with its availability if disabled."""
    note = f"(unavailable: {task['disabled']})" if task.get("disabled") else ""
    return f"{task['help']}  {note}".strip() if note else task["help"]


def iter_tasks(node: dict, prefix: str = ""):
    for name, task in node["tasks"].items():
        yield f"{prefix}{name}", task_line(task)
    for name, sub in node["groups"].items():
        yield from iter_tasks(sub, f"{prefix}{name} ")


def iter_group_paths(node: dict, prefix: str = ""):
    for name, sub in node["groups"].items():
        yield f"{prefix}{name}"
        yield from iter_group_paths(sub, f"{prefix}{name} ")


def json_default(value: object) -> object:
    """JSON forms for the types footman coerces *in* — Path, Enum, datetime,
    UUID, Decimal, dataclasses, sets — so a task may return what it accepts.
    Anything else raises TypeError; the caller turns that into a
    `returned_error` note rather than a broken envelope."""
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)  # str, not float: Decimal exists to keep precision
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)  # deterministic order for golden tests
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")


def jsonable(value: Any) -> tuple[bool, Any]:
    """(True, encoded) when *value* survives the JSON coercion mirror —
    used to bake parameter defaults into the manifest; (False, None) when it
    doesn't, in which case the key is simply omitted."""
    try:
        return True, json.loads(json.dumps(value, default=json_default))
    except (TypeError, ValueError):
        return False, None
