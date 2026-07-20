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


# --- the palette --------------------------------------------------------------
# One visual language for the whole CLI: bold for names and headers, dim for
# mechanics and secondary text, cyan for footman's own numbers and accents,
# green/red for verdicts. Every helper is a no-op when *on* is False, and
# every surface gates *on* by its own stream's tty-ness — piped output stays
# byte-clean.


def wants_color(stream: Any, no_color: bool = False) -> bool:
    try:
        tty = bool(stream.isatty())
    except Exception:
        tty = False
    import os as _os

    return (
        tty
        and not no_color
        and "NO_COLOR" not in _os.environ
        and _os.environ.get("TERM") != "dumb"
    )


def bold(text: str, on: bool) -> str:
    return f"\033[1m{text}\033[0m" if on else text


def dim(text: str, on: bool) -> str:
    return f"\033[2m{text}\033[0m" if on else text


def cyan(text: str, on: bool) -> str:
    return f"\033[36m{text}\033[0m" if on else text


def bold_cyan(text: str, on: bool) -> str:
    return f"\033[1;36m{text}\033[0m" if on else text


def red(text: str, on: bool) -> str:
    return f"\033[31m{text}\033[0m" if on else text


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
    doc, mechanics = param_detail_parts(p)
    return "; ".join(bit for bit in (doc, mechanics) if bit)


def param_detail_parts(p: dict) -> tuple[str, str]:
    """(author's doc, the mechanical suffix) — split so help can dim the
    mechanics under the author's words."""
    return p.get("doc", ""), _mechanics(p)


def _mechanics(p: dict) -> str:
    bits: list[str] = []
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


# CLI lines (usage, examples) are token lists — (kind, text) — so every
# renderer paints the same structure: `prog` bold, `group` bold cyan (as in
# the tree), `task` bold, `req`/`value` cyan, `opt` dim, `flag` plain.
_CLI_PAINT = {
    "prog": bold,
    "group": bold_cyan,
    "task": bold,
    "req": cyan,
    "value": cyan,
    "opt": dim,
    "flag": lambda text, on: text,
}


def paint_cli(parts: list[tuple[str, str]], on: bool) -> str:
    """The one way to print a command line, syntax-lit by token kind."""
    return " ".join(_CLI_PAINT.get(kind, bold)(text, on) for kind, text in parts)


def invocation_parts(prog: str, path: list[str]) -> list[tuple[str, str]]:
    """`prog group… task` as tokens — the head of every usage and example."""
    parts: list[tuple[str, str]] = [("prog", prog)]
    parts += [("group", name) for name in path[:-1]]
    if path:
        parts.append(("task", path[-1]))
    return parts


def usage_parts(prog: str, path: list[str], task: dict) -> list[tuple[str, str]]:
    parts = invocation_parts(prog, path)
    for p in task["params"]:
        fragment = usage_fragment(p)
        if fragment:
            kind = "opt" if fragment.startswith("[") else "req"
            parts.append((kind, fragment))
    return parts


def example_parts(path: list[str], task: dict, prog: str) -> list[tuple[str, str]]:
    """A realistic invocation synthesised straight from the signature — required
    positionals and options with sample values, plus one representative flag.

    Derived, never written, so it can't drift from the task's actual parameters.
    Optional options are skipped as noise; the shape teaches the invocation.
    """
    parts = invocation_parts(prog, path)
    flag_shown = False
    for p in task["params"]:
        kind = p["kind"]
        if kind in ("argument", "variadic"):
            parts.append(("value", sample_value(p)))
        elif kind == "option" and p.get("required"):
            parts.append(("flag", f"--{p['name']}"))
            parts.append(("value", sample_value(p)))
        elif kind == "flag" and (p.get("required") or not flag_shown):
            parts.append(("flag", f"--{p['name']}"))
            flag_shown = True
    return parts


def example(path: list[str], task: dict, prog: str) -> str:
    """The example invocation as plain text (the markdown exporter's form)."""
    return " ".join(text for _, text in example_parts(path, task, prog))


def task_line(task: dict) -> str:
    """A task's one-line description, plus how it ends when that's notable:
    availability if disabled, the Ctrl-C note if it runs until stopped."""
    notes = []
    if task.get("infinite"):
        notes.append("(runs until Ctrl-C)")
    if task.get("disabled"):
        notes.append(f"(unavailable: {task['disabled']})")
    if not notes:
        return task["help"]
    return f"{task['help']}  {' '.join(notes)}".strip()


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
