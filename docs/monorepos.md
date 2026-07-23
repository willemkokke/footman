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

!!! note "How footman finds the top of the cascade"

    The walk goes up from your current directory and stops at a **ceiling**,
    then collects downward. The rules, in order:

    1. **The ceiling is the nearest `.git` at or above your cwd.** That is the
       repo edge, and where both the task cascade and the [config
       search](#configuration) start.
    2. **No `.git`? The nearest ancestor holding a project marker** — a
       `pyproject.toml`, a `footman.toml`, or a `tasks.py` — is the ceiling
       instead, so a single-package checkout with no VCS still has a sensible
       top.
    3. **Nothing above you at all? Your current directory is the ceiling** — the
       walk never climbs past your home into the filesystem root looking for
       one.

    From that ceiling **down to your cwd**, footman loads every `tasks.py` that
    exists — root first, cwd last, so nearer files override — skipping folders
    that have none. The filename is the `tasks` [config key](#configuration), so
    a repo can look for something other than `tasks.py`.

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

## Sibling helpers

Each `tasks.py` may `import helpers` (or any module) from **its own folder** at
the top of the file — footman searches that folder first and gives each file
its own copy, so `services/api/helpers.py` and the root `helpers.py` never
collide. Import at module top; a deferred `import` inside a task body, in a
project with same-named helpers in several folders, is a known limitation.

## Completion is per directory

The completion manifest is cached **per directory**, so <kbd>Tab</kbd> in
`services/api` offers the merged set while the repo root offers only its own.

??? tip "Load exactly one file"

    `-f/--tasks-file PATH` loads a single tasks file, with **no tasks cascade** —
    the tasks-side mirror of `--config PATH` for config. The two are orthogonal:
    `-f` alone still reads the cwd's config (and any plugins it declares add
    tasks), so pass both for total control. <kbd>Tab</kbd> after `-f <file>`
    completes *that file's* tasks: a `-f` run caches its manifest under a key
    pairing the file with the cwd — separate from the plain-cwd cache, which it
    never touches (so plain <kbd>Tab</kbd> keeps describing the real cascade).

## Configuration

footman discovers behavioural settings with the same upward walk it uses for
tasks files. It reads `[tool.footman]` from `pyproject.toml` and a standalone
`footman.toml` (whole-file), from the repo root down to your cwd — **nearer
files win**, so a package can override repo-wide defaults:

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

The full key table — and the whole precedence ladder, user-level file
included — lives on the [Configuration](configuration.md) page.

A local task that overrides an inherited one can still *call* it:
`inherited()` is footman's `super()` — see the
[cookbook recipe](cookbook.md#extend-an-inherited-task-instead-of-replacing-it).
