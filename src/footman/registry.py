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
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from typing import Any, ParamSpec, Protocol, TypeVar, cast, overload

Task = Callable[..., Any]
Finalizer = Callable[["Tasks"], object]
"""A `@finalize` hook: edits the merged command tree in place at discovery."""


class RegistrationError(ValueError):
    """A task or group name collided during registration.

    Subclasses `ValueError` so existing `except ValueError` handlers keep
    working; the app layer matches this type to report a duplicate name as a
    user error rather than an import failure.
    """


def _cli_name(name: str) -> str:
    """Normalise a Python identifier to its command-line spelling."""
    return name.replace("_", "-")


def _empty_body(fn: object) -> bool:
    """True when *fn*'s body is only a docstring and/or `pass`.

    This is the signal that a `@group.default` fans out the group's own tasks
    rather than running a body of its own. Source that can't be read (a C
    function, a REPL definition) reads as *not* empty — a body we can't see is
    treated as one we must run.
    """
    import ast
    import inspect
    import textwrap

    try:
        src = textwrap.dedent(inspect.getsource(fn))  # type: ignore[arg-type]
        mod = ast.parse(src)
    except (OSError, TypeError, SyntaxError):
        return False
    func = mod.body[0] if mod.body else None
    if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    stmts = func.body
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]  # drop the docstring
    return all(isinstance(s, ast.Pass) for s in stmts)


# The orchestration options `.opts()` can override, mapped to their task
# attribute. These are *policy* (how a task runs), kept separate from the task's
# own parameters (the *work*) — which is why they ride in `.opts()` rather than
# the call, mirroring tools' `.opts()`.
_OPTS_ATTRS = {
    "keep_going": "_footman_keep_going",
    "atomic": "_footman_atomic",
    "interactive": "_footman_interactive",
    "progress": "_footman_progress",
    "confirm": "_footman_confirm",
    "infinite": "_footman_infinite",
}


