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
# Files footman itself reads, plus this brand's config file, which
# `find_project_root` appends — no VCS entry belongs here, see below.
PROJECT_MARKERS = ("pyproject.toml", "tasks.py")

# Marks the ceiling of the upward walk — the repo root where the task cascade
# starts and the config search stops: the version-control boundary, whichever
# system drew it. footman never runs these tools or reads their metadata; it
# notices a directory entry, which is why supporting four costs nothing.
REPO_MARKERS = (".git", ".jj", ".hg", ".svn")

# Default name of the tasks file, looked for in every folder of the cascade.
DEFAULT_TASKS_FILE = "tasks.py"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor of *start* (default: cwd) containing a project marker."""
    start = (start or Path.cwd()).resolve()
    # This brand's config file counts too — `acme.toml` marks an `acme`
    # project root exactly as `footman.toml` marks footman's.
    markers = (*PROJECT_MARKERS, config_basename())
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in markers):
            return directory
    return start


def find_repo_root(start: Path | None = None) -> Path:
    """Ceiling of the cascade: nearest ancestor with a `REPO_MARKERS` entry.

    Git, Jujutsu, Mercurial and Subversion all draw the same boundary, and
    footman only ever asks whether the directory is there — it runs none of
    these tools and reads none of their metadata.

    Falls back to `find_project_root` when there is no VCS boundary, so a
    single-package checkout still has a sensible top. That fallback is also
    why no VCS marker belongs in `PROJECT_MARKERS`: by the time it runs,
    every ancestor has already been searched for every one of them.
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


# The brand's locations, set once by `App.run` before anything reads them —
# the same module-global shape `_app._brand` uses for the brand's *names*.
# Plain strings and a path, never a `Brand`: this module is imported on every
# TAB press and must not reach for the framework.
_prefix = "FOOTMAN"
_home: Path | None = None
_config_name = "footman"


def configure(
    *,
    prefix: str = "FOOTMAN",
    home: Path | None = None,
    config_name: str = "footman",
) -> None:
    """Point every location at one brand's world.

    *home* is where that CLI keeps what it owns; `None` keeps the XDG
    fallback. *prefix* namespaces the environment variables, so a stray
    `FOOTMAN_CACHE_DIR` cannot move another product's cache. Detached
    children are handed the resolved values rather than re-deriving them.
    """
    global _prefix, _home, _config_name
    _prefix, _home, _config_name = prefix, home, config_name


def child_args() -> list[str]:
    """The configured locations as argv words for a detached child.

    Children inherit the environment but not the brand, and re-deriving a
    home from the environment would be re-deriving it *wrongly* — the brand
    computed it. So the parent hands over resolved values instead, and the
    child never reads a variable of its own.
    """
    return [_prefix, str(_home) if _home is not None else "", _config_name]


def configure_child(prefix: str = "", home: str = "", config_name: str = "") -> None:
    """The other side of `child_args` — empty strings mean stock defaults."""
    configure(
        prefix=prefix or "FOOTMAN",
        home=Path(home) if home else None,
        config_name=config_name or "footman",
    )


def home_from_env(var: str) -> Path | None:
    """A home read from environment variable *var*, or `None` when unset."""
    value = os.environ.get(var)
    return Path(value).expanduser() if value else None


def env_prefix() -> str:
    """The configured environment-variable prefix."""
    return _prefix


def env_var(suffix: str) -> str:
    """The configured spelling of a variable: `ACME_CACHE_DIR`."""
    return f"{_prefix}_{suffix}"


def brand_home() -> Path | None:
    """The configured home, or `None` when locations fall back to XDG."""
    return _home


def config_basename() -> str:
    """The standalone config filename for this brand — `acme.toml`."""
    return f"{_config_name}.toml"


def config_table() -> str:
    """The `pyproject.toml` table for this brand — the `acme` in `[tool.acme]`."""
    return _config_name


def cache_home() -> Path:
    """Base cache directory, honouring `XDG_CACHE_HOME`."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) if xdg else Path.home() / ".cache"


def config_home() -> Path:
    """Base config directory, honouring `XDG_CONFIG_HOME`.

    `~/.config` on every platform — the convention CLI tools (uv, ruff,
    git's own XDG support) follow on macOS and Windows too, and the
    symmetric sibling of `cache_home`.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def footman_config_file() -> Path:
    """The user-level config file, most specific answer first: this brand's
    `<PREFIX>_CONFIG` (a file path, since this is one file — unlike
    `<PREFIX>_CACHE_DIR`'s directory), then `<home>/config.toml` when the
    brand set a home, else `<config home>/<name>/config.toml`.

    The bottom rung of the precedence ladder: project config cascades over
    it, `--config` replaces it."""
    override = os.environ.get(env_var("CONFIG"))
    if override:
        return Path(override).expanduser()
    if _home is not None:
        return _home / "config.toml"
    return config_home() / _config_name / "config.toml"


def footman_cache_dir() -> Path:
    """This CLI's cache directory, most specific answer first: its own
    `<PREFIX>_CACHE_DIR`, then `<home>/cache` when the brand set a home,
    else `<cache home>/<name>`.

    One override moves every cache — completion manifests and timing history
    alike — and the completion hot path resolves through here too, so TAB
    follows it with no re-install.
    """
    override = os.environ.get(env_var("CACHE_DIR"))
    if override:
        return Path(override)
    if _home is not None:
        return _home / "cache"
    return cache_home() / _config_name


def user_tasks_file(filename: str = DEFAULT_TASKS_FILE) -> Path | None:
    """This CLI's user-level tasks file, or `None` when it has no home.

    `<home>/<tasks_file>` — your own tasks, without a project. It is a
    *fallback*, not a rung: a project's cascade wins outright, because there
    is one way to get tasks into a project tree and that is pulling them in
    a tasks file.
    """
    return None if _home is None else _home / filename


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


def source_manifest_path(cwd: Path, tasks_file: Path) -> Path:
    """Cache path for a `-f <file>` run, keyed by *both* the cwd and the file.

    A `-f` invocation loads that file's tasks *and* the cwd's config plugins,
    so the task set depends on the pair — the same file opened from two projects
    is two caches. A separate key from `manifest_path`, so a `-f` run never
    poisons the plain-cwd completion cache.
    """
    joined = f"{cwd.resolve()}\0{tasks_file.resolve()}"
    key = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return footman_cache_dir() / f"{key}.json"


def cwd_manifest_path() -> Path:
    """Manifest path for the current directory (both hot and cold paths agree)."""
    return manifest_path(Path.cwd())
