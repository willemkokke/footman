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
   current directory, each directory may contribute settings; nearer directories
   override farther ones. Within one directory, a standalone `footman.toml`
   overrides `[tool.footman]` in `pyproject.toml` — the customary
   dedicated-file-wins rule.
4. **`--config=PATH`** — total control over config: the named file
   *replaces* the user file and the config cascade entirely (the tasks side
   has the same escape hatch, `-f/--tasks-file`). You said exactly what
   applies.
5. **Environment variables** — `FOOTMAN_NO_UV`, `FOOTMAN_CACHE_DIR`, and
   friends always beat file config. (`NO_COLOR` and `FORCE_COLOR` are
   gentler: they are `--color`'s *default* — a project's `color` key or an
   explicit `--color=` outranks them, because they speak for the terminal
   in general, not for this invocation.)
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

## Keys

Every key the runner recognises, rendered from its own list on each docs
build — so this table can neither invent a key nor miss one:

--8<-- "docs/_generated/config.md"

## Environment variables

| Variable            | Effect                                              |
| ------------------- | --------------------------------------------------- |
| `FOOTMAN_CONFIG`    | Path of the user-level config file.                 |
| `FOOTMAN_CACHE_DIR` | Moves every footman cache (completion manifests, timing history). |
| `FOOTMAN_NO_UV`     | Disables both uv handoffs (project and script environment), regardless of any config. |
| `FOOTMAN_NO_GC`     | Disables the cache collector, regardless of any config. |
| `FOOTMAN_CASCADE`   | Overrides the `cascade` key for one invocation (`none` / `repo` / `filesystem`). |
| `NO_COLOR` / `TERM=dumb` | Disable ANSI styling — for footman and for every tool it spawns, which footman tells to stay monochrome too. |
| `FORCE_COLOR`       | Force ANSI styling on, even piped (below `--color` and `[tool.footman] color` in the ladder). |

See also [Monorepos & config](monorepos.md) for how the tasks cascade
itself composes, and the [CLI reference](reference.md) for the flags that
sit at the top of the ladder.
