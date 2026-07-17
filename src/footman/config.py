"""Behavioural settings, discovered the same way tasks are.

footman reads `[tool.footman]` from `pyproject.toml` and a standalone
`footman.toml` (whole-file), walking from the repo root down to the current
directory. Nearer files win, so a package can override repo-wide defaults; a
`--config PATH` on the command line overrides everything. Recognised keys:

* `tasks` — name of the task file to look for in the cascade (default
  `tasks.py`).
* `sequential` — run tasks one at a time by default (`fm` still overrides
  with `-s` / a parallel default).
* `plugins` — `footman.tasks` entry points to mount as command groups
  (opt-in; installing a package never adds tasks by itself).

Unknown keys are kept but ignored, so newer settings never break an older
footman.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from footman import _paths

# Filenames read in each directory of the cascade. Within one directory the
# dedicated `footman.toml` wins over `pyproject.toml`'s `[tool.footman]`.
PYPROJECT = "pyproject.toml"
FOOTMAN_TOML = "footman.toml"


class ConfigError(Exception):
    """A config TOML file exists but cannot be parsed."""


def _read_toml(path: Path) -> dict[str, Any] | None:
    """Parse *path*; `None` if absent/unreadable, `ConfigError` if malformed.

    A missing file is normal (most directories have no config); a file that
    exists but doesn't parse is a user mistake that must not be silently
    read as "no settings".
    """
    try:
        text = path.read_text("utf-8")
    except OSError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
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


def _dir_config(
    directory: Path, on_warning: Callable[[str], None] | None
) -> dict[str, Any]:
    """Merged footman settings for one directory (footman.toml wins).

    A malformed file in the discovered cascade is warned about and skipped —
    one broken pyproject.toml between the repo root and the cwd should not
    brick every `fm` invocation.
    """
    merged: dict[str, Any] = {}
    for name in (PYPROJECT, FOOTMAN_TOML):
        try:
            merged.update(_footman_table(directory / name))
        except ConfigError as exc:
            if on_warning is not None:
                on_warning(f"ignoring malformed config: {exc}")
    return merged


def load_config(
    cwd: Path,
    ceiling: Path,
    cli_path: str | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Merge config from *ceiling* down to *cwd*; *cli_path* overrides all.

    A malformed discovered file warns (via *on_warning*) and is skipped; a
    malformed explicit *cli_path* raises `ConfigError` — the user named that
    file on purpose, so it failing quietly is not an option.
    """
    merged: dict[str, Any] = {}
    for directory in _paths.dir_chain(cwd, ceiling):
        merged.update(_dir_config(directory, on_warning))
    if cli_path:
        merged.update(_footman_table(Path(cli_path).expanduser()))
    return merged
