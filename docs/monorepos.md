# Monorepos & config

## The task cascade

In a monorepo you rarely want one giant tasks file. footman collects every
`tasks.py` from the **repo root** (the nearest `.git` above you) down to your
current directory and merges them into one command set:

```text
repo/            .git  pyproject.toml  tasks.py   →  build  test  lint
  services/
    api/         tasks.py                         →  serve  migrate  build*
```

Standing in `services/api`, `fm` sees `build*` (the local override), `test`,
`lint`, `serve`, and `migrate`. The rules are the ones you'd guess:

- a **new name appends**;
- a name already defined higher up is **overridden** by the folder nearest you;
- a **group present at both levels merges** — its tasks are overlaid the same
  way.

## Where a task runs

Every task **runs from the folder of the file that defined it**. Root's `build`
always builds from `repo/`, `api`'s `serve` from `services/api/`, wherever you
invoke it:

```sh
cd services/api
fm build      # the api override, running in services/api/
fm test       # inherited from the root, running in repo/
```

`run(cwd=…)` still overrides the working directory per command.

## Completion is per directory

The completion manifest is cached **per directory**, so <kbd>Tab</kbd> in
`services/api` offers the merged set while the repo root offers only its own.

!!! tip "Load exactly one file"

    `-f/--tasks-file PATH` is the escape hatch: it loads a single file, with no
    cascade. It never rewrites the directory's cached completion manifest, so a
    one-off `-f` run leaves <kbd>Tab</kbd> describing the real cascade.

## Configuration

Behavioural settings are discovered by the same upward walk. footman reads
`[tool.footman]` from `pyproject.toml` and a standalone `footman.toml`
(whole-file), from the repo root down to your cwd — **nearer files win**, so a
package can override repo-wide defaults:

```toml
# repo/pyproject.toml
[tool.footman]
tasks = "tasks.py"     # the filename to look for in the cascade
sequential = false     # run tasks one at a time by default
```

```toml
# repo/services/api/footman.toml   (no pyproject here — a standalone file)
sequential = true      # this package prefers serial runs
```

Within one directory, `footman.toml` wins over `pyproject.toml`'s
`[tool.footman]`. `--config PATH` points at a single TOML file that overrides
everything else. Unknown keys are ignored, so a newer setting never breaks an
older footman.

### Recognised keys

| Key          | Meaning                                                    |
| ------------ | ---------------------------------------------------------- |
| `tasks`      | Filename to look for in each folder (default `tasks.py`).  |
| `sequential` | Run tasks one at a time by default.                        |
