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
import functools
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, ParamSpec, Protocol, TypeVar, cast, overload

Task = Callable[..., Any]
Finalizer = Callable[["Tasks"], object]
"""A `@finalize` hook: edits the merged command tree in place at discovery."""

# The hook kinds a provider module can contribute alongside (or instead of)
# tasks. Each kind is one bucket in `Group.contributions`, and the whole
# carriage — `capture()`/`reset()` here, `_fork`/`_pull` in compose,
# `load_tree`'s collection in discover — walks the dict generically, so a
# future hook kind is one entry here plus its own run semantics.
CONTRIBUTION_KINDS: tuple[str, ...] = ("finalize",)

# A task stays a plain function; its metadata rides as `_footman_*` attributes.
# These name every key in one place, so the strings appear once and the read
# accessors below are the one way the rest of the framework touches them.
_PRE = "_footman_pre"
_POST = "_footman_post"
_KEEP_GOING = "_footman_keep_going"
_ATOMIC = "_footman_atomic"
_INFINITE = "_footman_infinite"
_HIDDEN = "_footman_hidden"
_INTERACTIVE = "_footman_interactive"
_PROGRESS = "_footman_progress"
_CONFIRM = "_footman_confirm"
_PULLED = "_footman_pulled_from"  # provenance: the plugin identity a pull stamped
_CWD = "_footman_cwd"
_REL = "_footman_rel"
_SERIAL = "_footman_serial"
_EXCLUSIVE = "_footman_exclusive"

# The cwd policy tokens — where a task's working directory roots. Anything
# else passed as `cwd=` must be an absolute path (a relative one is a taught
# error pointing at `rel=`, so base-vs-suffix stays unambiguous).
CWD_TOKENS = ("root", "taskfile", "asinvoked", "unmanaged")


def _validate_cwd(value: str | Path) -> str | Path:
    """A cwd policy value: one of `CWD_TOKENS`, or an absolute path."""
    if isinstance(value, str) and value in CWD_TOKENS:
        return value
    path = Path(value)
    if not path.is_absolute():
        raise TypeError(
            f"cwd={str(value)!r} is relative — cwd takes a policy token "
            f"({', '.join(CWD_TOKENS)}) or an absolute path; a relative "
            f"suffix goes in rel=…"
        )
    return path


def _validate_rel(value: str | Path) -> str:
    """A rel suffix: a relative path, appended to the resolved cwd base.

    Anchored counts as absolute: on Windows a driveless-rooted path
    (`/x` — `is_absolute()` False, `anchor` set) would silently replace
    the base's whole path portion when joined, the opposite of a suffix."""
    rel_path = Path(value)
    if rel_path.is_absolute() or rel_path.anchor:
        raise TypeError(
            f"rel={str(value)!r} is absolute — rel is a suffix appended to the "
            f"resolved cwd base; an absolute directory goes in cwd=…"
        )
    return str(value)


_CHECKS = "_footman_checks"
_DEFAULT_GROUP = "_footman_default_group"
_DEFAULT_FANOUT = "_footman_default_fanout"


class RegistrationError(ValueError):
    """A task or group name collided during registration.

    Subclasses `ValueError` so existing `except ValueError` handlers keep
    working; the app layer matches this type to report a duplicate name as a
    user error rather than an import failure.
    """


def cli_name(name: str) -> str:
    """Normalise a Python identifier to its command-line spelling.

    A *trailing* underscore is Python's keyword/name-escape idiom (`sync_` to
    avoid shadowing `sync`, `import_`, `class_`); it is stripped, so the flag
    reads `--sync`, not `--sync-`. This is the one place identifiers become CLI
    tokens for task names, group names, *and* parameter flags — the `tools.*`
    bridge already strips the same way, and routing every mapping through here
    keeps the two from drifting apart again.
    """
    return name.rstrip("_").replace("_", "-")


