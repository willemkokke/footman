"""Annotation normalization and value coercion.

The manifest (introspection), the splitter (validation), and the executor
(binding) all reason about a parameter's type through this one module, so a
parameter's CLI shape is derived in exactly one place.

A parameter is normalized by :func:`peel` into ``(multiple, element, completer)``
and its scalar *element* is described as ordered "type tags"
(``int``/``float``/``path``/``str``) or as choices (``Literal``/``Enum``).
Coercion tries the tags in **specificity order** — the most restrictive parser
first, ``str`` last as the universal fallback — so ``str | int`` turns ``"5"``
into ``5`` and ``"x"`` into ``"x"``.
"""

from __future__ import annotations

import enum
import types
import typing
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Annotated, Any

from footman.params import suggest

_TAG_ORDER = {"int": 0, "float": 1, "path": 2, "str": 3}


def _tag_of(t: Any) -> str | None:
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
    multiple: bool | str  # False | True | "one_or_many"
    element: Any  # scalar type, or a Union of scalar types
    completer: suggest | None


def peel(ann: Any) -> Peeled:
    """Normalize a parameter annotation into (multiple, element, completer)."""
    completer: suggest | None = None

    if typing.get_origin(ann) is Annotated:
        base, *meta = typing.get_args(ann)
        for mark in meta:
            if isinstance(mark, suggest):
                completer = mark
            elif callable(mark) and not isinstance(mark, type):
                completer = suggest(mark)  # a bare callable == suggest(fn)
        ann = base

    if _is_union(ann):  # collapse Optional / X | None
        members = _strip_none(list(typing.get_args(ann)))
        if len(members) == 1:
            ann = members[0]

    if typing.get_origin(ann) is list:  # list[X] / Many[X]
        element = (typing.get_args(ann) or (str,))[0]
        return Peeled(True, element, completer)

    if _is_union(ann):
        members = _strip_none(list(typing.get_args(ann)))
        lists = [m for m in members if typing.get_origin(m) is list]
        if lists:  # list[X] | scalar... -> one-or-many
            parts: list[Any] = []
            for lm in lists:
                parts += list(typing.get_args(lm)) or [str]
            parts += [m for m in members if typing.get_origin(m) is not list]
            return Peeled("one_or_many", _union_of(parts), completer)
        return Peeled(False, ann, completer)  # scalar union

    return Peeled(False, ann, completer)  # plain scalar


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
        if tag == "int":
            digits = value[1:] if value[:1] in "+-" else value
            if digits.isdigit():
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
    if not tags:
        return value
    ok, out = coerce_scalar(value, tags)
    return out if ok else value
