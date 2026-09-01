"""Behavioural settings, discovered the same way tasks are.

footman reads `[tool.footman]` from `pyproject.toml` and a standalone
`footman.toml` (whole-file), walking from the repo root down to the current
directory. Nearer files win, so a package can override repo-wide defaults; a
`--config PATH` on the command line overrides everything.

**`KEYS` below is the list of recognised keys** — name, accepted values,
default and description, as data. The docs table renders from it, so the
reference page and the runner cannot disagree; add a key there and it
documents itself.

Unknown keys are kept but ignored, so newer settings never break an older
footman.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from footman import _encoding, _paths

# Filenames read in each directory of the cascade. Within one directory the
# dedicated file wins over `pyproject.toml`'s table. Both the dedicated
# filename and the table name are the brand's — `acme.toml` and
# `[tool.acme]` — so ask `_paths` rather than hard-coding footman's.
PYPROJECT = "pyproject.toml"

# Keys that only make sense in the user-level file: they govern shared,
# machine-wide behaviour (the cache collector sweeps one cache for every
# project), so a per-project value would be a lie waiting to confuse
# someone. Stripped from cascade files, with a note under -v; an explicit
# `--config` file keeps them — the user named that file on purpose.
USER_LEVEL_KEYS = frozenset({"gc", "cascade", "builtin"})

# Every recognised key, as data — the source the docs table renders from, so
# a reference page cannot describe a key set the runner doesn't have. The
# `cwd` key went undocumented for four releases because the only list was
# prose in a docstring; a table nothing reads is a table nobody updates.
#
# (name, values, default, help). `name` uses dotted form for a sub-table key.
KEYS: tuple[tuple[str, str, str, str], ...] = (
    (
        "tasks",
        "filename",
        "`tasks.py`",
        "Task file to look for in each directory of the cascade.",
    ),
    (
        "sequential",
        "`true` / `false`",
        "`false`",
        "Run tasks one at a time by default; `-s` does it for one invocation.",
    ),
    (
        "jobs",
        "integer",
        "cores - 1",
        "Max parallel tasks, never below 2. `-j=N` overrides it.",
    ),
    (
        "color",
        "`always` / `never` / `auto`",
        "`auto`",
        "When to emit ANSI colour, for footman's own output and the tools it "
        "spawns. `--color`/`--no-color` override it.",
    ),
    (
        "cwd",
        "policy token / absolute path",
        "`taskfile`",
        "Where tasks run by default: `taskfile` (the directory of the file "
        "that defined the task), `root` (the cascade's top), `asinvoked` "
        "(where you typed the command), `unmanaged` (footman holds no "
        "opinion), or an absolute path. A relative suffix belongs on a "
        "task's `rel=`.",
    ),
    (
        "sort",
        "`true` / `false`",
        "`false`",
        "List tasks alphabetically in `--list`, `--tree`, help and the "
        "generated docs pages. Default: definition order, so the file's own "
        "order is the listing's. Presentation only — never changes what runs.",
    ),
    (
        "progress",
        "`true` / `false`",
        "`true`",
        "`false` disables the progress bar, the eta line, and timing capture; "
        "`--progress` turns them back on for one invocation.",
    ),
    (
        "input",
        "`true` / `false`",
        "`true`",
        "`false` makes this project never prompt: a `confirm()` gate fails and "
        "an `ask()` without a default errors, rather than waiting for someone. "
        "`--input` allows prompting for one invocation, `--no-input` refuses "
        "it for one.",
    ),
    (
        "uv",
        "`true` / `false`",
        "`true`",
        "`false` disables both uv handoffs: re-running through the project's "
        "pinned footman, and the script environment of a tasks file carrying "
        "its own PEP 723 dependencies.",
    ),
    (
        "builtin",
        "list of entry points / `true`",
        "unset",
        "Mount `footman.tasks` entry points as built-in tasks — offered "
        "outside every project, ignored inside one (the cascade wins). "
        "`true` mounts every entry point installed with the runner. "
        "**User-level only.**",
    ),
    (
        "docs_url",
        "URL template",
        "unset",
        "Link task names in `--list`/`--tree`/`--help` to your generated "
        "task docs (and put a `docs_url` field on `--json` rows): `{path}` "
        "is the slash-joined task address, `{slug}` the dash-joined one. "
        "Terminal links ride the colour switch; piped output stays plain.",
    ),
    (
        "notes",
        "table of levels",
        "per kind",
        "Reclassify note kinds: `[tool.footman.notes]` maps `[task/]kind` "
        "patterns (either side `*`, most specific wins) to `trace` / `info` "
        "/ `warning` / `error` — `error` fails the task at its boundary. "
        "See the notes page.",
    ),
    (
        "completion.max_age",
        "duration / `off`",
        "`10m`",
        'Age before a background completion refresh (e.g. `"10m"`; `off` to disable).',
    ),
    (
        "fetch.backend",
        "`urllib` / `curl` / `httpx` / `requests` / `auto`",
        "`urllib`",
        "Download engine for `fetch()`.",
    ),
    (
        "shell.default",
        "`posix` / `native` / `pwsh` / a shell name",
        "`posix`",
        "What `run(shell=True)` resolves to. `posix` is bash, then sh — git "
        "bash on Windows.",
    ),
    (
        "gc",
        "`true` / `false`",
        "`true`",
        "`false` disables the cache collector (it runs at most daily). "
        "**User-level only** — ignored in a project config, with a note "
        "under `-v`.",
    ),
    (
        "cascade",
        "`none` / `repo` / `filesystem`",
        "`repo`",
        "How far discovery ranges for task files *and* config: this directory "
        "only, the repository, or across repositories. **User-level only**; "
        "`FOOTMAN_CASCADE` overrides it per invocation.",
    ),
)


class ConfigError(Exception):
    """A config TOML file exists but cannot be parsed."""


class CascadeError(ConfigError):
    """The cascade mode (env or config) is not one of the known walks."""


CASCADE_MODES = ("none", "repo", "filesystem")


def cascade_mode(cli_path: str | None = None) -> str:
    """How far discovery ranges for task files and config: `"none"` (the
    cwd's own files only), `"repo"` (the `.git` ceiling — the default,
    today's walk), or `"filesystem"` (past repo boundaries, up to the
    filesystem root).

    `FOOTMAN_CASCADE` overrides the `cascade` key — env over durable config,
    per-invocation over machine-wide. The key itself is user-level-only:
    what sits above a repo is the machine owner's layout, not any project's
    business — and the walk's own reach depends on it, so without an
    explicit `--config` it is read from the user-level file alone, before
    any walk. An unknown value is a taught error, never a silent default.
    """
    # This brand's spelling, in the read *and* in the error: naming
    # `FOOTMAN_CASCADE` at an `acme` user teaches a variable that does nothing.
    cascade_var = _paths.env_var("CASCADE")
    value, source = os.environ.get(cascade_var), cascade_var
    if not value:
        path = Path(cli_path).expanduser() if cli_path else _paths.footman_config_file()
        try:
            raw = _footman_table(path).get("cascade")
        except ConfigError:
            raw = None  # load_config warns about the malformed file itself
        if raw is None:
            return "repo"
        value, source = str(raw), f"`cascade` (in {path})"
    if value not in CASCADE_MODES:
        raise CascadeError(
            f"{source}: unknown cascade mode {value!r} — use 'none' (this "
            f"directory only), 'repo' (the repository, default), or "
            f"'filesystem' (across repositories)"
        )
    return value


# One tail for every "this file is not UTF-8" refusal, so the two spellings
# (a foreign BOM, a stray byte) teach the same fix.
_UTF8_ONLY = "TOML must be UTF-8; re-save the file as UTF-8"


def _decode(path: Path, raw: bytes) -> str:
    """*raw* as text, or `ConfigError` saying why it is not UTF-8.

    TOML's own spec makes UTF-8 mandatory, so a config in any other encoding
    is malformed by the format's rule and there is nothing to guess at:
    falling back to latin-1 the way rc files do would invent settings nobody
    wrote. The one unambiguous reading is a byte-order mark — a UTF-8 one
    (what Windows editors write) is stripped and the file reads normally, and
    any other mark names the encoding the refusal can teach against.
    """
    if raw.startswith(_encoding.UTF8_BOM):
        return raw.decode("utf-8-sig")
    sniffed = _encoding.sniff_bom(raw)
    if sniffed is not None:
        raise ConfigError(
            f"{path}: a {sniffed[0].upper()} byte-order mark — {_UTF8_ONLY}"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path}: not valid UTF-8 ({exc.reason} at byte {exc.start}) — {_UTF8_ONLY}"
        ) from exc


def _read_toml(path: Path, required: bool = False) -> dict[str, Any] | None:
    """Parse *path*; `None` if absent/unreadable, `ConfigError` if malformed.

    A missing file is normal (most directories have no config); a file that
    exists but doesn't parse — bad TOML or bad bytes — is a user mistake that
    must not be silently read as "no settings". When *required* (an explicit
    `--config`), an unreadable file is loud too, not silently skipped.
    """
    if path.exists() and not path.is_file():
        # A FIFO or a device would block `read_bytes` without bound; TOML
        # config is a regular file or it is nothing to read.
        if required:
            raise ConfigError(f"{path}: not a regular file — config is read from one")
        return None
    try:
        text = _decode(path, path.read_bytes())
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
    """The settings in *path* — `[tool.<name>]` for a pyproject, the whole
    file for anything else. Empty dict if absent/unreadable.

    The table is this brand's (`[tool.acme]`), so two branded CLIs in one
    repo read their own settings instead of fighting over one table."""
    data = _read_toml(path, required=required)
    if data is None:
        return {}
    if path.name == PYPROJECT:
        tool = data.get("tool")
        table = tool.get(_paths.config_table()) if isinstance(tool, dict) else None
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
    for name in (PYPROJECT, _paths.config_basename()):
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


def _parse_duration(value: object, *, strict: bool = False) -> int | None:
    """Seconds from a duration (`"10m"`, `"30s"`, `"1h"`, or a plain int); `None`
    to disable (`off`/`0`/negative).

    An unparseable value falls back to the default — or, under *strict*,
    refuses by name like every other config key. Both readings exist on
    purpose: the refresh child answers a keystroke and must never crash on
    a typo, while a real run is exactly where the typo should be taught.
    """
    if value is None:
        return DEFAULT_COMPLETION_MAX_AGE_S
    if isinstance(value, bool):  # bool is an int subclass — treat as on/off
        return DEFAULT_COMPLETION_MAX_AGE_S if value else None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("off", "none", ""):
            return None
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = units.get(text[-1:])
        try:
            n = int(text[:-1]) if unit else int(text)
        except ValueError:
            pass
        else:
            seconds = n * (unit or 1)
            return seconds if seconds > 0 else None
    if strict:
        raise ConfigError(
            f"`completion.max_age` expects a duration — seconds, or a number "
            f'with a unit ("30s", "10m", "1h", "1d"), or "off" (got {value!r})'
        )
    return DEFAULT_COMPLETION_MAX_AGE_S


def completion_max_age(cfg: dict[str, Any], *, strict: bool = False) -> int | None:
    """Seconds before the completion cache is considered stale, or `None` if
    disabled. Reads `[tool.footman] completion.max_age`; default 10 minutes.

    *strict* is the execution path's reading: a run refuses a mistyped
    value by name, the way `sort` and the other keys do. The refresh child
    keeps the quiet default — a background rebuild must never crash, and a
    keystroke is nobody's moment to learn about a config typo."""
    completion = cfg.get("completion")
    raw = completion.get("max_age") if isinstance(completion, dict) else None
    return _parse_duration(raw, strict=strict)


