# CLI reference

## Global options

Global options bind to `fm` itself and go **before** the first task name
(`fm --json test`, not `fm test --json`):

| option                    | effect                                          |
| ------------------------- | ----------------------------------------------- |
| `-V`, `--version`         | print the version and exit                      |
| `-l`, `--list`            | list tasks (flat)                               |
| `--tree`                  | list tasks (grouped by command group)           |
| `--where TASK`            | print the task's source `file:line`             |
| `-n`, `--dry-run`         | print the parsed plan without running           |
| `-s`, `--sequential`      | run tasks one at a time (default is parallel)   |
| `-k`, `--keep-going`      | run every segment even if one fails             |
| `-q`, `--quiet`           | suppress the per-task summary                   |
| `--timings`               | show per-task durations                         |
| `--json`                  | machine-readable results (captures task output) |
| `-C`, `--directory PATH`  | run as if launched from PATH                    |
| `-f`, `--tasks-file PATH` | use one file, no cascade                        |
| `--config PATH`           | override config with a single TOML file         |

Accepted but not yet wired: `--install-completion SHELL` (prints guidance for
now), a per-command `--help` (currently lists tasks), `-v`/`--verbose`,
`--no-color`, and `--refresh-manifest` (the manifest already refreshes on every
run).

## Decorator surface

```python
from footman import task, group

@task                       # bare
def build(): ...

@task(name="ci-build")      # override the command name
def build(): ...

@task(pre=[fmt], post=[notify])   # dependencies (run before / after)
def check(): ...

release = group("release", help="Cut a release")

@release.task
def wheel(): ...
```

## Runtime helpers

| Import                     | Purpose                                              |
| -------------------------- | ---------------------------------------------------- |
| `run(cmd, ...)`            | run a command or callable in the task context        |
| `parallel(*calls)`         | fan tasks/thunks out concurrently                    |
| `passthrough()`            | arguments after `--` on the command line             |
| `Context`                  | the task's context object (opt-in first parameter)   |
| `Many[T]`, `nosplit`       | one-or-many; opt a collection out of comma-splitting |
| `suggest[T, fn]`           | dynamic completion for a parameter                   |
| `tools.*`                  | typed wrappers for ruff, basedpyright, pytest, uv, … |

## Configuration keys

Read from `[tool.footman]` in `pyproject.toml`, a standalone `footman.toml`, or
a file passed to `--config`. See [Monorepos & config](monorepos.md).

| Key          | Meaning                                                   |
| ------------ | --------------------------------------------------------- |
| `tasks`      | Filename to look for in each folder (default `tasks.py`). |
| `sequential` | Run tasks one at a time by default.                       |
