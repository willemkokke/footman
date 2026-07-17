# CLI reference

## Global options

Global options bind to `fm` itself and go **before** the first task name
(`fm --json test`, not `fm test --json`):

| option                    | effect                                          |
| ------------------------- | ----------------------------------------------- |
| `-h`, `--help`            | help for `fm`, a group, or a task               |
| `-V`, `--version`         | print the version and exit                      |
| `-l`, `--list`            | list tasks (flat)                               |
| `--tree`                  | list tasks (grouped by command group)           |
| `--where TASK`            | print the task's source `file:line`             |
| `-n`, `--dry-run`         | print the parsed plan without running           |
| `-s`, `--sequential`      | run tasks one at a time (default is parallel)   |
| `-k`, `--keep-going`      | run every segment even if one fails             |
| `-q`, `--quiet`           | suppress the per-task summary                   |
| `-v`, `--verbose`         | replay captured `run()` output even on success  |
| `--no-color`              | disable ANSI colour                             |
| `--timings`               | show per-task durations                         |
| `--json`                  | machine-readable results (captures task output) |
| `-C`, `--directory PATH`  | run as if launched from PATH                    |
| `-f`, `--tasks-file PATH` | use one file, no cascade                        |
| `--config PATH`           | override config with a single TOML file         |
| `--install-completion SH` | install the completion hook (bash/zsh/fish/pwsh/nushell) |

`--help` is the one global allowed *anywhere* before `--`: `fm deploy --help`
is a read-only help request, never an execution. `fm --help` documents the
runner and its globals, `fm --help docs` a group, `fm --help deploy` a task.

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

| Import                       | Purpose                                              |
| ---------------------------- | ---------------------------------------------------- |
| `run(cmd, ...)`              | run a command or callable in the task context        |
| `parallel(*calls)`           | fan tasks/thunks out concurrently                    |
| `passthrough()`              | arguments after `--` on the command line             |
| `Context`, `use_context`     | the task context; install one from your own code     |
| `Many[T]`, `nosplit`         | one-or-many; opt a collection out of comma-splitting |
| `Annotated[T, suggest(fn)]`  | dynamic completion for a parameter                   |
| `exists`, `isfile`, `isdir`  | require a `Path` value to exist on disk              |
| `between(lo, hi)`            | inclusive numeric bounds (a bare `range` works too)  |
| `env("VAR")`                 | fall back to an environment variable (CLI > env > default) |
| `check(fn)`                  | custom post-coercion validator (`ValueError` rejects) |
| `tools.*`                    | typed wrappers for ruff, basedpyright, pytest, uv, … |
| `footman.testing`            | `Runner`/`Result` + `recording()` — see [Testing](testing.md) |
| `include`, `plugin`          | adopt tasks from modules/packages — see [Composing](composing.md) |
| `@task(when=…, reason=…)`    | disable-but-list a task that can't run here          |

## Configuration keys

Read from `[tool.footman]` in `pyproject.toml`, a standalone `footman.toml`, or
a file passed to `--config`. See [Monorepos & config](monorepos.md).

| Key          | Meaning                                                   |
| ------------ | --------------------------------------------------------- |
| `tasks`      | Filename to look for in each folder (default `tasks.py`). |
| `sequential` | Run tasks one at a time by default.                       |
| `plugins`    | `footman.tasks` entry points to mount as command groups (opt-in). |
