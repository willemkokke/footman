"""Parse docstrings: summary, long description, per-parameter docs.

Standalone by design — stdlib only, no footman imports — so the module can be
lifted into any project that wants structured docstrings. footman uses it to
fill parameter help (an explicit `doc("...")` marker wins) and the long
description `fm --help <task>` shows.

Three conventions are recognised, auto-detected per docstring; whichever
appears first wins, no mixing:

- **Google** — `Args:` / `Arguments:` / `Parameters:` header, `name: text`
  or `name (type): text` entries, continuations indented deeper;
- **NumPy** — `Parameters` over a `----` underline, `name : type` entry
  lines, descriptions indented beneath (`a, b : int` documents both);
- **Sphinx** — `:param name: text` / `:param type name: text` fields
  (`:arg`/`:argument`/`:parameter` accepted).

The parser is a single pass over cleaned lines (`inspect.cleandoc`, so tabs
and uniform indentation are normalised first) and is deliberately tolerant of
the real world: uneven indents, a missing blank line after the summary, CRLF,
sections in unusual orders, and empty entries all parse rather than raise.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field

__all__ = ["Docstring", "parse"]


@dataclass(frozen=True)
class Docstring:
    """The parsed pieces of one docstring.

    Shaped to grow additively (`returns`, `raises`, …) without breaking
    reusers; absent pieces are empty, never None.
    """

    summary: str = ""
    """The first line — the one-liner listings and completion menus show."""
    long: str = ""
    """Prose between the summary and the first section (`Args:` and
    friends), structure preserved; empty when there is none."""
    params: dict[str, str] = field(default_factory=dict)
    """Per-parameter help, keyed by the Python parameter name (not the
    CLI spelling): `{"fix": "apply safe fixes in place"}`."""


_GOOGLE_HEADER = re.compile(r"^(?:args|arguments|parameters)\s*:\s*$", re.IGNORECASE)
_NUMPY_HEADER = re.compile(r"^(?:parameters|other parameters)\s*:?\s*$", re.IGNORECASE)
_UNDERLINE = re.compile(r"^-{3,}\s*$")
# `name: text`, `name (type): text`, `*args: text` — the colon is required, so
# a wrapped continuation line almost never false-positives.
_GOOGLE_ENTRY = re.compile(r"^(\*{0,2}[A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
# `name`, `name : type`, `a, b : int` — names only; the type tail is ignored.
_NUMPY_ENTRY = re.compile(
    r"^(\*{0,2}[A-Za-z_]\w*(?:\s*,\s*\*{0,2}[A-Za-z_]\w*)*)(?:\s*:.*)?$"
)
_SPHINX_PARAM = re.compile(r"^:(?:param|parameter|arg|argument)\s+([^:]+):\s*(.*)$")
_SPHINX_FIELD = re.compile(r"^:[a-zA-Z]")  # any field ends the long description
# Any Google-style section header (`Returns:`, `See Also:`, …) — alone on its
# line — ends the long description, whether or not we extract from it.
_ANY_SECTION = re.compile(r"^[A-Z][A-Za-z ]*:\s*$")


def parse(text: str | None) -> Docstring:
    """Parse *text* (a raw or already-cleaned docstring) into its pieces."""
    if not text:
        return Docstring()
    lines = inspect.cleandoc(text).splitlines()
    if not lines:
        return Docstring()
    summary, body = lines[0].strip(), lines[1:]

    found: list[tuple[int, str]] = []
    if (i := _find_google(body)) is not None:
        found.append((i, "google"))
    if (i := _find_numpy(body)) is not None:
        found.append((i, "numpy"))
    if (i := _find_sphinx(body)) is not None:
        found.append((i, "sphinx"))
    if not found:
        return Docstring(summary, _prose(body[: _long_end(body, len(body))]))

    start, kind = min(found)
    params = {
        "google": _google_params,
        "numpy": _numpy_params,
        "sphinx": _sphinx_params,
    }[kind](body, start)
    return Docstring(summary, _prose(body[: _long_end(body, start)]), params)


def _long_end(body: list[str], stop: int) -> int:
    """Where the long description ends: the first section-ish line before
    *stop* — a `Word:` header alone on its line, or a NumPy underlined
    header — even when it isn't one we extract parameters from."""
    for i in range(stop):
        stripped = body[i].strip()
        if not stripped:
            continue
        if _ANY_SECTION.match(stripped):
            return i
        if i + 1 < stop and _UNDERLINE.match(body[i + 1].strip()):
            return i
    return stop


