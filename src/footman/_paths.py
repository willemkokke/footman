"""Filesystem locations shared by the execution path and the completion hot
path.

Stdlib-only and deliberately import-light: the completion hot path imports this
module on every TAB press, so it must never reach for the framework or the
user's code.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Ancestor markers that identify the project root. The manifest cache is keyed
# by cwd, but these still bound a lone-file lookup when there is no repo root.
PROJECT_MARKERS = ("pyproject.toml", "footman.toml", ".git", "tasks.py")

# Marks the ceiling of the upward walk — the repo root where the task cascade
# starts and the config search stops. `.git` is the natural monorepo edge.
REPO_MARKERS = (".git",)

# Default name of the tasks file, looked for in every folder of the cascade.
DEFAULT_TASKS_FILE = "tasks.py"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor of *start* (default: cwd) containing a project marker."""
    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in PROJECT_MARKERS):
            return directory
    return start


def find_repo_root(start: Path | None = None) -> Path:
    """Ceiling of the cascade: nearest ancestor with a repo marker (`.git`).

    Falls back to `find_project_root` when there is no VCS boundary, so a
    single-package checkout still has a sensible top.
    """
    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in REPO_MARKERS):
            return directory
    return find_project_root(start)


def dir_chain(cwd: Path, ceiling: Path) -> list[Path]:
    """Directories from *ceiling* down to *cwd* inclusive (root first).

    If *ceiling* is not an ancestor of *cwd* (unrelated trees), just `[cwd]`.
    """
    cwd = cwd.resolve()
    ceiling = ceiling.resolve()
    chain: list[Path] = []
    for directory in (cwd, *cwd.parents):
        chain.append(directory)
        if directory == ceiling:
            return list(reversed(chain))
    return [cwd]


def task_files(
    cwd: Path, ceiling: Path, filename: str = DEFAULT_TASKS_FILE
) -> list[Path]:
    """Existing task files from *ceiling* down to *cwd* (root first, cwd last)."""
    return [f for d in dir_chain(cwd, ceiling) if (f := d / filename).is_file()]


def cache_home() -> Path:
    """Base cache directory, honouring `XDG_CACHE_HOME`."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) if xdg else Path.home() / ".cache"


def footman_cache_dir() -> Path:
    """footman's own cache directory: `$FOOTMAN_CACHE_DIR` when set, else
    `<cache home>/footman`. One override moves every footman cache —
    completion manifests and timing history alike — and the completion hot
    path resolves through here too, so TAB follows it with no re-install.
    """
    override = os.environ.get("FOOTMAN_CACHE_DIR")
    return Path(override) if override else cache_home() / "footman"


def _dir_key(key_dir: Path) -> str:
    return hashlib.sha256(str(key_dir.resolve()).encode("utf-8")).hexdigest()[:16]


def manifest_path(key_dir: Path) -> Path:
    """Cached-manifest path for *key_dir* (the cwd), keyed by a path hash.

    The effective task set depends on where you stand in a monorepo — the
    cascade from the repo root down to the cwd — so the cache is per directory.
    """
    return footman_cache_dir() / f"{_dir_key(key_dir)}.json"


def times_path(key_dir: Path) -> Path:
    """Duration-history path for *key_dir* — beside its manifest, same key."""
    return footman_cache_dir() / f"{_dir_key(key_dir)}.times.json"


def cwd_manifest_path() -> Path:
    """Manifest path for the current directory (both hot and cold paths agree)."""
    return manifest_path(Path.cwd())
