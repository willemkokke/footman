"""footman — a task runner with typed commands and instant completion.

Typed function signatures become real flags and positionals, modules become
nested command groups, and shell completion answers from a cached manifest
without importing your code.

The console-script entry lives here and is deliberately thin: completion must
dispatch to the stdlib-only hot path before importing the framework or the
user's tasks, so `main` checks `--complete` first and everything else is
imported lazily. A bare `import footman` pays for nothing but this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Give type-checkers the real types for the lazily re-exported names below;
    # at runtime these are served by `__getattr__` without importing registry
    # on a bare `import footman` (the completion hot path).
    from footman.app import App as App
    from footman.app import Brand as Brand
    from footman.compose import include as include
    from footman.compose import plugin as plugin
    from footman.context import Context as Context
    from footman.context import RunFailed as RunFailed
    from footman.context import parallel as parallel
    from footman.context import passthrough as passthrough
    from footman.context import run as run
    from footman.context import use_context as use_context
    from footman.params import Many as Many
    from footman.params import between as between
    from footman.params import check as check
    from footman.params import env as env
    from footman.params import exists as exists
    from footman.params import isdir as isdir
    from footman.params import isfile as isfile
    from footman.params import nosplit as nosplit
    from footman.params import suggest as suggest
    from footman.registry import Group as Group
    from footman.registry import group as group
    from footman.registry import reset as reset
    from footman.registry import task as task

__version__ = "0.5.0"
__all__ = [
    "App",
    "Brand",
    "Context",
    "Group",
    "Many",
    "RunFailed",
    "__version__",
    "between",
    "check",
    "env",
    "exists",
    "group",
    "include",
    "isdir",
    "isfile",
    "main",
    "nosplit",
    "parallel",
    "passthrough",
    "plugin",
    "run",
    "suggest",
    "task",
    "use_context",
]


def main() -> None:
    """Console-script entry for `footman` and `fm`."""
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "--complete":
        from footman._complete import complete_cli

        raise SystemExit(complete_cli(argv[1:]))
    from footman.app import App

    raise SystemExit(App().run(argv))


def __getattr__(name: str) -> object:
    # Lazy re-export: `from footman import task, group` works without paying the
    # registry import on a bare `import footman` (the completion hot path).
    if name in ("task", "group", "Group", "reset"):
        from footman import registry

        return getattr(registry, name)
    if name in ("include", "plugin"):
        from footman import compose

        return getattr(compose, name)
    if name in (
        "suggest",
        "Many",
        "nosplit",
        "exists",
        "isfile",
        "isdir",
        "between",
        "env",
        "check",
    ):
        from footman import params

        return getattr(params, name)
    if name in (
        "run",
        "parallel",
        "Context",
        "passthrough",
        "RunFailed",
        "use_context",
    ):
        from footman import context

        return getattr(context, name)
    if name in ("App", "Brand"):
        from footman import app

        return getattr(app, name)
    raise AttributeError(f"module 'footman' has no attribute {name!r}")
