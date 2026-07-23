# Autocomplete without the import bill.
#
# This stub is never imported at runtime — the bridge in tools.py stays a
# few dozen mechanical lines — but IDEs and type checkers read it, so the
# common verbs and flags of the curated tools autocomplete like duty's
# hand-written wrappers, at zero runtime cost.
#
# Two rules keep the stub honest:
# - every verb ends in `**flags: Any`, so a stub can *suggest* flags but
#   never forbid one — when a tool grows a flag, the bridge already speaks
#   it and the stub merely hasn't heard of it yet;
# - unknown verbs fall through to `Tool` via `__getattr__`, so nothing the
#   runtime accepts is a type error.
# Flag lists are *generated* from the installed tools — `fm footman tools
# sync` writes one file per tool under `_stubs/`, and `fm footman tools
# audit` fails when a checked-in stub and its tool disagree. Stub drift
# therefore degrades a hint, never a run.

# The private aliases (`_re`, `_run`, …) mirror tools.py: they keep those names
# out of the public namespace so `tools.run`/`tools.sys`/… resolve to Tools via
# __getattr__, and they satisfy the AST parity test (tools.py bindings ⊆ this
# stub). Only `_threading` is referenced here; the rest exist purely for parity.
import re as _re  # noqa: F401
import subprocess as _subprocess  # noqa: F401
import sys as _sys  # noqa: F401
import threading as _threading
from collections.abc import Iterator, Sequence
from typing import Any

from footman._stubs.basedpyright import Basedpyright as Basedpyright

# One generated file per tool — `fm footman tools sync` writes them from
# the installed binaries, and `audit` fails when they drift. They import
# `Tool` and the aliases from here, which a stub may do circularly.
from footman._stubs.bash import Bash as Bash
from footman._stubs.build import Build as Build
from footman._stubs.bun import Bun as Bun
from footman._stubs.cmake import Cmake as Cmake
from footman._stubs.coverage import Coverage as Coverage
from footman._stubs.cspell import Cspell as Cspell
from footman._stubs.docker import Docker as Docker
from footman._stubs.eclint import Eclint as Eclint
from footman._stubs.fish import Fish as Fish
from footman._stubs.gh import Gh as Gh
from footman._stubs.git import Git as Git
from footman._stubs.git_changelog import GitChangelog as GitChangelog
from footman._stubs.git_cliff import GitCliff as GitCliff
from footman._stubs.markdownlint import Markdownlint as Markdownlint
from footman._stubs.mkdocs import Mkdocs as Mkdocs
from footman._stubs.mypy import Mypy as Mypy
from footman._stubs.ninja import Ninja as Ninja
from footman._stubs.nu import Nu as Nu
from footman._stubs.prek import Prek as Prek
from footman._stubs.pwsh import Pwsh as Pwsh
from footman._stubs.pytest import Pytest as Pytest
from footman._stubs.python import Python as Python
from footman._stubs.ruff import Ruff as Ruff
from footman._stubs.ruff_format import RuffFormat as RuffFormat
from footman._stubs.twine import Twine as Twine
from footman._stubs.ty import Ty as Ty
from footman._stubs.uv import Uv as Uv
from footman._stubs.zensical import Zensical as Zensical
from footman._stubs.zsh import Zsh as Zsh
from footman.context import Invocation as _Invocation  # noqa: F401
from footman.context import run as _run  # noqa: F401

_argv_lock: _threading.Lock

_version_cache: dict[str, tuple[int, ...]]

class _Off: ...

off: _Off

# A boolean flag: True → --flag, off → the tool's own negation,
# False/None → omitted (which is what lets a task parameter's default flow
# straight through).
_Flag = bool | _Off | None
# An option that takes a value. Wide on purpose: the bridge stringifies
# whatever it is handed and repeats the flag for each item of a sequence,
# so a narrower type would reject calls that demonstrably work.
_Value = str | int | float | Sequence[str] | _Off | None
# An option whose value is *optional* — usable bare (`gpg_sign=True`, sign
# with the default key) or with a value (`gpg_sign="KEY"`). Both spell a
# valid command; the tool prints its placeholder attached to the flag,
# `--gpg-sign[=<key-id>]`, which is how footman tells the two apart.
_ValuedFlag = bool | _Value

_NEGATIONS: dict[str, dict[str, str]]
_WRAPPERS: dict[str, frozenset[str]]

def _negation(tool: str, key: str) -> str: ...
def _is_wrapper(argv0: str, base: list[str]) -> bool: ...
def _emit(
    kwargs: dict[str, Any], tool: str = ...
) -> Iterator[tuple[str, str | None]]: ...
def _spell(flag: str, value: str | None, *, attach_long: bool) -> list[str]: ...
def _flags(kwargs: dict[str, Any], tool: str = ...) -> list[str]: ...
def _show_parts(
    argv0: str, base: list[str], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[str, str], ...]: ...
def _quote(text: str) -> str: ...
def _console_entrypoint(name: str) -> Any | None: ...
def _accepts_args(entry: Any) -> bool: ...

class Tool:
    _argv0: str
    _base: list[str]
    _prefer_in_process: bool
    _single_dash: bool
    def __init__(
        self,
        name: str,
        *base: str,
        in_process: bool = False,
        path: str = ...,
        entry: str = ...,
        single_dash: bool = False,
    ) -> None: ...
    def __getattr__(self, verb: str) -> Tool: ...
    def opts(self, **flags: Any) -> Tool: ...
    def __call__(
        self,
        *args: Any,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...
    def installed_version(self) -> tuple[int, ...]: ...

ruff: Ruff
ruff_format: RuffFormat
basedpyright: Basedpyright
uv: Uv
git: Git
docker: Docker
bun: Bun
mkdocs: Mkdocs
zensical: Zensical
coverage: Coverage
cspell: Cspell
prek: Prek
markdownlint: Markdownlint
gh: Gh
eclint: Eclint
mypy: Mypy
ty: Ty
twine: Twine
git_changelog: GitChangelog
git_cliff: GitCliff
build: Build
cmake: Cmake
ninja: Ninja
pytest: Pytest
python: Python
bash: Bash
zsh: Zsh
fish: Fish
pwsh: Pwsh
nu: Nu

def __getattr__(name: str) -> Tool: ...
