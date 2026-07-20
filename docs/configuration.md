# Configuration

Settings are discovered the way tasks are: from files along the path to
your current directory, nearer files winning, with sensible behaviour when
no config exists at all. Everything on this page is optional.

## The precedence ladder

From weakest to strongest — each rung overrides the ones below it, key by
key:

1. **Built-in defaults.** No config, full behaviour.
2. **Your user-level file** — `~/.config/footman/config.toml` (honouring
   `XDG_CONFIG_HOME`; point `FOOTMAN_CONFIG` at a different file to move
   it). Personal defaults for every project: a purist's `uv = false`, a
   permanent `progress = false`.
3. **The project cascade** — walking from the repo root down to your
   current directory, each folder may contribute settings; nearer folders
   override farther ones. Within one folder, a standalone `footman.toml`
   overrides `[tool.footman]` in `pyproject.toml` — the customary
   dedicated-file-wins rule.
4. **`--config PATH`** — total control: the named file *replaces* the
   global file and the cascade entirely. You said exactly what applies.
5. **Environment variables** — `FOOTMAN_NO_UV`, `NO_COLOR`,
   `FOOTMAN_CACHE_DIR`, and friends always beat file config.
6. **Command-line flags** — `-s`, `-j`, `--no-progress`… always win.

The cascade is what makes monorepos comfortable: a package deep in the
tree can carry a two-line `footman.toml` that adjusts behaviour for that
subtree only —

```toml
# services/deep/package/footman.toml — this subtree runs inside the
# already-active parent environment; don't hand off to uv run.
uv = false
```

— while the repo root's `pyproject.toml` sets the shared defaults.

## The files

In a `pyproject.toml`, settings live under the tool table:

```toml
[tool.footman]
plugins = ["footman"]
sequential = false
```

A standalone `footman.toml` is the same keys, top-level:

```toml
plugins = ["footman"]
sequential = false
```

The user-level `~/.config/footman/config.toml` uses the standalone form.
Unknown keys are kept but ignored, so a newer setting never breaks an
older footman.

## Keys

| Key          | Meaning                                                   |
| ------------ | --------------------------------------------------------- |
| `tasks`      | Filename to look for in each folder (default `tasks.py`). |
| `sequential` | Run tasks one at a time by default.                       |
| `jobs`       | Max parallel tasks (default: cores - 1, never below 2).   |
| `plugins`    | `footman.tasks` entry points to mount as command groups (opt-in). |
| `progress`   | `false` permanently disables the progress bar, eta line, and timing capture. |
| `uv`         | `false` disables the uv handoff (a globally-installed `fm` re-running itself via `uv run` when the project's lockfile pins footman). |
| `completion.max_age` | Age before a background completion refresh (e.g. `"10m"`; `off` to disable). |
| `fetch.backend` | Download engine for `fetch()`: `urllib` (default), `curl`, `httpx`, `requests`, or `auto`. |
| `gc`         | `false` disables the daily cache collector. **User-level only**: honoured from the global file; in a project config it is ignored, with a note under `-v`. |

## Environment variables

| Variable            | Effect                                              |
| ------------------- | --------------------------------------------------- |
| `FOOTMAN_CONFIG`    | Path of the user-level config file.                 |
| `FOOTMAN_CACHE_DIR` | Moves every footman cache (completion manifests, timing history). |
| `FOOTMAN_NO_UV`     | Disables the uv handoff, regardless of any config.  |
| `FOOTMAN_NO_GC`     | Disables the cache collector, regardless of any config. |
| `NO_COLOR` / `TERM=dumb` | Disable ANSI styling, as everywhere.           |

See also [Monorepos & config](monorepos.md) for how the tasks cascade
itself composes, and the [CLI reference](reference.md) for the flags that
sit at the top of the ladder.
