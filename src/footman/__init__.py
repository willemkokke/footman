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
    from footman import tools as tools
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
    from footman.params import doc as doc
    from footman.params import env as env
    from footman.params import exists as exists
    from footman.params import isdir as isdir
    from footman.params import isfile as isfile
    from footman.params import nosplit as nosplit
    from footman.params import suggest as suggest
    from footman.registry import Group as Group
    from footman.registry import capture as capture
    from footman.registry import group as group
    from footman.registry import task as task
    from footman.testing import Result as Result
    from footman.testing import Runner as Runner
    from footman.testing import recording as recording

__version__ = "0.13.0"
__all__ = [
    "App",
    "Brand",
    "Context",
    "Group",
    "Many",
    "Result",
    "RunFailed",
    "Runner",
    "__version__",
    "between",
    "capture",
    "check",
    "doc",
    "docstrings",
    "env",
    "exists",
    "group",
    "include",
    "isdir",
    "isfile",
    "main",
    "markdown",
    "nosplit",
    "parallel",
    "passthrough",
    "plugin",
    "recording",
    "run",
    "suggest",
    "task",
    "tools",
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
    if name in ("task", "group", "Group", "capture"):
        from footman import registry

        return getattr(registry, name)
    if name in ("Runner", "Result", "recording"):
        from footman import testing

        return getattr(testing, name)
    if name == "tools":
        import footman.tools

        return footman.tools
    if name == "docstrings":
        import footman.docstrings

        return footman.docstrings
    if name == "markdown":
        import footman.markdown

        return footman.markdown
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
        "doc",
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
