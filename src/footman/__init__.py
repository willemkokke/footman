"""footman — a task runner with typed commands and instant completion.

Typed function signatures become real flags and positionals, modules become
nested command groups, and shell completion answers from a cached manifest
without importing your code. Building that manifest does import it, in a
detached subprocess: the first <kbd>Tab</kbd> in a fresh directory, and the
background rebuild once the cache goes stale.

The console-script entry lives here and is deliberately thin: completion must
dispatch to the stdlib-only hot path before importing the framework or the
user's tasks, so `main` checks `--complete` first and everything else is
imported lazily. A bare `import footman` pays for nothing but this module.
"""

from __future__ import annotations

# The literal-False spelling both checkers honour without importing `typing`
# (~1.4 ms) — the completion dispatch runs through this module on every TAB
# press, and a bare `import footman` should pay for nothing it doesn't use.
TYPE_CHECKING = False
if TYPE_CHECKING:
    # Give type-checkers the real types for the lazily re-exported names below;
    # at runtime these are served by `__getattr__` without importing registry
    # on a bare `import footman` (the completion hot path).
    from footman import docstrings as docstrings
    from footman import markdown as markdown
    from footman._fetch import FetchError as FetchError
    from footman._fetch import fetch as fetch
    from footman._globals import Lane as Lane
    from footman._globals import console_lane as console_lane
    from footman._globals import cwd_lane as cwd_lane
    from footman._globals import make_lane as lane
    from footman._step import step as step
    from footman.app import App as App
    from footman.app import Brand as Brand
    from footman.compose import include as include
    from footman.compose import plugin as plugin
    from footman.context import Argv as Argv
    from footman.context import AuditEntry as AuditEntry
    from footman.context import Context as Context
    from footman.context import Failed as Failed
    from footman.context import Result as Result
    from footman.context import ResultView as ResultView
    from footman.context import RunFailed as RunFailed
    from footman.context import Section as Section
    from footman.context import Stream as Stream
    from footman.context import TimedOut as TimedOut
    from footman.context import cache_dir as cache_dir
    from footman.context import chdir as chdir
    from footman.context import confirm as confirm
    from footman.context import cwd as cwd
    from footman.context import data_dir as data_dir
    from footman.context import fail as fail
    from footman.context import given as given
    from footman.context import inherited as inherited
    from footman.context import mark as mark
    from footman.context import parallel as parallel
    from footman.context import passthrough as passthrough
    from footman.context import progress as progress
    from footman.context import prompt as prompt
    from footman.context import run as run
    from footman.context import section as section
    from footman.context import select as select
    from footman.context import stream as stream
    from footman.context import track as track
    from footman.context import use_context as use_context
    from footman.invocation import Invocation as Invocation
    from footman.params import Arg as Arg
    from footman.params import Exists as Exists
    from footman.params import Forward as Forward
    from footman.params import Hidden as Hidden
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
    from footman.params import default as default
    from footman.params import doc as doc
    from footman.params import env as env
    from footman.params import exists as exists
    from footman.params import forward as forward
    from footman.params import hidden as hidden
    from footman.params import isdir as isdir
    from footman.params import isfile as isfile
    from footman.params import matching as matching
    from footman.params import nosplit as nosplit
    from footman.params import stdin as stdin
    from footman.params import stdout as stdout
    from footman.params import suggest as suggest
    from footman.registry import GlobalOption as GlobalOption
    from footman.registry import Group as Group
    from footman.registry import Tasks as Tasks
    from footman.registry import TaskView as TaskView
    from footman.registry import capture as capture
    from footman.registry import config_section as config_section
    from footman.registry import group as group
    from footman.registry import post_task as post_task
    from footman.registry import post_tasks as post_tasks
    from footman.registry import pre_bind as pre_bind
    from footman.registry import pre_record as pre_record
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

__version__ = "0.48.0"

