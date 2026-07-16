"""Load the task cascade and merge it into one command tree.

In a monorepo you rarely want a single tasks file. footman collects every
``tasks.py`` from the repo root down to your current directory and merges them
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

#: Attribute stamped on every task fn: the directory of the file that defined
#: it. The scheduler uses it as the task's working directory.
DEFINING_DIR = "_footman_dir"


def _import_file(path: Path, index: int) -> Group:
    """Import *path* into a fresh registry and return the populated tree."""
    registry.reset()
    spec = importlib.util.spec_from_file_location(f"footman_tasks_{index}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load tasks file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)  # let tasks import sibling helpers
    spec.loader.exec_module(module)
    return registry.root


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


def load_tree(files: list[Path]) -> Group:
    """Import each file (root first) and overlay them into one merged tree."""
    merged = Group("root")
    for index, path in enumerate(files):
        tree = _import_file(path, index)
        _overlay(merged, tree, str(path.parent))
    registry.reset()  # leave no global state behind
    return merged


def load_single(path: Path) -> Group:
    """Load exactly one tasks file (the ``-f/--tasks-file`` escape hatch)."""
    return load_tree([path])


def defining_dir(fn: Task) -> str | None:
    """The folder the task was defined in, if the cascade tagged it."""
    return getattr(fn, DEFINING_DIR, None)
