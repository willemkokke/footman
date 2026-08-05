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
# Flag lists are *generated* from the installed tools — `fm tools.sync`
# writes one file per tool under `_stubs/`, and `fm tools.audit`
# fails when a checked-in stub and its tool disagree. Stub drift
# therefore degrades a hint, never a run.

# The private aliases (`_re`, `_run`, …) mirror tools.py: they keep those names
# out of the public namespace so `tools.run`/`tools.sys`/… resolve to Tools via
# __getattr__, and they satisfy the AST parity test (tools.py bindings ⊆ this
# stub). Only `_re` and `_threading` are referenced here; the rest exist purely
# for parity.
import os as _os  # noqa: F401
import re as _re
import subprocess as _subprocess  # noqa: F401
import sys as _sys  # noqa: F401
import threading as _threading
import types as _types  # noqa: F401
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path as _Path
from typing import Any, Generic, NamedTuple, Self
from typing import cast as _cast  # noqa: F401

from typing_extensions import TypeVar

from footman._stubs.basedpyright import Basedpyright as Basedpyright

# One generated file per tool — `fm tools.sync` writes them from
# the installed binaries, and `audit` fails when they drift. They import
# `Tool` and the aliases from here, which a stub may do circularly.
from footman._stubs.bash import Bash as Bash
from footman._stubs.build import Build as Build
from footman._stubs.bun import Bun as Bun
from footman._stubs.cmake import Cmake as Cmake
from footman._stubs.cmd import Cmd as Cmd
from footman._stubs.coverage import Coverage as Coverage
from footman._stubs.cspell import Cspell as Cspell
from footman._stubs.djlint import Djlint as Djlint
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
from footman._stubs.ssh import Ssh as Ssh
from footman._stubs.ssh_keygen import SshKeygen as SshKeygen
from footman._stubs.ssh_keyscan import SshKeyscan as SshKeyscan
from footman._stubs.twine import Twine as Twine
from footman._stubs.ty import Ty as Ty
from footman._stubs.uv import Uv as Uv
from footman._stubs.zensical import Zensical as Zensical
from footman._stubs.zsh import Zsh as Zsh
from footman.context import Argv as Argv
from footman.context import Invocation as _Invocation  # noqa: F401
from footman.context import Result as Result
from footman.context import ResultView as _ResultView
from footman.context import _target_cwd as _target_cwd_of  # noqa: F401
from footman.context import color_on as _color_on  # noqa: F401
from footman.context import container_error as _container_error  # noqa: F401
from footman.context import current as _current  # noqa: F401
from footman.context import real_stderr as _real_stderr  # noqa: F401
from footman.context import run as _run  # noqa: F401

_QUIET: dict[str, str]

_argv_lock: _threading.Lock

_version_cache: dict[str, tuple[int, ...]]
_VERSION: _re.Pattern[str]

def read_version(text: str) -> str: ...
def version_tuple(version: str) -> tuple[int, ...]: ...

class _Off: ...

off: _Off

class _Consumed: ...

_CONSUMED: _Consumed
_consume_lock: _threading.Lock

class _StdinPayload:
    def __init__(self, value: str) -> None: ...
    def take(self) -> str | _Consumed: ...

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

class _ColorFlag(NamedTuple):
    on: tuple[str, ...]
    off: tuple[str, ...] = ...
    pre_verb: bool = ...

def _load_color() -> dict[str, dict[str, _ColorFlag]]: ...

_COLOR: dict[str, dict[str, _ColorFlag]]

def _negation(tool: str, key: str) -> str: ...
def _is_wrapper(argv0: str, base: list[str]) -> bool: ...
def _color_flag(argv0: str, base: list[str]) -> _ColorFlag | None: ...
def _color_tokens(
    argv0: str, base: list[str], kwargs: dict[str, Any]
) -> _ColorFlag: ...
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

_TOOL_OPTS: tuple[str, ...]

def _opts_overrides(kwargs: dict[str, Any]) -> dict[str, Any]: ...

_CONTAINERS: tuple[type, ...]

def _positionals(args: tuple[Any, ...], tool: str) -> list[str]: ...

# `default=` (PEP 696) is why a bare `Tool(...)` construction and a bare
# `Tool` annotation both mean `Tool[Result]` in every checker — running is
# the default, `.argv` is how `Tool[Argv]` arises. `typing_extensions` in a
# stub costs no runtime dependency: a .pyi is never imported.
_R = TypeVar("_R", default=Result)

