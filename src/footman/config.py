"""Behavioural settings, discovered the same way tasks are.

footman reads `[tool.footman]` from `pyproject.toml` and a standalone
`footman.toml` (whole-file), walking from the repo root down to the current
directory. Nearer files win, so a package can override repo-wide defaults; a
`--config PATH` on the command line overrides everything. Recognised keys:

* `tasks` — name of the task file to look for in the cascade (default
  `tasks.py`).
* `sequential` — run tasks one at a time by default (`fm` still overrides
  with `-s` / a parallel default).

Unknown keys are kept but ignored, so newer settings never break an older
footman.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from footman import _paths

# Filenames read in each directory of the cascade. Within one directory the
# dedicated `footman.toml` wins over `pyproject.toml`'s `[tool.footman]`.
PYPROJECT = "pyproject.toml"
FOOTMAN_TOML = "footman.toml"


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _footman_table(path: Path) -> dict[str, Any]:
    """The footman settings in *path* — `[tool.footman]` for a pyproject,
    the whole file for anything else. Empty dict if absent/unreadable."""
    data = _read_toml(path)
    if data is None:
        return {}
    if path.name == PYPROJECT:
        tool = data.get("tool")
        table = tool.get("footman") if isinstance(tool, dict) else None
        return table if isinstance(table, dict) else {}
    return data


def _dir_config(directory: Path) -> dict[str, Any]:
    """Merged footman settings for one directory (footman.toml wins)."""
    merged: dict[str, Any] = {}
    merged.update(_footman_table(directory / PYPROJECT))
    merged.update(_footman_table(directory / FOOTMAN_TOML))
    return merged


def load_config(
    cwd: Path, ceiling: Path, cli_path: str | None = None
) -> dict[str, Any]:
    """Merge config from *ceiling* down to *cwd*; *cli_path* overrides all."""
    merged: dict[str, Any] = {}
    for directory in _paths.dir_chain(cwd, ceiling):
        merged.update(_dir_config(directory))
    if cli_path:
        merged.update(_footman_table(Path(cli_path).expanduser()))
    return merged
