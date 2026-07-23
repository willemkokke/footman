"""Compose the task surface: adopt tasks from other modules and packages.

Two public pieces, designed to be used together from a tasks file:

- `include(source, ...)` grafts another module's task tree into yours —
  cherry-picked, namespaced under a group, loud on collisions.
- `plugin(name)` resolves a `footman.tasks` entry point published by an
  installed package to its advertised `Group`, ready to `include()`.

Config-mounted plugins (`[tool.footman] plugins = ["name"]`) use the same
resolution but mount the group *under* the tasks-file cascade, so any name a
user defines shadows a plugin's. One rule of thumb: *config mounts a tool;
tasks.py adopts a task.*

Everything resolves at import/manifest-build time; the completion hot path is
untouched. `importlib.metadata` is stdlib — footman stays zero-dependency.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from footman import registry
from footman.registry import Group, RegistrationError

ENTRY_POINT_GROUP = "footman.tasks"

# One import (and capture) per provider module per process: every cascade
# file that includes the same module gets the same tree, whatever the import
# order — the `sys.modules` cache can't half-register a provider.
_module_trees: dict[str, Group] = {}


def _tree_of_module(module: ModuleType) -> Group:
    """The task tree of an already-imported module: the memo, or a taught no.

    A module imported *outside* `include()` already ran its decorators
    against whatever registry was live — its tree cannot be reconstructed,
    and re-executing the module would double every side effect. The answer
    is guidance, not guesswork.
    """
    name = module.__name__
    if name in _module_trees:
        return _module_trees[name]
    raise RegistrationError(
        f"include({name!r}): the module was already imported outside "
        f"include(), so its tasks were never captured — call include() "
        f"before anything else imports it, or have the module expose an "
        f"explicit Group and pass that instead"
    )


def _adopt_explicit_group(module: ModuleType) -> Group:
    """A never-registering provider's single module-level `Group`, if any."""
    groups = [v for v in vars(module).values() if isinstance(v, Group)]
    if len(groups) == 1:
        return groups[0]
    detail = "no module-level Group" if not groups else f"{len(groups)} Groups"
    raise RegistrationError(
        f"include({module.__name__!r}): the module registered no tasks and "
        f"has {detail} to adopt — define tasks with @task/group(), or expose "
        f"exactly one Group"
    )


def _import_source(dotted: str) -> Group:
    """Import *dotted* under `capture()` and memoise its captured tree."""
    if dotted in _module_trees:
        return _module_trees[dotted]
    import sys

    if dotted in sys.modules:
        return _tree_of_module(sys.modules[dotted])
    with registry.capture() as captured:
        module = importlib.import_module(dotted)
    tree = (
        captured
        if (captured.tasks or captured.groups)
        # Nothing registered at module level: the provider keeps an explicit
        # Group instead (the entry-point convention) — unambiguous only
        # because the capture came back empty.
        else _adopt_explicit_group(module)
    )
    _module_trees[dotted] = tree
    return tree


def _as_group(source: str | ModuleType | Group) -> Group:
    if isinstance(source, Group):
        return source
    if isinstance(source, ModuleType):
        return _tree_of_module(source)
    return _import_source(source)


def _fork(tree: Group) -> Group:
    """A structural copy of *tree*: fresh Group objects and dicts, shared fns.

    A memoised provider tree grafted into a project is later mutated by the
    cascade overlay/tag in place — without a fork, one project's tasks (and
    DEFINING_DIR stamps) leak into the shared `_module_trees` memo and thus into
    every later in-process invocation (F38). The task callables stay shared on
    purpose: DEFINING_DIR is re-stamped on each load, so sharing them is safe
    and keeps `recording()`/identity checks meaningful.
    """
    fork = Group(tree.name, tree.help)
    fork.tasks.update(tree.tasks)  # share fns, but into a fresh dict
    for name, sub in tree.groups.items():
        fork.groups[name] = _fork(sub)  # recurse: fresh subgroup objects
    # A faithful copy carries *every* Group field, not only tasks/groups: a
    # runnable group keeps its `@group.default` (so the bare-group grammar and
    # its options survive the graft), and a provider's `@finalize` hooks ride
    # along. The default action stays the shared fn — like the task fns, it is
    # re-stamped per load, and an empty-body default fans out its group's own
    # (equally shared) tasks. `test_compose`'s field census fails the moment a
    # new Group field is added but not copied here.
    fork.default_task = tree.default_task
    fork.finalizers = list(tree.finalizers)
    return fork