def _empty_body(fn: object) -> bool:
    """True when *fn*'s body is only a docstring and/or `pass`.

    This is the signal that a `@group.default` fans out the group's own tasks
    rather than running a body of its own. Source that can't be read (a C
    function, a REPL definition) reads as *not* empty — a body we can't see is
    treated as one we must run.
    """
    import ast
    import textwrap

    try:
        src = textwrap.dedent(task_source(fn))
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
    "keep_going": _KEEP_GOING,
    "atomic": _ATOMIC,
    "interactive": _INTERACTIVE,
    "progress": _PROGRESS,
    "confirm": _CONFIRM,
    "infinite": _INFINITE,
    "cwd": _CWD,
    "rel": _REL,
    "serial": _SERIAL,
    "exclusive": _EXCLUSIVE,
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
    if "cwd" in kwargs:
        kwargs["cwd"] = _validate_cwd(kwargs["cwd"])
    if "rel" in kwargs:
        kwargs["rel"] = _validate_rel(kwargs["rel"])
    if kwargs.get("cwd") == "unmanaged" and kwargs.get("rel"):
        raise TypeError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "use cwd='asinvoked' for a pinned launch-directory base"
        )
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
        base = object.__getattribute__(self, "_opted_base")
        overrides = object.__getattribute__(self, "_opted_overrides")
        if _CWD in overrides or _REL in overrides:
            # A direct body-call must honour a cwd/rel override: resolve it
            # against the caller's context and install it as `ctx.cwd` around
            # the base invocation — a save/restore of the *field*, never a
            # process chdir. The other options are scheduler-read and inert
            # on a plain call. Lazy import: executor imports registry.
            from footman import executor
            from footman.context import current

            ctx = current()
            saved, saved_unmanaged = ctx.cwd, ctx.cwd_unmanaged
            ctx.cwd = None  # let the override's ladder re-resolve
            ctx.cwd, ctx.cwd_unmanaged = executor.resolve_cwd(self, ctx)
            try:
                return base(*args, **kwargs)
            finally:
                ctx.cwd, ctx.cwd_unmanaged = saved, saved_unmanaged
        return base(*args, **kwargs)

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


