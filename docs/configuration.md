# Configuration

Settings are discovered the way tasks are: from files along the path to
your current directory, nearer files winning, with sensible behaviour when
no config exists at all. Everything on this page is optional.

## The precedence ladder

From weakest to strongest, each rung overriding the ones below it, key by
key:

1. **Built-in defaults.** No config, full behaviour.
2. **Your user-level file** — `~/.config/footman/config.toml` (honouring
   `XDG_CONFIG_HOME`; point `FOOTMAN_CONFIG` at a different file to move
   it). Personal defaults for every project: a purist's `uv = false`, a
   permanent `progress = false`.
3. **The project cascade** — walking from the repo root down to your
   current directory, each directory may contribute settings; nearer directories
   override farther ones. Within one directory, a standalone `footman.toml`
   overrides `[tool.footman]` in `pyproject.toml`, the customary
   dedicated-file-wins rule.
4. **`--config=PATH`** — total control over config: the named file
   *replaces* the user file and the config cascade entirely (the tasks side
   has the same escape hatch, `-f/--tasks-file`). You said exactly what
   applies.
5. **Environment variables** — `FOOTMAN_NO_UV`, `FOOTMAN_CACHE_DIR`, and
   friends always beat file config. (`NO_COLOR` and `FORCE_COLOR` are
   gentler: they are `--color`'s *default*, and a project's `color` key or an
   explicit `--color=` outranks them, because they speak for the terminal
   in general, not for this invocation.)
6. **Command-line flags** — `-s`, `-j`, `--no-progress`… always win.

The cascade is what makes monorepos comfortable: a package deep in the
tree can carry a two-line `footman.toml` that adjusts behaviour for that
subtree only:

```toml
# services/deep/package/footman.toml — this subtree runs inside the
# already-active parent environment; don't hand off to uv run.
uv = false
```

The repo root's `pyproject.toml` still sets the shared defaults.

## The files

In a `pyproject.toml`, settings live under the tool table:

```toml
[tool.footman]
tasks = "tasks.py"
sequential = false
```

A standalone `footman.toml` is the same keys, top-level:

```toml
tasks = "tasks.py"
sequential = false
```

The user-level `~/.config/footman/config.toml` uses the standalone form.
Unknown keys are kept but ignored, so a newer setting never breaks an
older footman.

## Where footman writes

Nothing footman writes lands inside your project. Three user-level
directories, each honouring its `XDG_*` variable:

| Directory | Default | What lives there |
| --------- | ------- | ---------------- |
| Cache | `~/.cache/footman/` (`XDG_CACHE_HOME`) | Derived data, safe to delete: completion manifests, timing history, `fetch()` downloads. The cache collector sweeps this directory and nothing else. |
| Data | `~/.local/share/` (`XDG_DATA_HOME`) | Durable files that survive every cache sweep — the installed completion hooks live at `fm/completion.*` in here. |
| Config | `~/.config/footman/` (`XDG_CONFIG_HOME`) | Your own writing: the user-level `config.toml` and the user-level tasks file, which travel together. |

The spellings are the same on every operating system — the convention uv,
ruff, and git's own XDG support follow — so on Windows everything lives
under `%USERPROFILE%`: `C:\Users\you\.cache\footman\`,
`C:\Users\you\.config\footman\`, and so on. The `XDG_*` variables move the
base directories on any platform, and the
[footman-specific variables](#environment-variables) below move each
corner on its own.

## Tasks outside every project

Some tasks only make sense *before* a project exists: create one, clone a
repo, log in. A package can ship them as a `footman.tasks` entry point, and
the user-level `builtin` key mounts them as built-in tasks — offered
wherever no project answers, and ignored inside one, where the cascade wins
outright.

```toml
# ~/.config/footman/config.toml
builtin = ["acme_devkit"]   # these entry points…
# builtin = true            # …or every one installed with the runner
```

`true` is the shorthand for "whatever I installed alongside `fm`", which is
honest because putting a package there
(`uv tool install footman --with acme-devkit`) is already a deliberate act.
The key is **user-level only**: what your machine offers outside every
project is yours, not any project's, and a project that wants the same tasks
mounts them the ordinary way in its own tasks file. A name that will not
mount is refused and says so; so is a value that is neither a list nor
`true`.

A package's tasks stay invisible out there until one says it belongs:
`@task(needs_project=False)` is the opt-in, and an unmarked task refuses
by name outside a project rather than going missing. `fm --plugins` shows
which rung mounted what — `built in` for the runner's own, `built in (your
config)` for yours.

Editing the key reaches <kbd>Tab</kbd> on the press after next, the way
every user-level edit does (see
[keeping the cache current](completion.md#keeping-the-cache-current)).

## Keys

Every key the runner recognises, rendered from its own list on each docs
build, so this table can neither invent a key nor miss one:

--8<-- "_generated/config.md"

## Environment variables

| Variable            | Effect                                              |
| ------------------- | --------------------------------------------------- |
| `FOOTMAN_CONFIG`    | Path of the user-level config file.                 |
| `FOOTMAN_CONFIG_DIR` | Moves footman's config corner whole (the config file and the user-level tasks file travel together). |
| `FOOTMAN_CACHE_DIR` | Moves every footman cache (completion manifests, timing history). |
| `FOOTMAN_DATA_DIR`  | Moves footman's data directory (durable files that survive a cache sweep). |
| `FOOTMAN_NO_UV`     | Disables both uv handoffs (project and script environment), regardless of any config. |
| `FOOTMAN_NO_GC`     | Disables the cache collector, regardless of any config. |
| `FOOTMAN_CASCADE`   | Overrides the `cascade` key for one invocation (`none` / `repo` / `filesystem`). |
| `FOOTMAN_STACKS_AFTER` | Seconds between stack dumps to stderr, for a run that stops moving ([Troubleshooting](troubleshooting.md#when-a-run-stops-moving)). |
| `NO_COLOR` / `TERM=dumb` | Disable ANSI styling, for footman and for every tool it spawns, which footman tells to stay monochrome too. |
| `FORCE_COLOR`       | Force ANSI styling on, even piped (below `--color` and `[tool.footman] color` in the ladder). |

Values for a *task's* environment are a different lane: `.env` files load
through the first-party `footman.env_files` plugin (`--env-file=.env`) —
see [the built-in on the hooks page](hooks.md#the-built-in-footmanenv_files)
— and a per-parameter `env("VAR")` marker reads a single variable as a
default.

See also [Monorepos & config](monorepos.md) for how the tasks cascade
itself composes, and the [CLI reference](reference.md) for the flags that
sit at the top of the ladder.