def include(
    source: str | ModuleType | Group,
    /,
    *,
    into: Group | None = None,
    only: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
    override: bool = False,
) -> Group:
    """Graft another module's tasks into the current tree (or *into* a group).

    ```python
    include("shared_tasks")                          # everything, at root
    include("shared_tasks", only=["lint", "fmt"])    # cherry-pick by CLI name
    include("mkdocs_helpers.tasks", into=docs)       # namespace: fm docs …
    include(plugin("mkdocs"), only=["build"])        # from an entry point
    ```

    *source* is a dotted module name, an imported module, or a `Group`. The
    provider imports under a registry capture, so its decorators can't leak
    into your tree. Collisions are loud (`RegistrationError`) unless
    `override=True`; unknown `only=`/`exclude=` names are errors too (typo
    protection). Included tasks run from *your* file's directory — a shared
    lint task lints this project. Returns the group it grafted into.
    """
    tree = _fork(_as_group(source))  # graft a private copy; never the memo
    target = into if into is not None else registry.root

    known = set(tree.tasks) | set(tree.groups)
    for name in (*only, *exclude):
        if name not in known:
            raise RegistrationError(
                f"include(): {source!r} has no task or group named {name!r} "
                f"(has: {', '.join(sorted(known)) or 'nothing'})"
            )
    wanted = set(only) if only else known
    wanted -= set(exclude)

    for name, fn in tree.tasks.items():
        if name not in wanted:
            continue
        if not override:
            target._claim(name)
        target.groups.pop(name, None)
        target.tasks[name] = fn
    for name, sub in tree.groups.items():
        if name not in wanted:
            continue
        if not override:
            target._claim(name)
        target.tasks.pop(name, None)
        target.groups[name] = sub
    # A provider's `@finalize` hooks edit the whole merged tree, so they belong
    # on the live root that discovery collects from — grafting only moved tasks
    # and groups, and a finalizer left on the forked subtree would never run.
    registry.root.finalizers.extend(tree.finalizers)
    return target


def plugin(name: str) -> Group:
    """The `Group` a package advertises under the `footman.tasks` entry point.

    ```toml
    # the plugin package's pyproject.toml
    [project.entry-points."footman.tasks"]
    mkdocs = "footman_mkdocs:tasks"
    ```

    Raises `RegistrationError` naming the installed entry points when *name*
    isn't one of them — a configured-but-missing plugin should read as the
    typo or missing install it is.
    """
    from importlib.metadata import entry_points

    found = entry_points(group=ENTRY_POINT_GROUP)
    matches = [ep for ep in found if ep.name == name]
    if not matches:
        installed = ", ".join(sorted(ep.name for ep in found)) or "none"
        raise RegistrationError(
            f"plugin {name!r}: no {ENTRY_POINT_GROUP!r} entry point found "
            f"(installed: {installed})"
        )
    if len(matches) > 1:
        dists = ", ".join(str(ep.dist) for ep in matches)
        raise RegistrationError(
            f"plugin {name!r}: claimed by more than one distribution ({dists})"
        )
    try:
        with registry.capture() as captured:
            loaded = matches[0].load()
    except RegistrationError:
        raise  # already a taught message; don't re-wrap
    except Exception as exc:
        # A plugin with a missing optional dep (or any import-time failure)
        # would otherwise dump a raw traceback on *every* invocation, `--help`
        # included. Teach it; the mount guard reports it at exit 2.
        raise RegistrationError(
            f"plugin {name!r}: failed to import ({type(exc).__name__}: {exc})"
        ) from exc
    if isinstance(loaded, Group):
        return loaded
    if isinstance(loaded, ModuleType):
        name = loaded.__name__
        if captured.tasks or captured.groups:
            # Memoise under the module name so re-resolving in the same
            # process (or a later include of the same module) reuses the tree.
            _module_trees[name] = captured
            return captured
        # Registered nothing at module level. Reuse a memoised tree if a prior
        # resolve captured one (the entry point re-`load()`s the cached module,
        # so decorators no longer fire and `captured` comes back empty);
        # otherwise adopt the module's single explicit Group. Routing through
        # _import_source would hit sys.modules — the entry point just loaded the
        # module — and raise the misleading "already imported outside include()"
        # error.
        if name in _module_trees:
            return _module_trees[name]
        tree = _adopt_explicit_group(loaded)
        _module_trees[name] = tree
        return tree
    raise RegistrationError(
        f"plugin {name!r}: entry point must resolve to a footman Group "
        f"(or a module of tasks), got {type(loaded).__name__}"
    )


def mount_plugins(base: Group, names: list[str]) -> None:
    """Mount config-listed plugins at the command path each name spells.

    A plugin's name *is* its command path. A bare name mounts at the root
    (`plugins = ["acme"]` → `fm acme …`); a dotted name nests, one group per
    segment (`plugins = ["footman.tools"]` → `fm footman tools …`). The last
    segment names the entry point to resolve; the leading segments are
    namespace groups, created on demand and shared by every plugin that
    spells the same prefix — so `footman.docs` and `footman.tools` meet under
    one `footman` group without either owning it.

    Called by the app layer *before* the cascade overlays, so user-defined
    names shadow plugin groups silently — consistent with the cascade's own
    nearest-wins rule.
    """
    for raw in names:
        dotted = str(raw)
        tree = _fork(plugin(dotted))  # graft a private copy; never the memo
        *parents, leaf = dotted.split(".")
        target = base
        for segment in parents:
            target = _namespace(target, segment)
        target.tasks.pop(leaf, None)
        target.groups[leaf] = tree if tree.name == leaf else _named(tree, leaf)


def _namespace(parent: Group, name: str) -> Group:
    """The subgroup *name* of *parent*, reused if present or created if not.

    Each leading segment of a dotted plugin name is a namespace group; two
    plugins that share a prefix (`footman.docs`, `footman.tools`) share the
    group rather than fight over it. A task of the same name yields — a group
    has to sit there for anything to nest beneath it.
    """
    existing = parent.groups.get(name)
    if isinstance(existing, Group):
        return existing
    parent.tasks.pop(name, None)
    created = Group(name)
    parent.groups[name] = created
    return created


def _named(tree: Group, name: str) -> Group:
    """Re-home a captured root tree under a named group."""
    named = Group(name, tree.help)
    named.tasks.update(tree.tasks)
    named.groups.update(tree.groups)
    return named