BUILTIN = ("footman.new",)
"""Stock footman's built-in task providers — what a project-less `fm` offers.

Named here, beside `main()`, because BOTH doors need it: the `App` the
execution path builds, and the `--complete` dispatch, which configures
`_paths` with it before the hot path decides whether this directory has a
global tree. A branded CLI passes its own through `App(builtin=…)`.
"""
__all__ = [
    "App",
    "Arg",
    "Argv",
    "AuditEntry",
    "Brand",
    "Context",
    "Exists",
    "Failed",
    "FetchError",
    "Forward",
    "GlobalOption",
    "Group",
    "Hidden",
    "Invocation",
    "IsDir",
    "IsFile",
    "Lane",
    "Many",
    "NoSplit",
    "Result",
    "ResultView",
    "RunFailed",
    "Runner",
    "Secret",
    "Section",
    "Stdin",
    "Stdout",
    "Stream",
    "TaskView",
    "Tasks",
    "TimedOut",
    "__version__",
    "ask",
    "between",
    "cache_dir",
    "capture",
    "chdir",
    "check",
    "config_section",
    "confirm",
    "console_lane",
    "cwd",
    "cwd_lane",
    "data_dir",
    "default",
    "doc",
    "docstrings",
    "env",
    "exists",
    "fail",
    "fetch",
    "forward",
    "given",
    "group",
    "hidden",
    "include",
    "inherited",
    "isdir",
    "isfile",
    "lane",
    "main",
    "mark",
    "markdown",
    "matching",
    "nosplit",
    "parallel",
    "passthrough",
    "plugin",
    "post_task",
    "post_tasks",
    "pre_bind",
    "pre_record",
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
    "section",
    "select",
    "stdin",
    "stdout",
    "step",
    "stream",
    "suggest",
    "task",
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
        # Still first: the hot path answers before anything else is decided,
        # and `footman.app` (with its dataclass machinery) stays off a TAB
        # press entirely. `_paths`' module defaults *are* stock footman's
        # locations — except the built-ins, which live on the `App` below
        # and no default can know. Telling `_paths` about them is two
        # attribute writes, and without it the hot path cannot see that a
        # project-less directory has a global tree at all: the fallback is
        # skipped, the refresh child writes nothing, and every TAB in a
        # directory like $HOME pays the full cold bound for empty output.
        from footman import _paths
        from footman._complete import complete_cli

        # `brand_version` too, not just the built-ins: the global-mode
        # manifest is keyed by (prog, version, builtins), and the execution
        # path configures the real version — leaving the default "" here
        # would give completion and execution two different global caches.
        _paths.configure(builtin=BUILTIN, brand_version=__version__)
        raise SystemExit(complete_cli(argv[1:]))
    # Past the hot path, so the TAB press above pays nothing for it (and
    # `_complete` writes bytes anyway): everything from here on prints text,
    # and a locale-encoded stdout defaults to errors='strict'. A task name, a
    # docstring, `--tree`'s own branch glyphs or the em dash in a header is
    # then unencodable on an ascii or cp1252 console, and a listing dies
    # half-written with a raw UnicodeEncodeError. Degrade those glyphs to '?'
    # instead. This is the same reconfigure `context.routing()` installs
    # around a run — hoisted here so the listings, which never start one, are
    # covered too.
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            # getattr, not hasattr-then-call: hasattr narrowing is not
            # portable across checkers, the getattr is.
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="replace")
    if tasks_file is not None and not any(
        a.startswith(("-f=", "--tasks-file=")) for a in argv
    ):
        argv = [f"--tasks-file={tasks_file}", *argv]
    # Past the completion hot path, this process exists to run one command and
    # exit, so it should not spend its startup collecting garbage it has not
    # made yet. `_globals` turns the collector back on — with everything
    # loaded by then frozen — at the last moment before task bodies run.
    from footman import _globals
    from footman.app import App

    _globals.defer_gc()

    # `dist` names the distribution this console script ships in — what a
    # project's lockfile pins, and what a tasks file carrying its own
    # dependencies must declare. A branded CLI passes its own.
    #
    # The stock pins — `FOOTMAN_*` variables, `footman.toml` config, the
    # long words a two-letter command cannot derive — are `App`'s own: any
    # `App` that keeps the `fm` command gets them, this one included.
    # `fm new` in an empty directory: footman declares its own built-in,
    # the same way any branded CLI would.
    raise SystemExit(App(dist="footman", builtin=BUILTIN).run(argv))


def __getattr__(name: str) -> object:
    # Lazy re-export: `from footman import task, group` works without paying the
    # registry import on a bare `import footman` (the completion hot path).
    if name in (
        "task",
        "group",
        "Group",
        "capture",
        "config_section",
        "GlobalOption",
        "pre_tasks",
        "pre_record",
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
    if name == "step":
        from footman import _step

        return _step.step
    if name in ("Lane", "cwd_lane", "console_lane", "lane"):
        from footman import _globals

        return _globals.make_lane if name == "lane" else getattr(_globals, name)
    if name in ("Runner", "recording"):
        from footman import testing

        return getattr(testing, name)
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
        "matching",
        "between",
        "env",
        "check",
        "default",
        "doc",
        "ask",
        "forward",
        "Forward",
        "hidden",
        "Hidden",
        "NoSplit",
        "Secret",
        "stdin",
        "Stdin",
        "stdout",
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
        "Argv",
        "Result",
        "ResultView",
        "AuditEntry",
        "inherited",
        "given",
        "passthrough",
        "progress",
        "prompt",
        "chdir",
        "confirm",
        "cache_dir",
        "cwd",
        "data_dir",
        "select",
        "track",
        "RunFailed",
        "Failed",
        "TimedOut",
        "fail",
        "use_context",
        "section",
        "stream",
        "mark",
        "Section",
        "Stream",
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