class _TaskFn:
    """The object `@task` returns — and the very same object it registers.

    A task's metadata rides as `_footman_*` attributes, and the framework keys
    a great deal on the *identity* of the task object: the DAG's dedup key
    (`schedule._dep_key`), the cascade's "am I shadowing myself?" test, the
    provenance and defining-directory stamps. So there is exactly **one**
    handle per decoration, and it is both registered and returned — a second
    handle over the same function would read as a different task.

    It is deliberately thin. Attribute reads fall through to the function, so
    a marker stamped *before* wrapping (`@requires` written below `@task`) is
    still read back; `functools.update_wrapper` gives it the function's name,
    docstring, and `__wrapped__`, so `inspect.signature`, `inspect.getdoc` and
    `inspect.unwrap` all answer about the function. The two `inspect` readers
    that don't follow `__wrapped__` are wrapped once in `task_source` /
    `task_source_file` below, so no call site carries the caveat.
    """

    def __init__(self, fn: Callable[..., Any]) -> None:
        # Copies __name__/__qualname__/__doc__/__module__/__annotations__ and
        # the function's __dict__ (markers stamped below @task), and sets
        # __wrapped__ — the seam every unwrapping reader follows.
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.__wrapped__(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Reached only for what neither the instance nor the class carries:
        # the function's own dunders (`__code__`, `__globals__`) and any
        # attribute set on it after wrapping. Read `__wrapped__` out of the
        # instance dict directly — going through `self` would recurse here
        # while `__init__` is still running.
        try:
            fn = object.__getattribute__(self, "__dict__")["__wrapped__"]
        except KeyError:
            raise AttributeError(name) from None
        return getattr(fn, name)

    def opts(self, **overrides: Any) -> _Opted:
        """Per-use option overrides — `lint.opts(keep_going=True)`. The base is
        this handle, so an opted reference and a bare one agree about which
        task they name (the DAG's dedup key reads `id(base)`)."""
        return _Opted(self, _opts_overrides(overrides))

    def __repr__(self) -> str:
        return f"<task {getattr(self, '__name__', '?')}>"


# `inspect`, told about task handles. Everything else in `inspect` follows
# `__wrapped__` on its own; these two resolve a *file and line*, which a
# handle doesn't have, so they unwrap to the function first. Wrapped here so
# reading a task's source stays a one-liner wherever it's needed.


def task_source(fn: Any) -> str:
    """The source text of the function behind *fn* (decorators included)."""
    import inspect

    return inspect.getsource(inspect.unwrap(fn))


def task_source_file(fn: Any) -> str | None:
    """The file the function behind *fn* is defined in, if it has one."""
    import inspect

    return inspect.getsourcefile(inspect.unwrap(fn))


def _apply_policy(
    fn: Task,
    *,
    pre: Sequence[Task],
    post: Sequence[Task],
    progress: bool,
    infinite: bool,
    confirm: str,
    interactive: bool,
    keep_going: bool | None,
    atomic: bool,
    cwd: str | Path = "",
    rel: str | Path = "",
    serial: bool = False,
    exclusive: bool = False,
    hidden: bool | None = None,
) -> None:
    """Stamp a task's `_footman_*` policy attributes onto *fn*.

    The single writer of the orchestration markers, shared by `@task` and
    `@group.default` so the two option surfaces cannot drift apart. Only the
    non-default markers are set, so `getattr(fn, _…, default)` reads elsewhere
    stay the source of truth; `pre`/`post` are always written (as lists) so a
    later mutation edits a list this task owns.
    """
    setattr(fn, _PRE, list(pre))
    setattr(fn, _POST, list(post))
    if not progress:
        setattr(fn, _PROGRESS, False)
    if infinite:
        setattr(fn, _INFINITE, True)
    if hidden is not None:
        # Tri-state on purpose: unset inherits the enclosing group's answer,
        # so `hidden=False` on a child of a hidden group is a real override
        # rather than indistinguishable from silence.
        setattr(fn, _HIDDEN, hidden)
    if confirm:
        setattr(fn, _CONFIRM, confirm)
    if interactive:
        setattr(fn, _INTERACTIVE, True)
    if keep_going is not None:
        setattr(fn, _KEEP_GOING, keep_going)
    if atomic:
        setattr(fn, _ATOMIC, True)
    if cwd == "unmanaged" and rel:
        raise TypeError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "use cwd='asinvoked' for a pinned launch-directory base"
        )
    if cwd:
        setattr(fn, _CWD, _validate_cwd(cwd))
    if rel:
        setattr(fn, _REL, _validate_rel(rel))
    if serial:
        setattr(fn, _SERIAL, True)
    if exclusive:
        setattr(fn, _EXCLUSIVE, True)


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

    def __init__(self, name: str, help: str = "", hidden: bool | None = None) -> None:
        self.name = name
        self.help = help
        # Tri-state, like a task's: unset inherits the enclosing group's
        # answer, so a hidden subtree needs saying once at its root and a
        # child can still opt back into the listings with `hidden=False`.
        self.hidden = hidden
        self.tasks: dict[str, Task] = {}
        self.groups: dict[str, Group] = {}
        # Lifecycle contributions, one bucket per hook kind (root registry
        # only). `@finalize` hooks are the only kind today.
        self.contributions: dict[str, list[Callable[..., object]]] = {
            kind: [] for kind in CONTRIBUTION_KINDS
        }
        # Provenance: the plugin identity that pulled this group in, or None
        # for a locally-defined one. Collision messages cite it, `--plugins`
        # reports it, and "local silently wins" is decided by it.
        self.pulled_from: str | None = None

    @property
    def default_task(self) -> Task | None:
        """The default action — what a bare `fm <group>` runs.

        Derived, never stored: the default *is* the child task named
        `default` (`@group.default` ↔ `fm lint.default`), so default-ness
        has one spelling, survives forks and grafts by construction, and
        there is no pointer to desync.
        """
        return self.tasks.get("default")

    def _stamp_default(self, fn: Task, interactive: bool) -> None:
        """The default-action validations and markers, one code path for
        every way a task can come to be named `default` — the decorator,
        `@task(name="default")`, or a pull landing one here."""
        fanout = _empty_body(fn)
        where = self.name if self.name != "root" else "the root group"
        if interactive and fanout:
            raise RegistrationError(
                f"{where}'s default {fn.__name__!r} is interactive but has "
                f"an empty body, so it fans the group's tasks out in "
                f"parallel — there is no single body to own the terminal. "
                f"Give it a real body, or drop interactive."
            )
        # A back-reference plus the empty-body flag: an empty-body default
        # fans out the group's own tasks (implicit prerequisites at DAG-build
        # time); a custom body is the escape hatch and runs as written.
        setattr(fn, _DEFAULT_GROUP, self)
        setattr(fn, _DEFAULT_FANOUT, fanout)

    def _claim(self, key: str) -> None:
        where = f"group {self.name!r}" if self.name != "root" else "the root"
        # `.` is the address separator (`fm docs.serve`), so a name containing
        # one would alias into fake nesting or become unreachable; whitespace
        # can never survive shell word-splitting. Refuse both at load time.
        if "." in key or any(c.isspace() for c in key):
            raise RegistrationError(
                f"{where}: {key!r} is not a legal name — '.' is the address "
                f"separator and spaces cannot be addressed; use '_' or '-' "
                f"inside a name, or nest a group instead"
            )
        if key in self.tasks:
            raise RegistrationError(f"{where} already has a task named {key!r}")
        if key in self.groups:
            raise RegistrationError(f"{where} already has a group named {key!r}")

    def _shadow_pulled(self, key: str) -> None:
        """Make way for a *local* definition of *key*: a pulled entry yields
        silently, whatever the file order — the cascade's "user names shadow
        plugins" principle, carried by provenance instead of ordering.
        Local-vs-local stays loud in `_claim`."""
        existing_task = self.tasks.get(key)
        if existing_task is not None and pulled_from(existing_task) is not None:
            del self.tasks[key]
        existing_group = self.groups.get(key)
        if existing_group is not None and existing_group.pulled_from is not None:
            del self.groups[key]

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
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
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
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
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
        can't run, rather than hide it.

        Three different things, worth keeping apart:

        * `hidden=True` — **listed nowhere, callable as ever.** The task
          drops out of `--list`, `--tree`, group help, the did-you-mean
          index and completion, while `fm <name>` runs it exactly as
          before. For the tasks a machine calls and a human never types: a
          CI entry point, a release step another task drives. It is
          presentation only — prerequisites still run it, a group's
          empty-body fan-out still includes it, and `--json` reports it
          *marked* rather than missing, because a machine is who calls it.
          Unset inherits the enclosing group's answer, so
          `group("internal", hidden=True)` hides a whole subtree and a
          child can still say `hidden=False`.
        * `@requires…` — listed *with a reason* it can't run here.
        * `if sys.platform == "darwin": @task ...` — plain Python, and the
          task does not **exist**: nothing to list, nothing to call, no
          address at all. Reach for it when the task is meaningless on this
          machine, not when it is merely uninteresting to type.

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

        `cwd=` roots the task's working directory: a policy token (`"root"` —
        the highest cascade file's directory, `"taskfile"` — the file the task
        was defined in (the default), `"asinvoked"` — a pinned snapshot of the
        launch directory, `"unmanaged"` — footman stays out entirely) or an
        absolute path. `rel=` appends a relative suffix to the resolved base.
        `ctx.cwd` and every `run()`/tools subprocess follow it.
        """

        if infinite and not progress:
            # Not an error worth raising — infinite already implies it —
            # but the pair is redundant, and saying so keeps the two
            # concepts distinct: "never times" vs "never ends".
            pass

        def register(fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
            key = cli_name(name or fn.__name__)
            self._shadow_pulled(key)
            self._claim(key)
            task = _TaskFn(fn)  # one handle: registered *and* returned
            if key == "default":
                # The name *is* the mechanism: any task named `default` is its
                # group's default action — `@group.default` is sugar for this.
                # One validation path, so there are no second-class defaults.
                self._stamp_default(task, interactive)
            _apply_policy(
                task,
                pre=pre,
                post=post,
                progress=progress,
                infinite=infinite,
                confirm=confirm,
                interactive=interactive,
                keep_going=keep_going,
                atomic=atomic,
                cwd=cwd,
                rel=rel,
                serial=serial,
                exclusive=exclusive,
                hidden=hidden,
            )
            self.tasks[key] = task
            return cast("TaskFn[_P, _R_co]", task)

        return register(fn) if fn is not None else register

    def group(self, name: str, help: str = "", hidden: bool | None = None) -> Group:
        """Create and register a nested command group, returning it.

        `hidden=True` keeps the whole subtree out of the human listings
        (`--list`, `--tree`, help, completion) while leaving every address in
        it callable — see `@task(hidden=…)`.
        """
        key = cli_name(name)
        if key == "default":
            # `default` is a meaningful *task* name — the group's default
            # action. A group-typed default is incoherent (a bare `fm lint`
            # resolving to another bare group — turtles), so the name is
            # refused for groups at load time.
            raise RegistrationError(
                f"a group cannot be named 'default': the name means \"this "
                f"group's default action\" and belongs to a task — declare "
                f"@{self.name}.default, or name a task 'default'"
            )
        adopted = self.groups.get(key)
        if adopted is not None and adopted.pulled_from is not None:
            # A local definition over a *pulled* group adopts it rather than
            # evicting it: claiming the name means adding to it — exactly
            # what pulling after the definition produces. Local leaves still
            # shadow pulled ones (task-level `_shadow_pulled`), so definition
            # order stops mattering: either way the union, local wins per
            # leaf, and only the listing order follows the file.
            if help:
                adopted.help = help
            if hidden is not None:
                adopted.hidden = hidden
            return adopted
        self._shadow_pulled(key)
        self._claim(key)
        sub = Group(key, help, hidden)
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
        self.contributions["finalize"].append(fn)
        return fn

    @overload
    def default(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]: ...
    @overload
    def default(
        self,
        fn: None = None,
        *,
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
    ) -> Callable[[Callable[_P, _R_co]], TaskFn[_P, _R_co]]: ...

    def default(
        self,
        fn: Task | None = None,
        *,
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
    ) -> Task | Callable[[Task], Task]:
        """Register *fn* as this group's default action — what a bare
        `fm <group>` runs, and what the group returns when called.

        Usable bare (`@group.default`) or parameterised
        (`@group.default(keep_going=True)`). It takes the same orchestration
        options as `@task` — `pre`/`post`, `progress`, `infinite`, `confirm`,
        `interactive`, `keep_going`, `atomic` — with no `name` (the group
        already names it).

        The function's signature *is* the group's whole CLI surface,
        positionals included: `fm lint src/` hands `src/` to the default the
        way any task takes an argument. A nested task always keeps its own
        dotted address (`fm lint.python`), so a bare word after the group is
        unambiguously the default's value — when a value happens to equal a
        child's name, the run carries a one-line stderr note pointing at the
        dotted spelling.

        An **empty-body** default fans the group's own tasks out in parallel, so
        `interactive=True` on one is rejected — there is no single body to own
        the terminal. Give the default a real body to make it interactive.

        The default registers as the child task named **`default`** — a
        fixed, well-known name, not the function's own (which stays private,
        so renaming it is free). The decorator you wrote is the address you
        type: `@lint.default` ↔ `fm lint.default`; bare `fm lint` stays the
        idiomatic spelling, the way `GET /` serves `/index.html`. The name is
        the mechanism — `@group.default` is sugar for a task named `default`.
        """

        def register(fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
            self._claim("default")
            task = _TaskFn(fn)  # one handle: registered *and* returned
            self._stamp_default(task, interactive)
            _apply_policy(
                task,
                pre=pre,
                post=post,
                progress=progress,
                infinite=infinite,
                confirm=confirm,
                interactive=interactive,
                keep_going=keep_going,
                atomic=atomic,
                cwd=cwd,
                rel=rel,
                serial=serial,
                exclusive=exclusive,
                hidden=hidden,
            )
            self.tasks["default"] = task
            return cast("TaskFn[_P, _R_co]", task)

        return register(fn) if fn is not None else register

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
        if not fans_out(self.default_task):
            return self.default_task(*args, **kwargs)  # custom body: as written
        # Empty-body default: fan out the group's own tasks, handing each only
        # the arguments it declares — the imperative echo of `fm <group>`.
        # Sequential, like any body call; wrap the call in parallel() to overlap.
        # The `default` child is the fan-out itself: excluded from its own set.
        from footman.manifest import resolved_signature

        for name, child in self.tasks.items():
            if name == "default":
                continue
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
    for bucket in root.contributions.values():
        bucket.clear()


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


def pre_tasks(fn: Task) -> list[Task]:
    """The prerequisites declared to run before *fn* (`@task(pre=…)`)."""
    return getattr(fn, _PRE, [])


def post_tasks(fn: Task) -> list[Task]:
    """The tasks declared to run after *fn* (`@task(post=…)`)."""
    return getattr(fn, _POST, [])


def pulled_from(node: Task | Group) -> str | None:
    """The plugin identity that pulled *node* in, or None for local code.

    Groups carry it as a real field (each graft gets fresh Group objects);
    task functions carry it as a marker attribute — fns are shared between
    forks on purpose, so the stamp is the *identity*, which is the same
    everywhere the same provider's fn lands.
    """
    if isinstance(node, Group):
        return node.pulled_from
    return getattr(node, _PULLED, None)


def default_group(fn: Task) -> Group | None:
    """The group *fn* is the `@group.default` action of, or `None`."""
    return getattr(fn, _DEFAULT_GROUP, None)


def fans_out(fn: Task) -> bool:
    """Whether *fn* is an empty-body `@group.default` that fans out its group's
    own tasks (they become its implicit prerequisites) rather than run a body."""
    return getattr(fn, _DEFAULT_FANOUT, False) is True


def wants_progress(fn: Task) -> bool:
    """Whether *fn* consented to timing: `@task(progress=False)` opts out,
    and `infinite=True`/`interactive=True` imply it — a duration that never
    arrives, or one spent waiting on a human, is not history."""
    if getattr(fn, _INFINITE, False):
        return False
    if getattr(fn, _INTERACTIVE, False):
        return False
    return getattr(fn, _PROGRESS, True) is not False


def is_infinite(fn: Task) -> bool:
    """Whether *fn* runs until stopped: `@task(infinite=True)`."""
    return getattr(fn, _INFINITE, False) is True


def declared_hidden(fn: Task) -> bool | None:
    """*fn*'s own answer to "list me?", or `None` when it never said.

    `None` is the whole point: the manifest resolves it against the enclosing
    group, so hiding a subtree is one declaration at its root and a child can
    still say `hidden=False` to come back.
    """
    value = getattr(fn, _HIDDEN, None)
    return value if value is None else bool(value)


def is_interactive(fn: Task) -> bool:
    """Whether *fn* owns the real terminal: `@task(interactive=True)` — no
    output capture, sole stdio, so its body may prompt or run a REPL."""
    return getattr(fn, _INTERACTIVE, False) is True


def keeps_going(fn: Task) -> bool | None:
    """*fn*'s declared failure policy: `@task(keep_going=True/False)`, or `None`
    when it left the choice to the command line / the built-in default."""
    return getattr(fn, _KEEP_GOING, None)


def is_atomic(fn: Task) -> bool:
    """Whether *fn*'s subprocesses opt out of fail-fast's kill:
    `@task(atomic=True)` — they run to completion rather than be cut off."""
    return getattr(fn, _ATOMIC, False) is True


def task_confirm(fn: Task) -> str:
    """The `@task(confirm="…")` prompt gating this task, or `""` if none."""
    return getattr(fn, _CONFIRM, "")


def task_cwd(fn: Task) -> str | Path | None:
    """The task's declared cwd policy — a token from `CWD_TOKENS` or an
    absolute `Path` — or `None` when undeclared (the config ladder decides)."""
    return getattr(fn, _CWD, None)


def task_rel(fn: Task) -> str | None:
    """The task's declared rel suffix (appended to the resolved cwd base)."""
    return getattr(fn, _REL, None)


