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
from typing import Annotated, Any, TypedDict

from footman.params import (
    PathRequirement,
    StdoutMarker,
    ask,
    between,
    check,
    doc,
    env,
    suggest,
)
from footman.params import _arg as _ARG
from footman.params import ask as _ask_marker
from footman.params import forward as _FORWARD
from footman.params import nosplit as _NOSPLIT
from footman.params import stdin as _stdin_marker
from footman.params import stdout as _STDOUT

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
    doc: str | None = None  # per-parameter help text (doc("..."))
    # `_ask_marker`, not `ask`: the field name shadows the class inside
    # this scope, so the plain spelling would annotate with the field.
    ask: _ask_marker | None = None  # prompt-if-missing marker (ask())
    forward: bool = False  # thread this value to dispatched tasks (forward)
    optional: bool = False  # Arg[T]: an optional trailing positional
    stdin: _stdin_marker | None = None  # bind from the boundary's stdin read
    as_tuple: bool = False  # `tuple[T, ...]`: same grammar as a list, tuple out


class _Markers(TypedDict):
    """The marker bundle `peel` collects and hands to `Peeled` — typed so the
    `**markers` unpack checks against the dataclass's own field types."""

    path_req: str | None
    bounds: tuple[float | None, float | None] | None
    env: str | None
    checks: tuple[Any, ...]
    doc: str | None
    ask: _ask_marker | None
    forward: bool
    optional: bool
    stdin: _stdin_marker | None


def peel(ann: Any) -> Peeled:
    """Normalize a parameter annotation into (multiple, element, completer)."""
    completer: suggest | None = None
    is_nosplit = False
    path_req: str | None = None
    bounds: tuple[float | None, float | None] | None = None
    env_var: str | None = None
    checks: tuple[Any, ...] = ()
    doc_text: str | None = None
    ask_marker: ask | None = None
    is_forward = False
    is_optional = False
    stdin_marker: _stdin_marker | None = None

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
                elif isinstance(mark, PathRequirement):
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
                elif isinstance(mark, doc):
                    doc_text = mark.text
                elif isinstance(mark, ask):
                    ask_marker = mark
                elif mark is _FORWARD:
                    is_forward = True
                elif mark is _ARG:
                    is_optional = True
                elif mark is _stdin_marker or isinstance(mark, _stdin_marker):
                    # The bare class and an instance both mark the parameter:
                    # `Annotated[str, stdin]` reads the whole stream,
                    # `stdin("field")` / `stdin(lines=True)` refine it.
                    stdin_marker = (
                        mark if isinstance(mark, _stdin_marker) else _stdin_marker()
                    )
                elif callable(mark) and not isinstance(mark, type):
                    # A bare callable used to mean `suggest(fn)`. One spelling
                    # per concept won: `suggest()` says what it does, and the
                    # guess quietly swallowed anything callable — a plugin's
                    # own marker became a mystery completer with no error
                    # either way. Refused rather than ignored, because this
                    # shape *did* work: silence would break it invisibly.
                    # Unknown metadata that is not callable stays ignored, so
                    # a plugin marker (the house pattern is a non-callable
                    # instance) still rides through untouched.
                    from footman._manifest import SpecError  # circular at import

                    name = getattr(mark, "__name__", type(mark).__name__)
                    raise SpecError(
                        f"Annotated[…, {name}]: a bare callable is not a "
                        f"marker — wrap it to say what it means: "
                        f"suggest({name})"
                    )
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True

    markers: _Markers = {
        "path_req": path_req,
        "bounds": bounds,
        "env": env_var,
        "checks": checks,
        "doc": doc_text,
        "ask": ask_marker,
        "forward": is_forward,
        "optional": is_optional,
        "stdin": stdin_marker,
    }

    if ann is dict or typing.get_origin(ann) is dict:  # dict[K, V] or bare dict
        kv = typing.get_args(ann)
        key_type = kv[0] if kv else str
        value_type = kv[1] if len(kv) > 1 else str
        value = peel(value_type)  # recurse: value may be scalar / union / list
        # A marker on the value type — dict[str, Annotated[int, between(1, 5)]]
        # — applies to each value; an outer marker on the whole dict wins if
        # both are present. (env stays outer-only; env() on a dict is a
        # SpecError.)
        return Peeled(
            False,
            value.element,
            completer,
            is_nosplit,
            mapping=True,
            key=key_type,
            value_multiple=value.multiple,
            path_req=path_req if path_req is not None else value.path_req,
            bounds=bounds if bounds is not None else value.bounds,
            env=env_var,
            checks=(*checks, *value.checks),
            doc=doc_text,
            stdin=stdin_marker,
        )

    if ann is list or typing.get_origin(ann) is list:  # list[X] / Many[X] / bare
        element = (typing.get_args(ann) or (str,))[0]
        return Peeled(True, element, completer, is_nosplit, **markers)

    # `tuple[T, ...]` is `list[T]`'s grammar exactly — one-or-many, comma or
    # repetition — and differs only in what the body is handed. Coercing it
    # to a list would hand back the container the annotation does not name,
    # which is the failure class 0.33.0's default inference removed.
    if typing.get_origin(ann) is tuple:
        args = typing.get_args(ann)
        if len(args) == 2 and args[1] is Ellipsis:
            return Peeled(
                True, args[0], completer, is_nosplit, as_tuple=True, **markers
            )

    if _is_union(ann):
        members = _strip_none(list(typing.get_args(ann)))
        lists = [m for m in members if m is list or typing.get_origin(m) is list]
        if lists:  # list[X] | scalar... -> a list of the merged element types
            parts: list[Any] = []
            for lm in lists:
                parts += list(typing.get_args(lm)) or [str]
            parts += [m for m in members if typing.get_origin(m) is not list]
            return Peeled(True, _union_of(parts), completer, is_nosplit, **markers)
        return Peeled(False, ann, completer, is_nosplit, **markers)  # scalar union

    return Peeled(False, ann, completer, is_nosplit, **markers)  # plain scalar


