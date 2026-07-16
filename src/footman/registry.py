"""The task registry: the ``@task`` / ``group()`` decorator surface.

Users build their command tree in a tasks file (``tasks.py`` by default)::

    from footman import task, group

    @task
    def lint(fix: bool = False):
        "Run ruff over the project."

    docs = group("docs", help="Documentation")

    @docs.task
    def serve(port: int = 8000):
        "Serve the docs locally."

A module of functions becomes a flat set of commands; each :func:`group` opens
a nested command group. Command names are the function/group name with
underscores turned into hyphens (``add_word`` -> ``add-word``).

This module holds only the tree structure. Turning it into the manifest (which
pays the cost of :mod:`inspect`) lives in :mod:`footman.manifest`, and the
completion hot path never imports either.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, overload

Task = Callable[..., Any]


def _cli_name(name: str) -> str:
    """Normalise a Python identifier to its command-line spelling."""
    return name.replace("_", "-")


class Group:
    """A node in the command tree: named tasks and nested sub-groups."""

    def __init__(self, name: str, help: str = "") -> None:
        self.name = name
        self.help = help
        self.tasks: dict[str, Task] = {}
        self.groups: dict[str, Group] = {}

    def _claim(self, key: str) -> None:
        where = f"group {self.name!r}" if self.name != "root" else "the root"
        if key in self.tasks:
            raise ValueError(f"{where} already has a task named {key!r}")
        if key in self.groups:
            raise ValueError(f"{where} already has a group named {key!r}")

    @overload
    def task(self, fn: Task) -> Task: ...
    @overload
    def task(
        self,
        fn: None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
    ) -> Callable[[Task], Task]: ...

    def task(
        self,
        fn: Task | None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
    ) -> Task | Callable[[Task], Task]:
        """Register a function as a task.

        Usable bare (``@task``) or parameterised (``@task(name="build")``) to
        override the command name. ``pre``/``post`` declare dependency tasks (by
        reference) that run before/after this one — the scheduler runs
        independent prerequisites in parallel::

            @task(pre=[format, lint, typecheck, test])
            def check(): ...
        """

        def register(fn: Task) -> Task:
            key = _cli_name(name or fn.__name__)
            self._claim(key)
            fn._footman_pre = list(pre)  # type: ignore[attr-defined]
            fn._footman_post = list(post)  # type: ignore[attr-defined]
            self.tasks[key] = fn
            return fn

        return register(fn) if fn is not None else register

    def group(self, name: str, help: str = "") -> Group:
        """Create and register a nested command group, returning it."""
        key = _cli_name(name)
        self._claim(key)
        sub = Group(key, help)
        self.groups[key] = sub
        return sub


#: The implicit root registry populated by the module-level ``task``/``group``
#: aliases (re-exported from ``footman``). Constructing an explicit ``Group`` is
#: always an option and keeps tests free of global state.
root = Group("root")
task = root.task
group = root.group


def reset() -> None:
    """Clear the global :data:`root` registry (used by the test-suite)."""
    root.tasks.clear()
    root.groups.clear()
