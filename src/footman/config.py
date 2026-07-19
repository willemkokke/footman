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

# Keys that only make sense in the user-level file: they govern shared,
# machine-wide behaviour (the cache collector sweeps one cache for every
# project), so a per-project value would be a lie waiting to confuse
# someone. Stripped from cascade files, with a note under -v; an explicit
# `--config` file keeps them — the user named that file on purpose.
USER_LEVEL_KEYS = frozenset({"gc"})


class ConfigError(Exception):
    """A config TOML file exists but cannot be parsed."""


def _read_toml(path: Path, required: bool = False) -> dict[str, Any] | None:
    """Parse *path*; `None` if absent/unreadable, `ConfigError` if malformed.

    A missing file is normal (most directories have no config); a file that
    exists but doesn't parse is a user mistake that must not be silently
    read as "no settings". When *required* (an explicit `--config`), an
    unreadable file is loud too, not silently skipped.
    """
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        if required:
            raise ConfigError(f"{path}: {exc.strerror or exc}") from exc
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def _footman_table(path: Path, required: bool = False) -> dict[str, Any]:
    """The footman settings in *path* — `[tool.footman]` for a pyproject,
    the whole file for anything else. Empty dict if absent/unreadable."""
    data = _read_toml(path, required=required)
    if data is None:
        return {}
    if path.name == PYPROJECT:
        tool = data.get("tool")
        table = tool.get("footman") if isinstance(tool, dict) else None
        return table if isinstance(table, dict) else {}
    return data


def _dir_config(
    directory: Path,
    on_warning: Callable[[str], None] | None,
    on_note: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Merged footman settings for one directory (footman.toml wins).

    A malformed file in the discovered cascade is warned about and skipped —
    one broken pyproject.toml between the repo root and the cwd should not
    brick every `fm` invocation. User-level-only keys are stripped here,
    with an advisory through *on_note* (verbose runs wire it; quiet ones
    don't) pointing at where the key belongs.
    """
    merged: dict[str, Any] = {}
    for name in (PYPROJECT, FOOTMAN_TOML):
        try:
            merged.update(_footman_table(directory / name))
        except ConfigError as exc:
            if on_warning is not None:
                on_warning(f"ignoring malformed config: {exc}")
    for key in USER_LEVEL_KEYS & merged.keys():
        del merged[key]
        if on_note is not None:
            on_note(
                f"`{key}` is a user-level setting — it belongs in "
                f"{_paths.footman_config_file()}; ignoring it in {directory}"
            )
    return merged


DEFAULT_COMPLETION_MAX_AGE_S = 600  # 10 minutes


def _parse_duration(value: object) -> int | None:
    """Seconds from a duration (`"10m"`, `"30s"`, `"1h"`, or a plain int); `None`
    to disable (`off`/`0`/negative). An unparseable value falls back to the
    default rather than crashing the completion build."""
    if value is None:
        return DEFAULT_COMPLETION_MAX_AGE_S
    if isinstance(value, bool):  # bool is an int subclass — treat as on/off
        return DEFAULT_COMPLETION_MAX_AGE_S if value else None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return DEFAULT_COMPLETION_MAX_AGE_S
    text = value.strip().lower()
    if text in ("off", "none", ""):
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = units.get(text[-1:])
    try:
        n = int(text[:-1]) if unit else int(text)
    except ValueError:
        return DEFAULT_COMPLETION_MAX_AGE_S
    seconds = n * (unit or 1)
    return seconds if seconds > 0 else None


def completion_max_age(cfg: dict[str, Any]) -> int | None:
    """Seconds before the completion cache is considered stale, or `None` if
    disabled. Reads `[tool.footman] completion.max_age`; default 10 minutes."""
    completion = cfg.get("completion")
    raw = completion.get("max_age") if isinstance(completion, dict) else None
    return _parse_duration(raw)


def load_config(
    cwd: Path,
    ceiling: Path,
    cli_path: str | None = None,
    on_warning: Callable[[str], None] | None = None,
    on_note: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Merge config from *ceiling* down to *cwd*; *cli_path* overrides all.

    A malformed discovered file warns (via *on_warning*) and is skipped; a
    missing or malformed explicit *cli_path* raises `ConfigError` — the user
    named that file on purpose, so it failing quietly (a typo silently ignored)
    is not an option. *on_note* carries advisories (a user-level key found in
    a project file) — verbose runs wire it, others leave it `None`.
    """
    if cli_path:
        # The explicit file is total control: it replaces the global file
        # and the cascade both — the user named exactly what applies.
        path = Path(cli_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"{path}: no such file")
        return _footman_table(path, required=True)

    merged: dict[str, Any] = {}
    try:
        # The bottom rung: the user-level file. Whole-file footman settings,
        # like footman.toml; every project layer cascades over it.
        merged.update(_footman_table(_paths.footman_config_file()))
    except ConfigError as exc:
        if on_warning is not None:
            on_warning(f"ignoring malformed config: {exc}")
    for directory in _paths.dir_chain(cwd, ceiling):
        merged.update(_dir_config(directory, on_warning, on_note))
    return merged