def emitted(ann: Any) -> tuple[bool, Any]:
    """Whether a *return* annotation carries the `stdout` marker, and the
    type inside it.

    `Stdout[dict | None]` and `Stdout[dict] | None` read identically —
    `Annotated` and `Optional` strip in any order and nesting, the same
    normalisation `peel` applies to parameters. The inner type decides the
    emission: `str` verbatim, `bytes` raw, anything else JSON.
    """
    found = False
    changed = True
    while changed:
        changed = False
        if typing.get_origin(ann) is Annotated:
            base, *meta = typing.get_args(ann)
            if any(m is _STDOUT or isinstance(m, StdoutMarker) for m in meta):
                found = True
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True
    return found, ann


def emission_mode(inner: Any) -> str:
    """How an emitted value reaches stdout: `text`, `bytes`, or `json`."""
    members = union_members(inner)
    if len(members) == 1:
        if members[0] is str:
            return "text"
        if members[0] is bytes:
            return "bytes"
    return "json"


@dataclass(frozen=True)
class Group:
    """A fixed-arity shape: what to build, from which positions.

    One reading covers four spellings, because `inspect.signature` answers
    identically for all of them — `tuple[X, Y]` from its subscript, a
    `NamedTuple` and a dataclass from their fields, a plain class from its
    `__init__`. They are one case, not four.
    """

    target: Any  # the callable that builds it (`tuple` builds from an iterable)
    names: tuple[str, ...] | None  # field names; None for a bare tuple
    types: tuple[Any, ...]  # the annotation at each position
    required: int  # positions that must be filled
    from_iterable: bool  # `tuple(values)` rather than `T(*values)`

    @property
    def total(self) -> int:
        return len(self.types)

    def build(self, values: list[Any]) -> Any:
        return self.target(values) if self.from_iterable else self.target(*values)

    def label(self) -> str:
        """How the arity reads in an error: `width,height`, or `2 values`.

        The named form is the whole argument for preferring `NamedTuple`:
        a plain tuple can only count, and counting errors read poorly.
        """
        if self.names:
            return ",".join(self.names)
        return f"{self.total} values"