def sort_listing(cfg: dict[str, Any]) -> bool:
    """Whether listings show names alphabetically instead of in definition
    order. One setting for every human-facing walk of the tree: `--list`,
    `--tree`, help, and the docs pages. Never the run: what executes and in
    what order is the DAG's business, not a presentation setting."""
    raw = cfg.get("sort")
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ConfigError(
            f"`sort` expects true (list tasks alphabetically) or false "
            f"(definition order, the default) — got {raw!r}"
        )
    return raw


def user_level_value(key: str) -> Any:
    """One key from the user-level file alone, or None.

    For the settings whose meaning must not be steered by a nearer file:
    the user tasks file's *name* is the user's own writing, so a project's
    `tasks` key — which renames the project's file — must not reach into
    the user's home and silently drop the personal rung by looking for a
    file the user never wrote.
    """
    try:
        return _footman_table(_paths.footman_config_file()).get(key)
    except ConfigError:
        return None  # the load_config walk warns about it; quiet here


class BuiltinError(ConfigError):
    """The user-level `builtin` key is not a list of names or `true`."""


def user_builtin() -> tuple[str, ...] | None:
    """The user's own built-in entry points, from the user-level file alone.

    `builtin = ["acme.tasks"]` names them; `builtin = true` means "every
    `footman.tasks` entry point installed alongside this runner" — which is
    honest because installing one there (`uv tool install footman --with
    <dist>`) is already a deliberate act, and the runner's own environment
    is a small, intentional place. `None` means the key is absent, which is
    not the same as an empty list (a list that mounts nothing is a choice).

    User-level only, like `cascade`: what a machine offers *outside* every
    project is the machine owner's business, and a project that wants the
    same tasks mounts them the ordinary way, in its tasks file.

    Raises `BuiltinError` for a value that is neither, because a mount
    someone declared and footman ignored is a missing command with no
    explanation.
    """
    value = user_level_value("builtin")
    if value is None or value is False:
        return None
    if value is True:
        return installed_entry_points()
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise BuiltinError(
        f"builtin = {value!r} in {_paths.footman_config_file()}: name the "
        f'entry points to mount (builtin = ["acme.tasks"]), or say '
        f"builtin = true for every one installed with the runner"
    )


def installed_entry_points() -> tuple[str, ...]:
    """Every `footman.tasks` entry point importable from here, sorted.

    The `builtin = true` set. Sorted so the tree — and the manifest built
    from it — is the same on every run whatever order the metadata is read
    in.
    """
    from importlib.metadata import entry_points

    from footman.compose import ENTRY_POINT_GROUP

    return tuple(sorted({ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}))


def effective_builtin(brand: tuple[str, ...]) -> tuple[str, ...]:
    """The built-in set this invocation mounts: the brand's, then the
    user's, in that order and without duplicates.

    The brand's own declarations come first because they are the product;
    the user's are additions to it, not replacements — a branded CLI's
    built-ins stay built in whatever a user adds beside them.
    """
    user = user_builtin()
    if user is None:
        return brand
    seen = list(brand)
    seen += [name for name in user if name not in seen]
    return tuple(seen)


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
