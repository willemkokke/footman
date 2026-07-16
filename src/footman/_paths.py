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

#: Ancestor markers that identify the project root. The manifest cache is keyed
#: by this root so completion and execution agree on the same file.
PROJECT_MARKERS = ("pyproject.toml", ".git", "tasks.py")

#: Default name of the tasks file, relative to the project root.
DEFAULT_TASKS_FILE = "tasks.py"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor of *start* (default: cwd) containing a project marker."""
    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in PROJECT_MARKERS):
            return directory
    return start


def cache_home() -> Path:
    """Base cache directory, honouring ``XDG_CACHE_HOME``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) if xdg else Path.home() / ".cache"


def manifest_path(project_root: Path) -> Path:
    """Path to the cached manifest for *project_root* (keyed by a path hash)."""
    key = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()[:16]
    return cache_home() / "footman" / f"{key}.json"
