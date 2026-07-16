"""Annotation normalization and value coercion.

The manifest (introspection), the splitter (validation), and the executor
(binding) all reason about a parameter's type through this one module, so a
parameter's CLI shape is derived in exactly one place.

A parameter is normalized by `peel` into `(multiple, element, completer)`
and its scalar *element* is described as ordered "type tags"
(`bool`/`int`/`float`/`path`/`str`) or as choices (`Literal`/`Enum`).
Coercion tries the tags in **specificity order** — the most restrictive parser
first, `str` last as the universal fallback — so `str | int` turns `"5"`
into `5` and `"x"` into `"x"`.
"""

from __future__ import annotations

import datetime as _datetime
import enum
import types
import typing
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Annotated, Any

from footman.params import nosplit as _NOSPLIT
from footman.params import suggest

_TAG_ORDER = {"bool": 0, "int": 1, "float": 2, "path": 3, "str": 4}

# The tokens a non-flag `bool` accepts (a scalar `bool` is a --flag and never
# parses a token; these cover bool inside collections, dict values, and unions).
_BOOL_TOKENS = {
    "true": True,
    "1": True,
    "yes": True,
    "on": True,
    "false": False,
    "0": False,
    "no": False,
    "off": False,
}


def _tag_of(t: Any) -> str | None:
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t is float:
        return "float"
    if isinstance(t, type) and issubclass(t, PurePath):
        return "path"
    if t is str:
        return "str"
    return None


def _is_union(ann: Any) -> bool:
    return typing.get_origin(ann) in (typing.Union, getattr(types, "UnionType", ()))


def _strip_none(members: list[Any]) -> list[Any]:
    return [m for m in members if m is not type(None)]


def _union_of(parts: list[Any]) -> Any:
    parts = list(dict.fromkeys(parts))
    union = parts[0]
    for part in parts[1:]:
        union = union | part
    return union


@dataclass
class Peeled:
    multiple: bool  # a list-valued parameter?
    element: Any  # scalar type / Union (or, for a mapping, the value type)
    completer: suggest | None
    nosplit: bool = False  # opt OUT of comma-splitting (collections split by default)
    mapping: bool = False  # a dict[K, V] parameter?
    key: Any = None  # mapping key type
    value_multiple: bool = False  # mapping value is a list (dict[K, list[E]])


def peel(ann: Any) -> Peeled:
    """Normalize a parameter annotation into (multiple, element, completer)."""
    completer: suggest | None = None
    is_nosplit = False

    # Strip Annotated and Optional wrappers in any order/nesting, e.g. both
    # `Annotated[list[X], nosplit] | None` and `Annotated[list[X] | None, nosplit]`.
    changed = True
    while changed:
        changed = False
        if typing.get_origin(ann) is Annotated:
            base, *meta = typing.get_args(ann)
            for mark in meta:
                if isinstance(mark, suggest):
                    completer = mark
                elif mark is _NOSPLIT:
                    is_nosplit = True
                elif callable(mark) and not isinstance(mark, type):
                    completer = suggest(mark)  # a bare callable == suggest(fn)
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True

    if typing.get_origin(ann) is dict:  # dict[K, V]
        kv = typing.get_args(ann)
        key_type = kv[0] if kv else str
        value_type = kv[1] if len(kv) > 1 else str
        value = peel(value_type)  # recurse: value may be scalar / union / list
        return Peeled(
            False,
            value.element,
            completer,
            is_nosplit,
            mapping=True,
            key=key_type,
            value_multiple=value.multiple,
        )

    if typing.get_origin(ann) is list:  # list[X] / Many[X]
        element = (typing.get_args(ann) or (str,))[0]
        return Peeled(True, element, completer, is_nosplit)

    if _is_union(ann):
        members = _strip_none(list(typing.get_args(ann)))
        lists = [m for m in members if typing.get_origin(m) is list]
        if lists:  # list[X] | scalar... -> a list of the merged element types
            parts: list[Any] = []
            for lm in lists:
                parts += list(typing.get_args(lm)) or [str]
            parts += [m for m in members if typing.get_origin(m) is not list]
            return Peeled(True, _union_of(parts), completer, is_nosplit)
        return Peeled(False, ann, completer, is_nosplit)  # scalar union

    return Peeled(False, ann, completer, is_nosplit)  # plain scalar


def is_flag(element: Any) -> bool:
    return element is bool


def sort_tags(tags: list[str]) -> list[str]:
    return sorted(dict.fromkeys(tags), key=lambda t: _TAG_ORDER.get(t, 99))


def element_tags(element: Any) -> list[str]:
    """Scalar coercion tags (specificity-sorted); empty for choice/unknown types."""
    if _is_union(element):
        tags = [
            t for m in _strip_none(list(typing.get_args(element))) if (t := _tag_of(m))
        ]
    else:
        tag = _tag_of(element)
        tags = [tag] if tag else []
    return sort_tags(tags)


def element_choices(
    element: Any,
) -> tuple[list[str] | None, type[enum.Enum] | None, tuple | None]:
    """(choices as strings, Enum class, Literal values) for a choice element."""
    if typing.get_origin(element) is typing.Literal:
        values = typing.get_args(element)
        return [str(v) for v in values], None, values
    if isinstance(element, type) and issubclass(element, enum.Enum):
        return [str(m.value) for m in element], element, None
    return None, None, None


def coerce_scalar(value: str, tags: list[str]) -> tuple[bool, Any]:
    """Try to coerce *value* to one of *tags* in specificity order."""
    for tag in sort_tags(tags):
        if tag == "bool":
            if value.lower() in _BOOL_TOKENS:
                return True, _BOOL_TOKENS[value.lower()]
        elif tag == "int":
            # `isascii` guards the gap between `str.isdigit` and `int()`:
            # "²".isdigit() is true but int("²") raises.
            digits = value[1:] if value[:1] in "+-" else value
            if digits.isdigit() and digits.isascii():
                return True, int(value)
        elif tag == "float":
            try:
                return True, float(value)
            except ValueError:
                pass
        elif tag == "path":
            return True, Path(value)
        elif tag == "str":
            return True, value
    return False, None


def coerce_one(value: str, element: Any) -> Any:
    """Coerce a single token to its annotated element type (best effort)."""
    _, enum_cls, literal = element_choices(element)
    if enum_cls is not None:
        for member in enum_cls:
            if str(member.value) == value or member.name == value:
                return member
        return enum_cls(value)
    if literal is not None:
        for lit in literal:
            if str(lit) == value:
                return lit
        return value
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        return out if ok else value
    return coerce_custom(value, element)


def coerce_custom(value: str, element: Any) -> Any:
    """Coerce to a type footman doesn't special-case, via its constructor.

    Covers `UUID`, `Decimal`, and any user type whose constructor accepts a
    string; `datetime`/`date` use `fromisoformat`. Validated here at
    execution time (the splitter only ever sees strings), and raises
    `ValueError` on a bad value so footman can report it cleanly.
    """
    if not isinstance(element, type):
        return value
    try:
        if issubclass(element, _datetime.datetime):
            return element.fromisoformat(value)
        if issubclass(element, _datetime.date):
            return element.fromisoformat(value)
        return element(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{value!r} is not a valid {element.__name__}") from exc