def task_lane(fn: Task) -> str | None:
    """The task's declared arbiter lane: `"exclusive"` (runs with nothing
    else in flight), `"serial"` (owns the process globals, one at a time,
    overlapping the parallel pool), or `None` (the parallel regime)."""
    if getattr(fn, _EXCLUSIVE, False):
        return "exclusive"
    if getattr(fn, _SERIAL, False):
        return "serial"
    return None


Check = Callable[[], str | None]
"""One availability gate: the reason it fails, or `None` when it passes."""


def _gate(check: Check) -> Callable[[Task], Task]:
    """Stack *check* onto a task's availability gates, read live by `availability`."""

    def decorate(fn: Task) -> Task:
        setattr(fn, _CHECKS, [*getattr(fn, _CHECKS, ()), check])
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
    reasons = [r for check in getattr(fn, _CHECKS, ()) if (r := check()) is not None]
    return "; ".join(reasons) if reasons else None


def _as_fn(t: TaskView | Task) -> Task:
    """Unwrap a `TaskView` to its function; pass a raw function through."""
    return t.fn if isinstance(t, TaskView) else t


class TaskView:
    """A finalizer's handle on one task: read its wiring, its policy flags, and
    its cascade provenance (where it was defined, what it overrode), and edit it
    here — never through the private `_footman_*` attributes."""

    def __init__(self, fn: Task, name: str, group: Group | None = None) -> None:
        self.fn = fn
        """The task function itself — the escape hatch past the view."""
        self.name = name
        """The task's command-line name, e.g. `deploy-web`."""
        self.group = group
        """The group this task lives in, or `None` for a top-level task — its
        `.name` is the group's command-line spelling (e.g. `docs`). Use it to
        disambiguate two tasks that share a leaf name across groups."""

    @property
    def pre(self) -> tuple[Task, ...]:
        """The prerequisites that run before this task."""
        return tuple(pre_tasks(self.fn))

    @property
    def post(self) -> tuple[Task, ...]:
        """The tasks that run after this one."""
        return tuple(post_tasks(self.fn))

    @property
    def disabled(self) -> str | None:
        """Why the task is unavailable here, or `None` if it can run."""
        return availability(self.fn)

    # Policy flags (read-only — declared at decoration).

    @property
    def keep_going(self) -> bool | None:
        """The task's declared failure policy (`@task(keep_going=…)`), or `None`
        when it left the choice to the command line / the built-in default."""
        return keeps_going(self.fn)

    @property
    def atomic(self) -> bool:
        """Whether the task's subprocesses opt out of fail-fast's kill."""
        return is_atomic(self.fn)

    @property
    def infinite(self) -> bool:
        """Whether the task runs until stopped (`@task(infinite=True)`)."""
        return is_infinite(self.fn)

    @property
    def interactive(self) -> bool:
        """Whether the task owns the real terminal (`@task(interactive=True)`)."""
        return is_interactive(self.fn)

    @property
    def hidden(self) -> bool | None:
        """The task's own `hidden=` answer, or `None` when it inherits one.

        Read the *declaration*, not the resolved answer: a finalizer that
        hides a group wants to know whether this task overrode it.
        """
        return declared_hidden(self.fn)

    @property
    def timed(self) -> bool:
        """Whether the task records timing history / shows a determinate bar —
        `@task(progress=False)` (and `infinite`/`interactive`) opt out."""
        return wants_progress(self.fn)

    @property
    def confirm(self) -> str:
        """The `@task(confirm="…")` prompt gating the task, or `""` if none."""
        return task_confirm(self.fn)

    # Cascade provenance (read-only) — for finalizers making decisions by
    # where a task came from and what it overrode.

    @property
    def defining_dir(self) -> str | None:
        """The folder the task was defined in, or `None` when the cascade did
        not tag it (a plugin- or `include()`-composed task, not a cascade file).
        Use it to act on tasks from one subtree of a monorepo."""
        from footman import discover

        return discover.defining_dir(self.fn)

    @property
    def shadowed(self) -> Task | None:
        """The task this one overrides — same name, one cascade level up — or
        `None` if it shadows nothing."""
        from footman import discover

        return discover.shadowed(self.fn)

    @property
    def shadow_chain(self) -> tuple[Task, ...]:
        """This task and every task it shadows, nearest (this one) first."""
        from footman import discover

        return tuple(discover.shadow_chain(self.fn))

    @property
    def source_file(self) -> str | None:
        """The file the task's function is defined in, or `None` when it can't
        be located (a built-in or dynamically constructed function)."""
        try:
            return task_source_file(self.fn)
        except TypeError:
            return None

    def add_pre(self, *tasks: TaskView | Task) -> None:
        """Prepend prerequisites (views or functions), skipping any already set."""
        have = list(pre_tasks(self.fn))
        setattr(
            self.fn,
            _PRE,
            [*(f for t in tasks if (f := _as_fn(t)) not in have), *have],
        )

    def add_post(self, *tasks: TaskView | Task) -> None:
        """Append post-tasks (views or functions), skipping any already set."""
        have = list(post_tasks(self.fn))
        setattr(
            self.fn,
            _POST,
            [*have, *(f for t in tasks if (f := _as_fn(t)) not in have)],
        )

    def disable(self, reason: str) -> None:
        """Mark the task unavailable — listed with *reason*, refused if run."""
        _gate(lambda: reason)(self.fn)

    def set_opts(self, **overrides: Any) -> None:
        """Set orchestration options on the task **permanently, for every use** —
        the finalize-time counterpart to a per-use `.opts()`. Takes the same
        options (`keep_going`, `atomic`, `interactive`, `progress`, `confirm`,
        `infinite`) and rejects a task parameter with the same taught error; the
        difference is that it edits the registered task rather than a per-use
        proxy, so a finalizer can set a policy across a whole class of tasks. A
        command-line `-k`/`--fail-fast` still wins over a set `keep_going`."""
        for attr, value in _opts_overrides(overrides).items():
            setattr(self.fn, attr, value)


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


def _task_views(g: Group, owner: Group | None = None) -> Iterator[TaskView]:
    for name, fn in g.tasks.items():
        yield TaskView(fn, name, owner)
    for sub in g.groups.values():
        yield from _task_views(sub, sub)


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
    saved_contributions = root.contributions
    root.tasks, root.groups = captured.tasks, captured.groups
    root.contributions = captured.contributions
    try:
        yield captured
    finally:
        root.tasks, root.groups = saved_tasks, saved_groups
        root.contributions = saved_contributions
