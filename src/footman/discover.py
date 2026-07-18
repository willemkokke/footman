"""Load the task cascade and merge it into one command tree.

In a monorepo you rarely want a single tasks file. footman collects every
`tasks.py` from the repo root down to your current directory and merges them
top-down: a new name **appends**, a name already present **overrides** (the
folder nearest your cwd wins), and a command group present at both levels
**merges** (its tasks overlaid the same way). Each task remembers the folder of
the file that defined it, so it runs from there regardless of where you stand.

The registry raises on a duplicate name, so the merge can't be done by importing
every file into one registry — each file is imported into a fresh registry and
the resulting trees are overlaid here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from footman import registry
from footman.registry import Group, Task

# Attribute stamped on every task fn: the directory of the file that defined
# it. The scheduler uses it as the task's working directory.
DEFINING_DIR = "_footman_dir"


class TasksImportError(Exception):
    """A tasks file failed to import; names the file and keeps the cause."""

    def __init__(self, path: Path, original: BaseException) -> None:
        self.path = path
        self.original = original
        super().__init__(f"{path}: {type(original).__name__}: {original}")


def _import_file(path: Path, index: int) -> Group:
    """Import *path* into a fresh registry and return the populated tree."""
    registry.reset()
    spec = importlib.util.spec_from_file_location(f"footman_tasks_{index}", path)
    if spec is None or spec.loader is None:
        raise TasksImportError(path, ImportError("cannot load tasks file"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    parent = str(path.parent)
    # Search this file's own dir first for sibling helpers (move-to-front, not
    # insert-if-absent — a shared dir on sys.path must not shadow it), snapshot
    # sys.path/sys.modules, and evict the direct siblings it imports afterwards.
    # Otherwise two cascade files each doing `import helpers` share whoever
    # imported first (F14/D8). Restoring sys.path also stops it accumulating
    # across the many load_tree calls an in-process runner makes.
    saved_path = sys.path[:]
    before = set(sys.modules)
    if parent in sys.path:
        sys.path.remove(parent)
    sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise TasksImportError(path, exc) from exc
    finally:
        sys.path[:] = saved_path
        _evict_siblings(before, Path(parent))
    return registry.root


def _evict_siblings(before: set[str], parent: Path) -> None:
    """Drop modules a cascade file imported that live directly in its dir.

    A sibling `helpers.py` (`parent/helpers.py`) or a package one level down
    (`parent/pkg/__init__.py`) — so the next file gets its own copy rather than
    whoever-imported-first-wins. Deeper imports and editable-installed packages
    live elsewhere on disk and are deliberately left (D8).
    """
    for name in set(sys.modules) - before:
        file = getattr(sys.modules.get(name), "__file__", None)
        if file is None:
            continue
        f = Path(file)
        sibling = f.parent == parent
        package = f.name == "__init__.py" and f.parent.parent == parent
        if sibling or package:
            del sys.modules[name]


def _tag(group: Group, directory: str) -> None:
    """Stamp every task in *group* (recursively) with its defining directory."""
    for fn in group.tasks.values():
        setattr(fn, DEFINING_DIR, directory)
    for sub in group.groups.values():
        _tag(sub, directory)


def _overlay(base: Group, overlay: Group, directory: str) -> None:
    """Merge *overlay* onto *base* in place: local (overlay) wins by name."""
    for name, fn in overlay.tasks.items():
        setattr(fn, DEFINING_DIR, directory)
        base.groups.pop(name, None)  # a local task shadows an inherited group
        base.tasks[name] = fn
    for name, sub in overlay.groups.items():
        if name in base.groups:
            base.groups[name].help = sub.help or base.groups[name].help
            _overlay(base.groups[name], sub, directory)
        else:
            base.tasks.pop(name, None)  # a local group shadows an inherited task
            _tag(sub, directory)
            base.groups[name] = sub


def load_tree(files: list[Path], base: Group | None = None) -> Group:
    """Import each file (root first) and overlay them into one merged tree.

    *base* seeds the tree (config-mounted plugin groups go there), so
    anything a tasks file defines overlays it — user names win over plugins
    exactly as nearer cascade files win over farther ones.
    """
    merged = base if base is not None else Group("root")
    for index, path in enumerate(files):
        tree = _import_file(path, index)
        _overlay(merged, tree, str(path.parent))
    registry.reset()  # leave no global state behind
    return merged


def load_single(path: Path) -> Group:
    """Load exactly one tasks file (the `-f/--tasks-file` escape hatch)."""
    return load_tree([path])


def defining_dir(fn: Task) -> str | None:
    """The folder the task was defined in, if the cascade tagged it."""
    return getattr(fn, DEFINING_DIR, None)
