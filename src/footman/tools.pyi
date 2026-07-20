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
# Flag lists were read from the installed tools' --help, not from memory.
# Stub drift therefore degrades a hint, never a run.

# The private aliases (`_re`, `_run`, …) mirror tools.py: they keep those names
# out of the public namespace so `tools.run`/`tools.sys`/… resolve to Tools via
# __getattr__, and they satisfy the AST parity test (tools.py bindings ⊆ this
# stub). Only `_threading` is referenced here; the rest exist purely for parity.
import re as _re  # noqa: F401
import subprocess as _subprocess  # noqa: F401
import sys as _sys  # noqa: F401
import threading as _threading
from collections.abc import Sequence
from typing import Any

from footman.context import run as _run  # noqa: F401

_argv_lock: _threading.Lock

_version_cache: dict[str, tuple[int, ...]]

class _Off: ...

off: _Off

# A boolean flag: True → --flag, off → --no-flag, False/None → omitted.
_Flag = bool | _Off | None

_NEGATIONS: dict[str, dict[str, str]]

def _negation(tool: str, key: str) -> str: ...
def _flags(kwargs: dict[str, Any], tool: str = ...) -> list[str]: ...
def _console_entrypoint(name: str) -> Any | None: ...
def _accepts_args(entry: Any) -> bool: ...

class Tool:
    _argv0: str
    _base: list[str]
    _prefer_in_process: bool
    def __init__(self, name: str, *base: str, in_process: bool = False) -> None: ...
    def __getattr__(self, verb: str) -> Tool: ...
    def __call__(
        self,
        *args: Any,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...
    def installed_version(self) -> tuple[int, ...]: ...

class _RuffFormat(Tool):
    def __call__(  # type: ignore[override]
        self,
        *paths: str,
        check: _Flag = ...,
        diff: _Flag = ...,
        preview: _Flag = ...,
        target_version: str | None = ...,
        no_cache: _Flag = ...,
        config: str | None = ...,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...

class _Ruff(Tool):
    format: _RuffFormat
    def check(
        self,
        *paths: str,
        fix: _Flag = ...,
        unsafe_fixes: _Flag = ...,
        show_fixes: _Flag = ...,
        diff: _Flag = ...,
        watch: _Flag = ...,
        fix_only: _Flag = ...,
        output_format: str | None = ...,
        output_file: str | None = ...,
        target_version: str | None = ...,
        preview: _Flag = ...,
        statistics: _Flag = ...,
        select: Sequence[str] | None = ...,
        ignore: Sequence[str] | None = ...,
        extend_select: Sequence[str] | None = ...,
        exit_zero: _Flag = ...,
        exit_non_zero_on_fix: _Flag = ...,
        quiet: _Flag = ...,
        silent: _Flag = ...,
        verbose: _Flag = ...,
        isolated: _Flag = ...,
        no_cache: _Flag = ...,
        cache_dir: str | None = ...,
        config: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Uv(Tool):
    def sync(
        self,
        *,
        frozen: _Flag = ...,
        locked: _Flag = ...,
        group: Sequence[str] | None = ...,
        all_groups: _Flag = ...,
        no_dev: _Flag = ...,
        upgrade: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def build(
        self,
        *,
        sdist: _Flag = ...,
        wheel: _Flag = ...,
        out_dir: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def add(
        self,
        *packages: str,
        dev: _Flag = ...,
        group: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def lock(
        self, *, upgrade: _Flag = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def run(self, *args: str, nofail: bool = False, **flags: Any) -> int: ...

class _Git(Tool):
    def status(self, *, s: _Flag = ..., nofail: bool = False, **flags: Any) -> int: ...
    def add(self, *paths: str, nofail: bool = False, **flags: Any) -> int: ...
    def commit(
        self,
        *,
        message: str | None = ...,
        all: _Flag = ...,
        amend: _Flag = ...,
        no_verify: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def push(
        self,
        *refs: str,
        force_with_lease: _Flag = ...,
        tags: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def tag(self, *args: str, nofail: bool = False, **flags: Any) -> int: ...
    def diff(
        self,
        *paths: str,
        staged: _Flag = ...,
        quiet: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _DockerCompose(Tool):
    def up(
        self,
        *services: str,
        detach: _Flag = ...,
        build: _Flag = ...,
        wait: _Flag = ...,
        pull: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def down(
        self,
        *,
        volumes: _Flag = ...,
        remove_orphans: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def logs(
        self,
        *services: str,
        follow: _Flag = ...,
        tail: int | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def build(self, *services: str, nofail: bool = False, **flags: Any) -> int: ...

class _Docker(Tool):
    compose: _DockerCompose
    def build(
        self,
        path: str | None = ...,
        *,
        tag: str | None = ...,
        file: str | None = ...,
        platform: str | None = ...,
        push: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def run(self, *args: Any, nofail: bool = False, **flags: Any) -> int: ...

class _Bun(Tool):
    def add(
        self,
        *packages: str,
        dev: _Flag = ...,
        global_: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(
        self, *, frozen_lockfile: _Flag = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def run(
        self, script: str, *args: str, nofail: bool = False, **flags: Any
    ) -> int: ...
    def build(self, *entrypoints: str, nofail: bool = False, **flags: Any) -> int: ...

class _Mkdocs(Tool):
    def build(
        self,
        *,
        strict: _Flag = ...,
        clean: _Flag = ...,
        site_dir: str | None = ...,
        config_file: str | None = ...,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...
    def serve(
        self,
        *,
        dev_addr: str | None = ...,
        strict: _Flag = ...,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...

class _Zensical(Tool):
    def build(
        self,
        *,
        strict: _Flag = ...,
        clean: _Flag = ...,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...
    def serve(
        self, *, nofail: bool = False, in_process: bool | None = None, **flags: Any
    ) -> int: ...

class _Coverage(Tool):
    def run(self, *args: str, nofail: bool = False, **flags: Any) -> int: ...
    def report(
        self,
        *,
        fail_under: float | None = ...,
        show_missing: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def html(
        self, *, directory: str | None = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def xml(
        self, *, o: str | None = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def combine(self, *paths: str, nofail: bool = False, **flags: Any) -> int: ...
    def erase(self, *, nofail: bool = False, **flags: Any) -> int: ...

class _Cspell(Tool):
    def lint(
        self,
        *globs: str,
        config: str | None = ...,
        words_only: _Flag = ...,
        quiet: _Flag = ...,
        gitignore: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Prek(Tool):
    def run(
        self,
        *hooks: str,
        all_files: _Flag = ...,
        files: Sequence[str] | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(self, *, nofail: bool = False, **flags: Any) -> int: ...

class _Markdownlint(Tool):
    def __call__(  # type: ignore[override]
        self,
        *globs: str,
        fix: _Flag = ...,
        config: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Basedpyright(Tool):
    def __call__(  # type: ignore[override]
        self,
        *paths: str,
        watch: _Flag = ...,
        outputjson: _Flag = ...,
        project: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

ruff: _Ruff
ruff_format: _RuffFormat
basedpyright: _Basedpyright
uv: _Uv
git: _Git
docker: _Docker
bun: _Bun
mkdocs: _Mkdocs
zensical: _Zensical
coverage: _Coverage
cspell: _Cspell
prek: _Prek
markdownlint: _Markdownlint

def pytest(*args: str, in_process: bool = True, nofail: bool = False) -> int: ...
def python(*args: str, nofail: bool = False) -> int: ...
def sh(command: str, nofail: bool = False) -> int: ...
def __getattr__(name: str) -> Tool: ...