# --- section detection --------------------------------------------------------


def _find_google(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _GOOGLE_HEADER.match(line.strip()):
            # `Parameters:` over a dash underline is NumPy wearing a colon —
            # let the NumPy path claim it.
            nxt = next((s for s in lines[i + 1 :] if s.strip()), "")
            if not _UNDERLINE.match(nxt.strip()):
                return i
    return None


def _find_numpy(lines: list[str]) -> int | None:
    for i, line in enumerate(lines[:-1]):
        if _NUMPY_HEADER.match(line.strip()) and _UNDERLINE.match(lines[i + 1].strip()):
            return i
    return None


def _find_sphinx(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _SPHINX_FIELD.match(line.strip()):
            return i
    return None


# --- helpers ------------------------------------------------------------------


def _prose(lines: list[str]) -> str:
    """The long description: outer blank lines dropped, structure kept."""
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(line.rstrip() for line in lines)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _clean_name(name: str) -> str:
    return name.lstrip("*").strip()


def _join(fragments: list[str]) -> str:
    return " ".join(f.strip() for f in fragments if f.strip()).strip()


def _store(params: dict[str, str], name: str, fragments: list[str]) -> None:
    if name and name not in params:  # first definition wins on duplicates
        params[name] = _join(fragments)


# --- per-format extraction ----------------------------------------------------


def _google_params(lines: list[str], start: int) -> dict[str, str]:
    """Entries under a Google `Args:` header, ended by a dedent to it."""
    header_indent = _indent(lines[start])
    params: dict[str, str] = {}
    name, fragments = "", []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue  # blank lines inside the section are allowed
        if _indent(line) <= header_indent:
            break  # dedent to (or past) the header: the section is over
        entry = _GOOGLE_ENTRY.match(line.strip())
        if entry:
            _store(params, name, fragments)
            name, fragments = _clean_name(entry[1]), [entry[2]]
        else:
            fragments.append(line)  # a wrapped continuation of the entry
    _store(params, name, fragments)
    return params


def _numpy_params(lines: list[str], start: int) -> dict[str, str]:
    """`name : type` lines after a NumPy header, descriptions indented."""
    header_indent = _indent(lines[start])
    params: dict[str, str] = {}
    names: list[str] = []
    fragments: list[str] = []

    def flush() -> None:
        for one in names:
            _store(params, one, fragments)

    i = start + 2  # skip the header and its underline
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _indent(line) <= header_indent and _UNDERLINE.match(next_stripped):
            # A new underlined section: Other Parameters keeps collecting,
            # anything else (Returns, Notes, …) ends the walk.
            if _NUMPY_HEADER.match(stripped):
                i += 2
                continue
            break
        entry = _NUMPY_ENTRY.match(stripped)
        if entry is not None and _indent(line) <= header_indent:
            flush()
            names = [_clean_name(n) for n in entry[1].split(",")]
            fragments = []
        else:
            fragments.append(line)  # an indented description line
        i += 1
    flush()
    return params


def _sphinx_params(lines: list[str], start: int) -> dict[str, str]:
    """`:param [type] name: text` fields; indented follow-ups continue one."""
    params: dict[str, str] = {}
    name, fragments = "", []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            _store(params, name, fragments)
            name, fragments = "", []
            continue
        if stripped.startswith(":"):
            _store(params, name, fragments)
            name, fragments = "", []
            entry = _SPHINX_PARAM.match(stripped)
            if entry:  # `:param str name:` — the name is the last word
                name = _clean_name(entry[1].split()[-1])
                fragments = [entry[2]]
            continue  # any other field (:returns:, :type x:, …) is skipped
        if name:
            fragments.append(line)
    _store(params, name, fragments)
    return params
