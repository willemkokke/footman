"""The task registry: the `@task` / `group()` decorator surface.

Users build their command tree in a tasks file (`tasks.py` by default):

```python
from footman import task, group

@task
def lint(fix: bool = False):
    "Run ruff over the project."

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    "Serve the docs locally."
```

A module of functions becomes a flat set of commands; each `group` opens
a nested command group. Command names are the function/group name with
underscores turned into hyphens (`add_word` -> `add-word`).

This module holds only the tree structure. Turning it into the manifest (which
pays the cost of `inspect`) lives in `footman.manifest`, and the
completion hot path never imports either.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Sequence
from typing import Any, overload

Task = Callable[..., Any]


class RegistrationError(ValueError):
    """A task or group name collided during registration.

    Subclasses `ValueError` so existing `except ValueError` handlers keep
    working; the app layer matches this type to report a duplicate name as a
    user error rather than an import failure.
    """


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
            raise RegistrationError(f"{where} already has a task named {key!r}")
        if key in self.groups:
            raise RegistrationError(f"{where} already has a group named {key!r}")

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
        when: bool | Callable[[], object] = True,
        requires: str | Sequence[str] = (),
        reason: str = "",
        progress: bool = True,
        infinite: bool = False,
    ) -> Callable[[Task], Task]: ...

    def task(
        self,
        fn: Task | None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        when: bool | Callable[[], object] = True,
        requires: str | Sequence[str] = (),
        reason: str = "",
        progress: bool = True,
        infinite: bool = False,
    ) -> Task | Callable[[Task], Task]:
        """Register a function as a task.

        Usable bare (`@task`) or parameterised (`@task(name="build")`) to
        override the command name. `pre`/`post` declare dependency tasks (by
        reference) that run before/after this one — the scheduler runs
        independent prerequisites in parallel:

        ```python
        @task(pre=[format, lint, typecheck, test])
        def check(): ...
        ```

        `when` disables a task that can't run *here* while keeping it listed
        (pytest-skip semantics — the listing shows the `reason`, running it
        refuses with the reason, and completion stays stable):

        ```python
        @task(when=lambda: shutil.which("docker"), reason="requires docker")
        def up(): ...
        ```

        `requires` names Python modules the task needs — checked with
        `importlib.util.find_spec`, which does not import the module itself (a
        dotted name imports its parent packages to locate it). The task
        is listed as unavailable (with a taught reason) when a module is
        absent, so a shared library can carry tasks with heavy optional
        dependencies: keep the actual `import` in the body, so the cost is
        paid only when the task runs, and mark the requirement here so a
        missing dependency reads as a clean message, not an import crash:

        ```python
        @task(requires="stripe", reason="pip install devkit[release]")
        def publish(version: str):
            import stripe  # only imported when publish actually runs
            ...
        ```

        A callable `when` is re-evaluated live on every run — the cached
        manifest is never trusted for availability. To *hide* a task
        entirely, use plain Python: `if sys.platform == "darwin": @task ...`

        `progress=False` marks a task whose duration has no rhyme or
        reason (a REPL, a watcher, a network fetch): any run containing it
        never records timing history and never shows a determinate
        progress bar — the indeterminate pulse still does.

        `infinite=True` marks a task that runs until *stopped* — a dev
        server, a follow-mode tail. It implies `progress=False`, and the
        run swaps the status line for a one-time hint that Ctrl-C is how
        this ends. Listings and help carry the same note.
        """

        if infinite and not progress:
            # Not an error worth raising — infinite already implies it —
            # but the pair is redundant, and saying so keeps the two
            # concepts distinct: "never times" vs "never ends".
            pass
        reqs = (requires,) if isinstance(requires, str) else tuple(requires)

        def register(fn: Task) -> Task:
            key = _cli_name(name or fn.__name__)
            self._claim(key)
            fn._footman_pre = list(pre)  # type: ignore[attr-defined]
            fn._footman_post = list(post)  # type: ignore[attr-defined]
            if reqs:
                fn._footman_requires = reqs  # type: ignore[attr-defined]
            if when is not True:
                fn._footman_when = when  # type: ignore[attr-defined]
            if reason:
                fn._footman_reason = reason  # type: ignore[attr-defined]
            if not progress:
                fn._footman_progress = False  # type: ignore[attr-defined]
            if infinite:
                fn._footman_infinite = True  # type: ignore[attr-defined]
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


# The implicit root registry populated by the module-level `task`/`group`
# aliases (re-exported from `footman`). Constructing an explicit `Group` is
# always an option and keeps tests free of global state.
root = Group("root")
task = root.task
group = root.group


def reset() -> None:
    """Clear the global `root` registry (used by the test-suite)."""
    root.tasks.clear()
    root.groups.clear()


def _importable(module: str) -> bool:
    """True if *module* is importable, via `find_spec`.

    `find_spec` doesn't import the module itself, but a dotted name imports its
    parent packages to locate the child — so a parent whose `__init__` raises
    (any exception, not just ImportError/ValueError) must read as
    not-importable, never crash `fm --list` with a traceback.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def wants_progress(fn: Task) -> bool:
    """Whether *fn* consented to timing: `@task(progress=False)` opts out,
    and `infinite=True` implies it — a duration that never arrives is not
    history."""
    if getattr(fn, "_footman_infinite", False):
        return False
    return getattr(fn, "_footman_progress", True) is not False


def is_infinite(fn: Task) -> bool:
    """Whether *fn* runs until stopped: `@task(infinite=True)`."""
    return getattr(fn, "_footman_infinite", False) is True


def availability(fn: Task) -> str | None:
    """The reason a task is unavailable here, or `None` if it can run.

    `requires` modules are checked with `find_spec` (no import of the module
    itself, though a dotted name imports its parent packages); a callable
    `when` is evaluated *live* — never from the cached manifest — so
    `DOCKER_HOST=… fm up` works the moment the environment does. A predicate
    that raises reads as unavailable with the exception named (a broken
    predicate must not grant availability); likewise a `requires` parent whose
    import raises reads as unavailable, never a crash.
    """
    custom = getattr(fn, "_footman_reason", "")

    missing = [m for m in getattr(fn, "_footman_requires", ()) if not _importable(m)]
    if missing:
        return custom or f"requires {', '.join(missing)}"

    when = getattr(fn, "_footman_when", True)
    if when is True:
        return None
    reason = custom or "condition not met"
    if callable(when):
        try:
            ok = bool(when())
        except Exception as exc:
            return f"{reason} (when() raised {type(exc).__name__}: {exc})"
        return None if ok else reason
    return None if when else reason


@contextlib.contextmanager
def capture() -> Iterator[Group]:
    """Redirect module-level `@task`/`group` registration into a fresh tree.

    The seam `include()` uses to import a provider module without letting its
    decorators land in the current registry: `root.tasks`/`root.groups` are
    swapped for fresh dicts for the duration and the captured tree is yielded.
    Reentrant — a provider may itself `include()` another provider.
    """
    captured = Group("root")
    saved_tasks, saved_groups = root.tasks, root.groups
    root.tasks, root.groups = captured.tasks, captured.groups
    try:
        yield captured
    finally:
        root.tasks, root.groups = saved_tasks, saved_groups