def _opts_overrides(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Validate `.opts()` kwargs and map them to their task attributes."""
    unknown = sorted(set(kwargs) - set(_OPTS_ATTRS))
    if unknown:
        valid = ", ".join(sorted(_OPTS_ATTRS))
        raise TypeError(
            f".opts() got unknown option(s) {unknown}; valid options are {valid}. "
            f"A task's own parameters go in the call — `t.opts(atomic=True)(x=1)` — "
            f"not in .opts()."
        )
    for name, value in kwargs.items():
        # Override values key the DAG's dedup identity, so they must be hashable.
        # Every real policy value is (bool / str / None); this turns a stray
        # unhashable one into a clear error here, not a cryptic crash at DAG build.
        try:
            hash(value)
        except TypeError:
            raise TypeError(
                f".opts({name}=…) needs a hashable value — options key the run's "
                f"deduplication — but got an unhashable {type(value).__name__}"
            ) from None
    return {_OPTS_ATTRS[k]: v for k, v in kwargs.items()}


class _Opted:
    """A task (or runnable group) reference carrying per-use option overrides.

    `lint.opts(atomic=True)` reads as a task everywhere a bare task does — a
    `pre=`/`post=` target, a body call — but reports the overridden `_footman_*`
    options *for this use*, leaving the registered task untouched. It proxies the
    base transparently: same signature (via `__wrapped__`), same name, same call;
    only the overridden options differ. This is footman's policy-vs-work split —
    the options ride beside the call, not inside its argument list — mirroring
    the `.opts()` on `tools.*`.
    """

    _opted_base: Task | Group
    _opted_overrides: dict[str, Any]

    def __init__(self, base: Task | Group, overrides: dict[str, Any]) -> None:
        object.__setattr__(self, "_opted_base", base)
        object.__setattr__(self, "_opted_overrides", overrides)
        object.__setattr__(self, "__wrapped__", base)  # inspect.signature follows

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_opted_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_opted_base"), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return object.__getattribute__(self, "_opted_base")(*args, **kwargs)

    def opts(self, **overrides: Any) -> _Opted:
        base = object.__getattribute__(self, "_opted_base")
        merged = dict(object.__getattribute__(self, "_opted_overrides"))
        merged.update(_opts_overrides(overrides))  # a later .opts() wins
        return _Opted(base, merged)

    def _dedup_key(self) -> tuple[int, frozenset[tuple[str, Any]]]:
        """This override's identity for DAG deduplication: the base task plus its
        frozen overrides — the same `(id, frozenset)` shape a bare task uses, so
        an empty `.opts()` collapses onto the bare task and identical overrides
        share a node (a shared prerequisite still runs once). A different policy
        is a distinct node, a genuinely different run. Values are hashable by
        construction — `_opts_overrides` rejects an unhashable one at call time.
        The proxy's internals stay behind this method, so the scheduler never
        reaches into them for identity. (`.opts()` never nests — it merges onto
        the base — so `_opted_base` is always the ultimate task, never `_Opted`.)"""
        base = object.__getattribute__(self, "_opted_base")
        overrides = object.__getattribute__(self, "_opted_overrides")
        return (id(base), frozenset(overrides.items()))


_P = ParamSpec("_P")
_R_co = TypeVar("_R_co", covariant=True)


class TaskFn(Protocol[_P, _R_co]):
    """The static type of a `@task`-decorated function: callable with the task's
    *own* signature (parameters and return type forwarded through the `ParamSpec`),
    plus `.opts()` for per-use option overrides. The `_footman_*` markers ride as
    dynamic attributes (read through `getattr`), so they need no declaration here.
    """

    __name__: str

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _R_co: ...
    def opts(self, **overrides: Any) -> _Opted: ...


class Group:
    """A node in the command tree: named tasks and nested sub-groups."""

    def __init__(self, name: str, help: str = "") -> None:
        self.name = name
        self.help = help
        self.tasks: dict[str, Task] = {}
        self.groups: dict[str, Group] = {}
        self.default_task: Task | None = None  # runs on a bare `fm <group>`
        self.finalizers: list[Finalizer] = []  # @finalize hooks (root registry only)

    def _claim(self, key: str) -> None:
        where = f"group {self.name!r}" if self.name != "root" else "the root"
        if key in self.tasks:
            raise RegistrationError(f"{where} already has a task named {key!r}")
        if key in self.groups:
            raise RegistrationError(f"{where} already has a group named {key!r}")

    @overload
    def task(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]: ...
    @overload
    def task(
        self,
        fn: None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
    ) -> Callable[[Callable[_P, _R_co]], TaskFn[_P, _R_co]]: ...

    def task(
        self,
        fn: Task | None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
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

        Availability gating lives in the `@requires` decorators — stack
        `@requires`, `@requires_dep`, `@requires_tool`, or `@requires_env`
        above `@task` to list a task as unavailable (with a reason) where it
        can't run, rather than hide it. To hide a task entirely, use plain
        Python: `if sys.platform == "darwin": @task ...`

        `progress=False` marks a task whose duration has no rhyme or
        reason (a REPL, a watcher, a network fetch): any run containing it
        never records timing history and never shows a determinate
        progress bar — the indeterminate pulse still does.

        `infinite=True` marks a task that runs until *stopped* — a dev
        server, a follow-mode tail. It implies `progress=False`, and the
        run swaps the status line for a one-time hint that Ctrl-C is how
        this ends. Listings and help carry the same note.

        `confirm="…"` gates the task on a yes/no answer asked *before* the
        task and its prerequisites run — deny and the task (and its
        subtree) is skipped; `--yes` auto-answers it. `interactive=True`
        hands the task the real terminal — no output capture, sole stdio —
        so its body can prompt or run a REPL; it can't run under `--json`, and
        because it owns the terminal, a run that contains an interactive task
        goes fully sequential — that task and everything else, one at a time.
        """

        if infinite and not progress:
            # Not an error worth raising — infinite already implies it —
            # but the pair is redundant, and saying so keeps the two
            # concepts distinct: "never times" vs "never ends".
            pass

        def register(fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
            key = _cli_name(name or fn.__name__)
            self._claim(key)
            fn._footman_pre = list(pre)  # type: ignore[attr-defined]
            fn._footman_post = list(post)  # type: ignore[attr-defined]
            if not progress:
                fn._footman_progress = False  # type: ignore[attr-defined]
            if infinite:
                fn._footman_infinite = True  # type: ignore[attr-defined]
            if confirm:
                fn._footman_confirm = confirm  # type: ignore[attr-defined]
            if interactive:
                fn._footman_interactive = True  # type: ignore[attr-defined]
            if keep_going is not None:
                fn._footman_keep_going = keep_going  # type: ignore[attr-defined]
            if atomic:
                fn._footman_atomic = True  # type: ignore[attr-defined]
            fn.opts = lambda **o: _Opted(fn, _opts_overrides(o))  # type: ignore[attr-defined]
            self.tasks[key] = fn
            return cast("TaskFn[_P, _R_co]", fn)

        return register(fn) if fn is not None else register

    def group(self, name: str, help: str = "") -> Group:
        """Create and register a nested command group, returning it."""
        key = _cli_name(name)
        self._claim(key)
        sub = Group(key, help)
        self.groups[key] = sub
        return sub

    def finalize(self, fn: Finalizer) -> Finalizer:
        """Register a hook that edits the discovered command tree in place.

        Every `@finalize` function runs once, after the whole `tasks.py` cascade
        is assembled but before dispatch, handed a `Tasks` view of the merged
        tree. Its edits are part of the plan, never a runtime surprise: an added
        `pre` runs and shows in `--dry-run`, a disabled task drops from listings
        and completion. It is footman's `collection_modifyitems`.

        Finalizers run in cascade order — root's first, the folder nearest your
        cwd last, each seeing the previous ones' edits — the same "local overrides
        global" precedence the task cascade itself uses. Read and edit each task
        through the `TaskView` surface, never the private `_footman_*` attributes.

            @footman.finalize
            def gate_deploys(tasks):
                audit = tasks["audit"]
                for t in tasks:
                    if t.name.startswith("deploy"):
                        t.add_pre(audit)
        """
        self.finalizers.append(fn)
        return fn

    def default(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
        """Register *fn* as this group's default action — what a bare
        `fm <group>` runs, and what the group returns when called.

        The function's signature *is* the group's option surface, so it takes
        flags/options only: a positional parameter is rejected at load time,
        because a bare word after a group names a child, not a value. Model a
        positional action as a task, or take free arguments via `--` passthrough.
        """
        # Lazy: manifest imports registry, so importing it at module load would
        # cycle. By call time (a tasks file being imported) it resolves fine.
        from footman.context import context_param_name
        from footman.manifest import param_spec, resolved_signature

        sig = resolved_signature(fn)
        ctx_name = context_param_name(sig)
        for param in sig.parameters.values():
            if param.name == ctx_name:
                continue
            if param_spec(param).get("kind") in ("argument", "variadic"):
                where = self.name if self.name != "root" else "the root group"
                raise RegistrationError(
                    f"{where}'s default {fn.__name__!r} takes a positional "
                    f"parameter ({param.name!r}); a group default takes "
                    f"flags/options only — a bare word after a group names a "
                    f"child. Model a positional action as a task, or take free "
                    f"arguments via `--` passthrough."
                )
        # A back-reference plus the empty-body flag: an empty-body default fans
        # out the group's own tasks (they become its implicit prerequisites at
        # DAG-build time); a custom body is the escape hatch and runs as written.
        fn._footman_default_group = self  # type: ignore[attr-defined]
        fn._footman_default_fanout = _empty_body(fn)  # type: ignore[attr-defined]
        self.default_task = fn
        return cast("TaskFn[_P, _R_co]", fn)

    def opts(self, **overrides: Any) -> _Opted:
        """Per-use option overrides for this group's default action, the same
        `.opts()` a task has — `pre=[lint.opts(keep_going=True)]`. Overrides ride
        the group's default when it runs (bare, as a `pre=`, or called)."""
        return _Opted(self, _opts_overrides(overrides))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Run this group's default action — the imperative mirror of a bare
        `fm <group>` and of `pre=[group]`.

        A runnable group (one with an `@group.default`) is callable from a task
        body the way a task is: a `check` task can call `lint(fix=fix)`. It runs
        the default's action synchronously and in order — a custom body as
        written, or, for an empty-body default, the group's own tasks, each
        handed the arguments it declares (partial reach, by name). Like every
        body call it forwards arguments explicitly and runs to completion before
        the next statement; prerequisites and parallelism stay the scheduler's
        job — reach for a real chain, `pre=`, or `parallel()` for those.
        """
        if self.default_task is None:
            raise TypeError(
                f"group {self.name!r} is not runnable: it has no "
                f"@{self.name}.default, so there is no action to call. Add a "
                f"default action, or call a task inside the group directly."
            )
        if not getattr(self.default_task, "_footman_default_fanout", False):
            return self.default_task(*args, **kwargs)  # custom body: as written
        # Empty-body default: fan out the group's own tasks, handing each only
        # the arguments it declares — the imperative echo of `fm <group>`.
        # Sequential, like any body call; wrap the call in parallel() to overlap.
        from footman.manifest import resolved_signature

        for child in self.tasks.values():
            accepts = set(resolved_signature(child).parameters)
            child(**{k: v for k, v in kwargs.items() if k in accepts})
        return None


# The implicit root registry populated by the module-level `task`/`group`
# aliases (re-exported from `footman`). Constructing an explicit `Group` is
# always an option and keeps tests free of global state.
root = Group("root")
task = root.task
group = root.group
finalize = root.finalize


def reset() -> None:
    """Clear the global `root` registry (used by the test-suite)."""
    root.tasks.clear()
    root.groups.clear()
    root.finalizers.clear()


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
    and `infinite=True`/`interactive=True` imply it — a duration that never
    arrives, or one spent waiting on a human, is not history."""
    if getattr(fn, "_footman_infinite", False):
        return False
    if getattr(fn, "_footman_interactive", False):
        return False
    return getattr(fn, "_footman_progress", True) is not False


def is_infinite(fn: Task) -> bool:
    """Whether *fn* runs until stopped: `@task(infinite=True)`."""
    return getattr(fn, "_footman_infinite", False) is True


def is_interactive(fn: Task) -> bool:
    """Whether *fn* owns the real terminal: `@task(interactive=True)` — no
    output capture, sole stdio, so its body may prompt or run a REPL."""
    return getattr(fn, "_footman_interactive", False) is True


def keeps_going(fn: Task) -> bool | None:
    """*fn*'s declared failure policy: `@task(keep_going=True/False)`, or `None`
    when it left the choice to the command line / the built-in default."""
    return getattr(fn, "_footman_keep_going", None)


def is_atomic(fn: Task) -> bool:
    """Whether *fn*'s subprocesses opt out of fail-fast's kill:
    `@task(atomic=True)` — they run to completion rather than be cut off."""
    return getattr(fn, "_footman_atomic", False) is True


def task_confirm(fn: Task) -> str:
    """The `@task(confirm="…")` prompt gating this task, or `""` if none."""
    return getattr(fn, "_footman_confirm", "")


Check = Callable[[], str | None]
"""One availability gate: the reason it fails, or `None` when it passes."""


def _gate(check: Check) -> Callable[[Task], Task]:
    """Stack *check* onto a task's availability gates, read live by `availability`."""

    def decorate(fn: Task) -> Task:
        fn._footman_checks = [  # type: ignore[attr-defined]
            *getattr(fn, "_footman_checks", ()),
            check,
        ]
        return fn

    return decorate


def requires(
    predicate: Callable[[], object], *, reason: str = ""
) -> Callable[[Task], Task]:
    """Gate a task on a live *predicate* — available only while it is truthy.

    The generic gate the three specialisations build on. A predicate that
    raises reads as unavailable, the exception named:

    ```python
    @task
    @requires(lambda: Path("config.toml").exists(), reason="needs config.toml")
    def publish(): ...
    ```
    """

    def check() -> str | None:
        try:
            ok = predicate()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return f"{reason} ({detail})" if reason else detail
        return None if ok else (reason or "unavailable here")

    return _gate(check)


def requires_dep(*modules: str, reason: str = "") -> Callable[[Task], Task]:
    """Gate a task on Python *modules* being importable (`find_spec`, no import).

    Keep the real `import` in the body; this only checks availability, so a
    missing optional dependency lists as a clean reason, never an import crash.
    """

    def check() -> str | None:
        missing = [m for m in modules if not _importable(m)]
        if not missing:
            return None
        return reason or f"requires {', '.join(missing)}"

    return _gate(check)


def requires_tool(*commands: str, reason: str = "") -> Callable[[Task], Task]:
    """Gate a task on command-line tools being on `PATH` (`shutil.which`)."""

    def check() -> str | None:
        missing = [c for c in commands if shutil.which(c) is None]
        if not missing:
            return None
        return reason or f"requires {', '.join(missing)} on PATH"

    return _gate(check)


def requires_env(*names: str, reason: str = "") -> Callable[[Task], Task]:
    """Gate a task on environment variables being set (`in os.environ`)."""

    def check() -> str | None:
        missing = [v for v in names if v not in os.environ]
        if not missing:
            return None
        return reason or f"set {', '.join(missing)}"

    return _gate(check)


def availability(fn: Task) -> str | None:
    """The reason(s) a task is unavailable here, or `None` if it can run.

    Every `@requires` gate on the task is evaluated **live** — never from the
    cached manifest, so `DOCKER_HOST=… fm up` works the moment the environment
    does — and **all** failures are collected, each in its own words, so a task
    gated on both a missing tool and a missing variable says both. A gate whose
    predicate raises reads as unavailable with the exception named, scoped to
    that one gate.
    """
    reasons = [
        r for check in getattr(fn, "_footman_checks", ()) if (r := check()) is not None
    ]
    return "; ".join(reasons) if reasons else None


def _as_fn(t: TaskView | Task) -> Task:
    """Unwrap a `TaskView` to its function; pass a raw function through."""
    return t.fn if isinstance(t, TaskView) else t


class TaskView:
    """A finalizer's handle on one task: read its wiring and edit it here,
    never through the private `_footman_*` attributes."""

    def __init__(self, fn: Task, name: str) -> None:
        self.fn = fn
        """The task function itself — the escape hatch past the view."""
        self.name = name
        """The task's command-line name, e.g. `deploy-web`."""

    @property
    def pre(self) -> tuple[Task, ...]:
        """The prerequisites that run before this task."""
        return tuple(getattr(self.fn, "_footman_pre", ()))

    @property
    def post(self) -> tuple[Task, ...]:
        """The tasks that run after this one."""
        return tuple(getattr(self.fn, "_footman_post", ()))

    @property
    def disabled(self) -> str | None:
        """Why the task is unavailable here, or `None` if it can run."""
        return availability(self.fn)

    def add_pre(self, *tasks: TaskView | Task) -> None:
        """Prepend prerequisites (views or functions), skipping any already set."""
        have = list(getattr(self.fn, "_footman_pre", []))
        self.fn._footman_pre = [  # type: ignore[attr-defined]
            *(f for t in tasks if (f := _as_fn(t)) not in have),
            *have,
        ]

    def add_post(self, *tasks: TaskView | Task) -> None:
        """Append post-tasks (views or functions), skipping any already set."""
        have = list(getattr(self.fn, "_footman_post", []))
        self.fn._footman_post = [  # type: ignore[attr-defined]
            *have,
            *(f for t in tasks if (f := _as_fn(t)) not in have),
        ]

    def disable(self, reason: str) -> None:
        """Mark the task unavailable — listed with *reason*, refused if run."""
        _gate(lambda: reason)(self.fn)


class Tasks:
    """A finalizer's view of the merged command tree: iterate every task, or
    look one up by its command-line name, each as a `TaskView`."""

    def __init__(self, root: Group) -> None:
        self._root = root

    def __iter__(self) -> Iterator[TaskView]:
        yield from _task_views(self._root)

    def get(self, name: str) -> TaskView | None:
        """The task named *name* (command-line spelling), or `None`."""
        return next((v for v in self if v.name == name), None)

    def __getitem__(self, name: str) -> TaskView:
        if (view := self.get(name)) is None:
            raise KeyError(name)
        return view

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None


def _task_views(g: Group) -> Iterator[TaskView]:
    for name, fn in g.tasks.items():
        yield TaskView(fn, name)
    for sub in g.groups.values():
        yield from _task_views(sub)


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
    saved_finalizers = root.finalizers
    root.tasks, root.groups = captured.tasks, captured.groups
    root.finalizers = captured.finalizers
    try:
        yield captured
    finally:
        root.tasks, root.groups = saved_tasks, saved_groups
        root.finalizers = saved_finalizers
