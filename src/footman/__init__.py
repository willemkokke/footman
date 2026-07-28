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
    from footman import docstrings as docstrings
    from footman import markdown as markdown
    from footman import tools as tools
    from footman._fetch import FetchError as FetchError
    from footman._fetch import fetch as fetch
    from footman.app import App as App
    from footman.app import Brand as Brand
    from footman.compose import include as include
    from footman.compose import plugin as plugin
    from footman.context import Context as Context
    from footman.context import Failed as Failed
    from footman.context import Result as Result
    from footman.context import RunFailed as RunFailed
    from footman.context import chdir as chdir
    from footman.context import confirm as confirm
    from footman.context import cwd as cwd
    from footman.context import fail as fail
    from footman.context import inherited as inherited
    from footman.context import parallel as parallel
    from footman.context import passthrough as passthrough
    from footman.context import progress as progress
    from footman.context import prompt as prompt
    from footman.context import run as run
    from footman.context import select as select
    from footman.context import track as track
    from footman.context import use_context as use_context
    from footman.invocation import Invocation as Invocation
    from footman.params import Arg as Arg
    from footman.params import Exists as Exists
    from footman.params import Forward as Forward
    from footman.params import IsDir as IsDir
    from footman.params import IsFile as IsFile
    from footman.params import Many as Many
    from footman.params import NoSplit as NoSplit
    from footman.params import Secret as Secret
    from footman.params import Stdin as Stdin
    from footman.params import Stdout as Stdout
    from footman.params import ask as ask
    from footman.params import between as between
    from footman.params import check as check
    from footman.params import doc as doc
    from footman.params import env as env
    from footman.params import exists as exists
    from footman.params import forward as forward
    from footman.params import isdir as isdir
    from footman.params import isfile as isfile
    from footman.params import nosplit as nosplit
    from footman.params import stdin as stdin
    from footman.params import stdout as stdout
    from footman.params import suggest as suggest
    from footman.registry import GlobalOption as GlobalOption
    from footman.registry import Group as Group
    from footman.registry import Tasks as Tasks
    from footman.registry import TaskView as TaskView
    from footman.registry import capture as capture
    from footman.registry import group as group
    from footman.registry import post_task as post_task
    from footman.registry import post_tasks as post_tasks
    from footman.registry import pre_bind as pre_bind
    from footman.registry import pre_task as pre_task
    from footman.registry import pre_tasks as pre_tasks
    from footman.registry import requires as requires
    from footman.registry import requires_dep as requires_dep
    from footman.registry import requires_env as requires_env
    from footman.registry import requires_tool as requires_tool
    from footman.registry import task as task
    from footman.registry import wrap_bind as wrap_bind
    from footman.registry import wrap_task as wrap_task
    from footman.testing import Runner as Runner
    from footman.testing import recording as recording

__version__ = "0.26.0"
__all__ = [
    "App",
    "Arg",
    "Brand",
    "Context",
    "Exists",
    "Failed",
    "FetchError",
    "Forward",
    "GlobalOption",
    "Group",
    "Invocation",
    "IsDir",
    "IsFile",
    "Many",
    "NoSplit",
    "Result",
    "RunFailed",
    "Runner",
    "Secret",
    "Stdin",
    "Stdout",
    "TaskView",
    "Tasks",
    "__version__",
    "ask",
    "between",
    "capture",
    "chdir",
    "check",
    "confirm",
    "cwd",
    "doc",
    "docstrings",
    "env",
    "exists",
    "fail",
    "fetch",
    "forward",
    "group",
    "include",
    "inherited",
    "isdir",
    "isfile",
    "main",
    "markdown",
    "nosplit",
    "parallel",
    "passthrough",
    "plugin",
    "post_task",
    "post_tasks",
    "pre_bind",
    "pre_task",
    "pre_tasks",
    "progress",
    "prompt",
    "recording",
    "requires",
    "requires_dep",
    "requires_env",
    "requires_tool",
    "run",
    "select",
    "stdin",
    "stdout",
    "suggest",
    "task",
    "tools",
    "track",
    "use_context",
    "wrap_bind",
    "wrap_task",
]


def main(tasks_file: str | None = None) -> None:
    """Console-script entry for `footman` and `fm`.

    `tasks_file` makes a tasks file its own command. Ending a file with

        if __name__ == "__main__":
            footman.main(__file__)

    turns it into a runnable script — `./deploy.py build` — reading its own
    tasks whatever the directory, which is what pairs with a PEP 723
    header and a `#!/usr/bin/env -S uv run --script` shebang. An explicit
    `-f` on the command line still wins.
    """
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "--complete":
        # Still first: the hot path answers before anything else is decided.
        from footman._complete import complete_cli

        raise SystemExit(complete_cli(argv[1:]))
    if tasks_file is not None and not any(
        a.startswith(("-f=", "--tasks-file=")) for a in argv
    ):
        argv = [f"--tasks-file={tasks_file}", *argv]
    from footman.app import App

    # `dist` names the distribution this console script ships in — what a
    # project's lockfile pins, and what a tasks file carrying its own
    # dependencies must declare. A branded CLI passes its own.
    raise SystemExit(App(dist="footman").run(argv))


def __getattr__(name: str) -> object:
    # Lazy re-export: `from footman import task, group` works without paying the
    # registry import on a bare `import footman` (the completion hot path).
    if name in (
        "task",
        "group",
        "Group",
        "capture",
        "GlobalOption",
        "pre_tasks",
        "pre_bind",
        "pre_task",
        "post_task",
        "post_tasks",
        "wrap_task",
        "wrap_bind",
        "Tasks",
        "TaskView",
        "requires",
        "requires_dep",
        "requires_env",
        "requires_tool",
    ):
        from footman import registry

        return getattr(registry, name)
    if name in ("Runner", "recording"):
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
    if name in ("fetch", "FetchError"):
        from footman import _fetch

        return getattr(_fetch, name)
    if name in ("include", "plugin"):
        from footman import compose

        return getattr(compose, name)
    if name in (
        "Arg",
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
        "ask",
        "forward",
        "Forward",
        "NoSplit",
        "Secret",
        "stdin",
        "Stdin",
        "stdout",
        "Stdout",
        "Stdout",
        "Exists",
        "IsFile",
        "IsDir",
    ):
        from footman import params

        return getattr(params, name)
    if name in (
        "run",
        "parallel",
        "Context",
        "Result",
        "inherited",
        "passthrough",
        "progress",
        "prompt",
        "chdir",
        "confirm",
        "cwd",
        "select",
        "track",
        "RunFailed",
        "Failed",
        "fail",
        "use_context",
        "wrap_bind",
        "wrap_task",
    ):
        from footman import context

        return getattr(context, name)
    if name in ("App", "Brand"):
        from footman import app

        return getattr(app, name)
    if name == "Invocation":
        from footman import invocation

        return invocation.Invocation
    raise AttributeError(f"module 'footman' has no attribute {name!r}")