def group_of(ann: Any) -> Group | None:
    """The fixed-arity shape *ann* names, or `None` if it is not one.

    A one-parameter constructor is deliberately not a group: it keeps
    today's `T(value)` behaviour, where the whole token reaches the type.
    That is what makes this non-breaking — only shapes that are a hard
    error today start grouping.
    """
    import inspect

    if typing.get_origin(ann) is tuple:
        args = typing.get_args(ann)
        if not args or (len(args) == 2 and args[1] is Ellipsis):
            return None  # variadic: a list's grammar, handled by `peel`
        return Group(tuple, None, args, len(args), from_iterable=True)

    if not isinstance(ann, type) or ann in (str, bytes) or issubclass(ann, enum.Enum):
        return None
    try:
        sig = inspect.signature(ann)
        hints = typing.get_type_hints(ann.__init__ if not _is_record(ann) else ann)
    except (TypeError, ValueError, NameError):
        return None

    names: list[str] = []
    types: list[Any] = []
    required = 0
    for param in sig.parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            return None  # no fixed arity to group by
        names.append(param.name)
        types.append(hints.get(param.name, str))
        if param.default is inspect.Parameter.empty:
            required += 1
    if len(names) < 2:
        return None  # one parameter keeps the whole token, as it does today
    return Group(ann, tuple(names), tuple(types), required, from_iterable=False)


def _is_record(ann: Any) -> bool:
    """A shape whose annotations live on the class, not on `__init__`."""
    import dataclasses

    return dataclasses.is_dataclass(ann) or hasattr(ann, "_fields") or False


def is_flag(element: Any) -> bool:
    return element is bool


# The basic types a default may stand in for. Ordered because `bool` is a
# subclass of `int` in Python — and excluded outright, since a bool default
# is a flag the splitter resolves before coercion ever runs.
_INFERRED_FROM_DEFAULT = (int, float, str, Path)


def inferred_type(default: Any) -> type | None:
    """The type a bare default stands in for, or `None` to infer nothing.

    A parameter written `port=8000` carries no annotation, but Python's own
    inference — and every type checker footman gates on — reads it as `int`.
    Without this, the command line would hand the body `'99'` while the
    checker had already concluded `int`: the same parameter arriving as two
    types depending on whether it was passed.

    The rule is *infer exactly where the checker infers*, so the cases it
    declines to type are declined here too: `None` (it says
    `Unknown | None`), containers empty or not (`Unknown`), and anything
    exotic. An `Enum` member is excluded despite `IntEnum` passing the
    `int` check — the value would coerce, but to the wrong kind of thing.
    """
    if default is None or isinstance(default, bool | enum.Enum):
        return None
    for basic in _INFERRED_FROM_DEFAULT:
        if isinstance(default, basic):
            return basic
    return None


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
    return " or ".join(_TYPE_PHRASE.get(t, t) for t in tags)


def element_choices(
    element: Any,
) -> tuple[list[str] | None, type[enum.Enum] | None, tuple[Any, ...] | None]:
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


def coerce_checked(value: str, element: Any) -> Any:
    """Coerce a token, refusing rather than falling back to the raw string.

    `coerce_one` is best-effort on purpose: on the ordinary path the
    splitter has already validated the token eagerly, so its fall-through
    is unreachable. A grouped position has no such pre-check — the
    splitter validates a parameter against one type, and a group's
    positions have one each — so it needs the strict form, or a
    `tuple[str, int]` would quietly accept `height='tall'`.
    """
    _choices, enum_cls, literal = element_choices(element)
    if enum_cls is not None:
        return coerce_one(value, element)  # `enum_cls(value)` refuses its own
    if literal is not None:
        if not any(str(lit) == value for lit in literal):
            spelled = "|".join(str(lit) for lit in literal)
            raise ValueError(f"must be one of {spelled} (got {value!r})")
        return coerce_one(value, element)
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        if not ok:
            raise ValueError(f"expects {type_phrase(tags)} (got {value!r})")
        return out
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
    # `Any`/`object` deliberately mean "take the raw string": both are classes
    # on Python >=3.11, so without this guard `Any("x")` would raise.
    if element is Any or element is object or not isinstance(element, type):
        return value
    if element is bytes:
        # A CLI token for a bytes parameter is its UTF-8 encoding — the raw
        # form arrives via the stdin boundary, where bytes stay bytes.
        return value.encode()
    try:
        if issubclass(element, _datetime.datetime):
            return element.fromisoformat(value)
        if issubclass(element, _datetime.date):
            return element.fromisoformat(value)
        # A user constructor: its call signature is its own business (the
        # contract is "accepts a string"), so the call is deliberately dynamic.
        ctor: Any = element
        return ctor(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{value!r} is not a valid {element.__name__}") from exc
