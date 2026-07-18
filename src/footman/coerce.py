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

from footman.params import _PathRequirement, between, check, env, suggest
from footman.params import nosplit as _NOSPLIT

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


def union_members(element: Any) -> list[Any]:
    """Members of a union (None stripped), or `[element]` for a non-union."""
    if _is_union(element):
        return _strip_none(list(typing.get_args(element)))
    return [element]


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
    path_req: str | None = None  # exists / file / dir requirement on a Path
    bounds: tuple[float | None, float | None] | None = None  # inclusive lo/hi
    env: str | None = None  # environment-variable fallback
    checks: tuple[Any, ...] = ()  # post-coercion validators (check(fn))


def peel(ann: Any) -> Peeled:
    """Normalize a parameter annotation into (multiple, element, completer)."""
    completer: suggest | None = None
    is_nosplit = False
    path_req: str | None = None
    bounds: tuple[float | None, float | None] | None = None
    env_var: str | None = None
    checks: tuple[Any, ...] = ()

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
                elif isinstance(mark, _PathRequirement):
                    path_req = mark.kind
                elif isinstance(mark, between):
                    bounds = (mark.lo, mark.hi)
                elif isinstance(mark, range):
                    # A bare range: Python's half-open semantics, ints only.
                    bounds = (mark.start, mark.stop - 1)
                elif isinstance(mark, env):
                    env_var = mark.var
                elif isinstance(mark, check):
                    checks = (*checks, mark.fn)
                elif callable(mark) and not isinstance(mark, type):
                    completer = suggest(mark)  # a bare callable == suggest(fn)
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True

    markers = {
        "path_req": path_req,
        "bounds": bounds,
        "env": env_var,
        "checks": checks,
    }

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
            **markers,
        )

    if typing.get_origin(ann) is list:  # list[X] / Many[X]
        element = (typing.get_args(ann) or (str,))[0]
        return Peeled(True, element, completer, is_nosplit, **markers)

    if _is_union(ann):
        members = _strip_none(list(typing.get_args(ann)))
        lists = [m for m in members if typing.get_origin(m) is list]
        if lists:  # list[X] | scalar... -> a list of the merged element types
            parts: list[Any] = []
            for lm in lists:
                parts += list(typing.get_args(lm)) or [str]
            parts += [m for m in members if typing.get_origin(m) is not list]
            return Peeled(True, _union_of(parts), completer, is_nosplit, **markers)
        return Peeled(False, ann, completer, is_nosplit, **markers)  # scalar union

    return Peeled(False, ann, completer, is_nosplit, **markers)  # plain scalar


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


_TYPE_PHRASE = {
    "bool": "true or false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


def type_phrase(tags: list[str]) -> str:
    """A human phrase for a list of type tags: `['int']` -> "an integer"."""
    return " or ".join(str(_TYPE_PHRASE.get(t, t)) for t in tags)


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


def all_choices(element: Any) -> list[str] | None:
    """Choice strings gathered across a union's Literal/Enum members (or a
    scalar Literal/Enum); `None` if no member contributes choices."""
    out: list[str] = []
    for member in union_members(element):
        member_choices, _, _ = element_choices(member)
        if member_choices:
            out.extend(member_choices)
    return out or None


def eagerly_checkable(element: Any) -> bool:
    """Whether every union member is taggable (bool/int/float/path/str) or a
    Literal/Enum — so the splitter can accept/reject a value up front. A member
    like `UUID` or `Any` is not eagerly checkable; only binding can coerce it."""
    for member in union_members(element):
        if _tag_of(member) is not None:
            continue
        member_choices, _, _ = element_choices(member)
        if member_choices is not None:
            continue
        return False
    return True


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
    if _is_union(element):
        return _coerce_union(value, element)
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        return out if ok else value
    return coerce_custom(value, element)


def _coerce_union(value: str, element: Any) -> Any:
    """Coerce a token to the best-matching member of a union (best effort).

    Order: an exact Literal/Enum member (so `Literal[5] | str` yields the int
    5, not "5"), then scalar tags in specificity order, then any custom-type
    member's constructor (so `UUID | int` binds a real UUID); falls back to the
    raw string when nothing matches.
    """
    members = union_members(element)
    for member in members:
        _, enum_cls, literal = element_choices(member)
        if enum_cls is not None:
            for m in enum_cls:
                if str(m.value) == value or m.name == value:
                    return m
        elif literal is not None:
            for lit in literal:
                if str(lit) == value:
                    return lit
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        if ok:
            return out
    for member in members:
        if (
            isinstance(member, type)
            and _tag_of(member) is None
            and not issubclass(member, enum.Enum)
        ):
            try:
                return coerce_custom(value, member)
            except ValueError:
                continue
    return value


def coerce_token(value: str, element: Any) -> Any:
    """Strict `coerce_one` for a token the splitter never validated — an env
    fallback or a `--` passthrough value.

    Raises `ValueError` when a purely tag-typed element cannot parse the token
    (e.g. `JOBS=abc` for an `int`), rather than passing the raw string through
    the way `coerce_one` does for CLI tokens the splitter already validated.
    Choice and
    custom-type membership are left to `coerce_one` (and the caller's own
    choices check), so union values keep working.
    """
    if element_tags(element) and all_choices(element) is None:
        tags = element_tags(element)
        ok, out = coerce_scalar(value, tags)
        if not ok:
            raise ValueError(f"expects {type_phrase(tags)} (got {value!r})")
        return out
    return coerce_one(value, element)


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
