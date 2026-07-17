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

from collections.abc import Sequence
from typing import Any

_version_cache: dict[str, tuple[int, ...]]

def _flags(kwargs: dict[str, Any]) -> list[str]: ...
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
        check: bool | None = ...,
        diff: bool | None = ...,
        preview: bool | None = ...,
        target_version: str | None = ...,
        no_cache: bool | None = ...,
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
        fix: bool | None = ...,
        unsafe_fixes: bool | None = ...,
        show_fixes: bool | None = ...,
        diff: bool | None = ...,
        watch: bool | None = ...,
        fix_only: bool | None = ...,
        output_format: str | None = ...,
        output_file: str | None = ...,
        target_version: str | None = ...,
        preview: bool | None = ...,
        statistics: bool | None = ...,
        select: Sequence[str] | None = ...,
        ignore: Sequence[str] | None = ...,
        extend_select: Sequence[str] | None = ...,
        no_cache: bool | None = ...,
        config: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Uv(Tool):
    def sync(
        self,
        *,
        frozen: bool | None = ...,
        locked: bool | None = ...,
        group: Sequence[str] | None = ...,
        all_groups: bool | None = ...,
        no_dev: bool | None = ...,
        upgrade: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def build(
        self,
        *,
        sdist: bool | None = ...,
        wheel: bool | None = ...,
        out_dir: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def add(
        self,
        *packages: str,
        dev: bool | None = ...,
        group: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def lock(
        self, *, upgrade: bool | None = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def run(self, *args: str, nofail: bool = False, **flags: Any) -> int: ...

class _Git(Tool):
    def status(
        self, *, s: bool | None = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def add(self, *paths: str, nofail: bool = False, **flags: Any) -> int: ...
    def commit(
        self,
        *,
        message: str | None = ...,
        all: bool | None = ...,
        amend: bool | None = ...,
        no_verify: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def push(
        self,
        *refs: str,
        force_with_lease: bool | None = ...,
        tags: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def tag(self, *args: str, nofail: bool = False, **flags: Any) -> int: ...
    def diff(
        self,
        *paths: str,
        staged: bool | None = ...,
        quiet: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _DockerCompose(Tool):
    def up(
        self,
        *services: str,
        detach: bool | None = ...,
        build: bool | None = ...,
        wait: bool | None = ...,
        pull: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def down(
        self,
        *,
        volumes: bool | None = ...,
        remove_orphans: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def logs(
        self,
        *services: str,
        follow: bool | None = ...,
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
        push: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def run(self, *args: Any, nofail: bool = False, **flags: Any) -> int: ...

class _Bun(Tool):
    def add(
        self,
        *packages: str,
        dev: bool | None = ...,
        global_: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(
        self, *, frozen_lockfile: bool | None = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def run(
        self, script: str, *args: str, nofail: bool = False, **flags: Any
    ) -> int: ...
    def build(self, *entrypoints: str, nofail: bool = False, **flags: Any) -> int: ...

class _Mkdocs(Tool):
    def build(
        self,
        *,
        strict: bool | None = ...,
        clean: bool | None = ...,
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
        strict: bool | None = ...,
        nofail: bool = False,
        in_process: bool | None = None,
        **flags: Any,
    ) -> int: ...

class _Zensical(Tool):
    def build(
        self,
        *,
        strict: bool | None = ...,
        clean: bool | None = ...,
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
        show_missing: bool | None = ...,
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
        words_only: bool | None = ...,
        quiet: bool | None = ...,
        gitignore: bool | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Prek(Tool):
    def run(
        self,
        *hooks: str,
        all_files: bool | None = ...,
        files: Sequence[str] | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(self, *, nofail: bool = False, **flags: Any) -> int: ...

class _Markdownlint(Tool):
    def __call__(  # type: ignore[override]
        self,
        *globs: str,
        fix: bool | None = ...,
        config: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...

class _Basedpyright(Tool):
    def __call__(  # type: ignore[override]
        self,
        *paths: str,
        watch: bool | None = ...,
        outputjson: bool | None = ...,
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