# Generic over what a call returns. Every binding below fixes it to
# `Result`; `.argv` answers with the same surface over `Argv`. Making the
# parameter live on the *base* is what keeps every generated override —
# `__call__` returning its class TypeVar, `argv` re-parameterising — a
# plain covariant override rather than a Liskov violation needing
# suppression in four type checkers.
class Tool(Generic[_R]):
    _argv0: str
    _base: list[str]
    _prefer_in_process: bool
    _single_dash: bool
    _rebound: bool
    _opts: dict[str, Any]
    def __init__(
        self,
        name: str,
        *base: str,
        in_process: bool = False,
        path: str = ...,
        entry: str = ...,
        single_dash: bool = False,
        version_argv: tuple[str, ...] = ...,
        policy: dict[str, Any] | None = None,
    ) -> None: ...
    def __getattr__(self, verb: str) -> Tool[_R]: ...
    # footman run-control policy — a closed vocabulary that rides beside the
    # call (never a tool flag). Returns Self, so a generated tool keeps its verb
    # completions: `git.opts(nofail=True).push()`.
    #
    # The options are forwarded verbatim to `run()`, so they carry `run()`'s
    # types — the four it treats as "unset" take None, which is what a caller
    # computing one (`timeout=cfg.timeout`, `cwd=None if inline else build_dir`)
    # passes. `test_tools.py` enforces the match.
    def opts(
        self,
        *,
        nofail: bool = ...,
        in_process: bool | None = ...,
        capture: bool = ...,
        input: str | None = ...,
        env: dict[str, str] | None = ...,
        title: str | None = ...,
        cwd: str | _Path | None = ...,
        rel: str | _Path | None = ...,
        recorded: bool = ...,
        timeout: float | None = ...,
        pre_record: Callable[[_ResultView], None] | None = ...,
    ) -> Self: ...
    # A tool's own global options, bound before the next subcommand
    # (`docker.flags(host="x").ps()`). Generated stubs override it with the
    # tool's typed globals; the base takes any flag.
    def at(self, path: str | _Path) -> Self: ...
    def flags(self, **flags: Any) -> Self: ...
    # Build this call's command line instead of running it — `.argv` slots in
    # right before the parentheses. Generated stubs override it with the
    # tool's own class re-parameterised to return `Argv` (a covariant
    # narrowing, since every generated class derives from `Tool[_R]`), so a
    # built call keeps its flag checking; the base answers with the untyped
    # handle, whose calls all return `Argv`.
    @property
    def argv(self) -> Tool[Argv]: ...
    def __call__(self, *args: Any, **flags: Any) -> _R: ...
    def installed_version(self) -> tuple[int, ...]: ...

# The building handle: a `Tool` whose calls answer in `Argv`. The class adds
# nothing the parameterisation doesn't say — it exists so the runtime has a
# concrete class to chain through `_sub`, and the annotation `Tool[Argv]`
# is how the stubs spell it.
class ArgvTool(Tool[Argv]): ...

# Parameterised by what a call returns: `Result` here, and `.argv` re-spells
# the same class over `Argv` — one flag block serving both the run and the
# build path.
ruff: Ruff[Result]
ruff_format: RuffFormat[Result]
basedpyright: Basedpyright[Result]
uv: Uv[Result]
git: Git[Result]
docker: Docker[Result]
bun: Bun[Result]
mkdocs: Mkdocs[Result]
zensical: Zensical[Result]
coverage: Coverage[Result]
cspell: Cspell[Result]
prek: Prek[Result]
markdownlint: Markdownlint[Result]
gh: Gh[Result]
ssh: Ssh[Result]
ssh_keygen: SshKeygen[Result]
ssh_keyscan: SshKeyscan[Result]
eclint: Eclint[Result]
djlint: Djlint[Result]
mypy: Mypy[Result]
ty: Ty[Result]
twine: Twine[Result]
git_changelog: GitChangelog[Result]
git_cliff: GitCliff[Result]
build: Build[Result]
cmake: Cmake[Result]
ninja: Ninja[Result]
pytest: Pytest[Result]
python: Python[Result]
bash: Bash[Result]
zsh: Zsh[Result]
fish: Fish[Result]
pwsh: Pwsh[Result]
nu: Nu[Result]
cmd: Cmd[Result]

def __getattr__(name: str) -> Tool[Result]: ...
