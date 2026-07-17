# footman

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and the
UX of [typer](https://typer.tiangolo.com/): typed function signatures become
real flags and positionals, modules become nested command groups, and shell
completion answers from a cached manifest in **~19 ms — without importing your
code**.

```console
fm lint --fix
fm format lint --fix test          # a chain: three tasks, no separator
fm workspace mount --share <TAB>   # main  scratch  archive
```

Ships two console scripts: `footman` and the two-letter `fm`.

> [!WARNING]
> **Very early code.** footman is alpha and moving fast — the public API, the
> decorator surface, the manifest format, and the CLI grammar can all change
> without notice or a deprecation cycle. Pin an exact version if you build on it.

## Why

`duty` got a lot right — the `ctx.run` capture model, the tools wrappers, the
decorator ergonomics — and footman keeps those ideas. Where it pushes is the
parts that compound: completion that answers from a cache instead of
re-importing your whole project on every TAB (~15× faster in practice), eager
type and choice validation (including unions and dynamic value sets), native
command groups, no `ctx` boilerplate in task signatures, a DAG scheduler that
runs independent tasks in parallel by default (duty and invoke can't), and a
monorepo task cascade that merges a `tasks.py` per folder from the repo root
down to where you stand. A measured
head-to-head against duty, invoke, poe, and typer lives in
[`comparison/`](comparison/) — modern duty has real flags and chaining, so the
gap is validation, ergonomics, and completion latency, not grammar.

## Install

```console
uv add --dev footman        # or: pip install footman
```

Requires Python 3.11+. Zero runtime dependencies.

## Quick start

Write a `tasks.py` in your project root:

```python
from footman import task, group

@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ...

@task
def test(marker: str = "", *pytest_args):
    "Run the test suite (extra pytest args after --)."
    ...

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    "Serve the docs locally."
    ...
```

Then:

```console
fm lint --fix
fm docs serve --port 8001
fm --list
```

## Signatures become CLIs

A function's signature is introspected into real CLI semantics:

| Signature                              | CLI                                                  |
| -------------------------------------- | ---------------------------------------------------- |
| `fix: bool = False`                    | flag `--fix` / `--no-fix`                            |
| `mode: str = "loose"`                  | option `--mode VALUE`                                |
| `env: Literal["dev","prod"]`           | completable, eagerly-validated choices               |
| `count: int = 100`                     | typed option, validated at parse time                |
| `target: str \| int`                   | union — coerced by specificity (int before str)      |
| `paths: list[Path] \| None`            | repeatable or comma-separated (`--paths a,b`)        |
| `items: Many[str \| int]`              | one or more values, each coerced                     |
| `names: Annotated[list[str], nosplit]` | repeatable only — values may contain commas          |
| `env: dict[str, int]`                  | `--env KEY=VAL` pairs (repeatable or comma-separated)|
| `labels: dict[str, list[int]]`         | repeated key appends: `--labels p=1 --labels p=2`    |
| `project: Annotated[str, suggest(fn)]` | dynamic choices from `fn()`, cached + validated      |
| `template: Path` (no default)          | required positional (exact arity)                    |
| `*cmd: str`                            | variadic; also receives everything after `--`        |

Errors are product surface — they name the task, state the expectation, and
propose the fix:

```console
$ fm deploy check
fm: deploy: <env> must be one of dev|staging|prod — 'check' looks like the next
task; did you forget <env>?
```

### Unions and one-or-many values

A parameter can accept a **union** of types; footman coerces in specificity order
(most restrictive first, `str` last), so `str | int` turns `"5"` into `5` and
`"x"` into `"x"`. For **one or more** values use `Many[T]` (or `list[T]`) —
always a list, element type may itself be a union:

```python
@task
def build(targets: Many[str | int], jobs: int | str = "auto"):
    "Build one or more targets."
```

```console
fm build core web 3        # targets = ["core", "web", 3]  (positional, juxtaposed)
fm build core --jobs 8     # jobs = 8 (int); --jobs auto -> "auto"
```

Multi-value **options** work two ways, and collections comma-split by default:
repeat the flag (`--tag a --tag b`) *or* comma-separate one token (`--tag a,b`):

```python
@task
def build(tags: list[str] = ()):
    "..."
```

```console
fm build --tags a,b,c        # ["a", "b", "c"]  — also works as --tags a --tags b
```

Splitting is on `,` and nothing else (deliberately shell-portable — a
comma-joined token survives bash, zsh, and PowerShell intact, unlike a bare
comma *separator*). When a value must contain a comma, mark the parameter
`nosplit` — then only the repeated flag adds items:

```python
@task
def notify(lines: Annotated[list[str], nosplit] = ()):
    "..."
```

### Dictionaries

`dict[K, V]` becomes a repeatable `KEY=VALUE` option; keys and values are typed
and validated like everything else, and it comma-splits by default too:

```python
@task
def deploy(env: dict[str, int | str] | None = None,
           ports: dict[str, list[int]] | None = None):
    ...
```

```console
fm deploy --env DEBUG=1 --env HOST=prod          # {"DEBUG": 1, "HOST": "prod"}
fm deploy --env=DEBUG=1,HOST=prod                # same — commas split the pairs
fm deploy --ports web=8080 --ports web=8443      # {"web": [8080, 8443]}  (repeat appends)
```

Values split on the *first* `=` (so a value may contain one); a scalar value's
duplicate key is last-wins, and a `dict[K, list[E]]` appends on repeat. Mark a
dict `nosplit` when a value may itself contain a comma.

### Dynamic completion

Some values change occasionally — the projects in a monorepo, environments,
branches. Attach a completer with `Annotated[T, suggest(fn)]` (a bare callable
works too):

```python
from footman import task, suggest
from typing import Annotated

def projects() -> list[str]:
    return [p.name for p in Path("projects").iterdir() if p.is_dir()]

@task
def build(project: Annotated[str, suggest(projects)]):
    ...
```

footman runs `projects()` on the **execution path** — refreshing a cache the
completion hot path serves — so TAB stays instant (no import of the framework or
your code) while the candidates stay current. By default it is **strict**: the
value is validated against a *fresh* call, with a did-you-mean hint:

```console
$ fm build myprojet
fm: build: <project> must be one of api|core|web (got 'myprojet') — did you mean
'web'?
```

Pass `suggest(fn, strict=False)` for best-effort data that shouldn't block a run.
A completer shared across parameters runs once per invocation.

## Chaining and parallelism

`fm format lint --fix test` runs three tasks from one line — duty's muscle
memory, but with real flags. The split is driven by the manifest, so it is
deterministic; `+` is always available as an explicit boundary, and `--dry-run`
prints the parsed plan:

```console
$ fm --dry-run format lint --fix test
  globals: --dry-run
  -> format
  -> lint  --fix
  -> test
```

**Independent tasks run in parallel by default.** footman builds a DAG from the
chain and each task's declared dependencies, then runs everything that isn't
waiting on something else concurrently. Tasks are almost always I/O-bound (they
shell out through `run()`, releasing the GIL), so threads give real wall-clock
speedups without process isolation:

```sh
fm a b c            # three 1s tasks -> ~1.0s, not 3.0s
fm -s a b c         # -s/--sequential runs them one at a time -> ~3.0s
```

Output never interleaves: each task's stdout is buffered and flushed as one
contiguous block when it finishes.

**Dependencies with `pre` / `post`.** Declare prerequisites and follow-ups on the
task; footman schedules them (deduping shared deps, so a prerequisite pulled in
twice runs once) and skips a task whose prerequisite failed:

```python
@task(pre=[fmt, lint])      # fmt and lint run (concurrently) before check
def check(): ...

@task(post=[notify])        # notify runs after deploy succeeds
def deploy(): ...
```

**Fan out from inside a task** with `parallel()` — pass task functions directly,
or thunks when you need arguments. It runs them concurrently, waits, and fails
if any fail:

```python
from footman import task, parallel

@task
def check():
    parallel(lambda: format(check=True), lint, typecheck, test)
```

Tasks run stop-on-first-failure by default; `-k/--keep-going` runs every
independent branch even if one fails.

## Monorepos

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

- a **new name appends**, a name already defined higher up is **overridden** by
  the folder nearest you, and a **group present at both levels merges** (its
  tasks overlaid the same way);
- every task **runs from the folder of the file that defined it** — root's
  `build` always builds from `repo/`, `api`'s `serve` from `services/api/`,
  wherever you invoke it. (`run(cwd=…)` still overrides per command.)

Completion is cached **per directory**, so `<TAB>` in `services/api` offers the
merged set while the repo root offers only its own. `-f/--tasks-file` is the
escape hatch: it loads exactly one file, no cascade.

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

# repo/services/api/footman.toml   (no pyproject here — a standalone file)
sequential = true      # this package prefers serial runs
```

`--config PATH` points at a single TOML file that overrides everything else.
Unknown keys are ignored, so a newer setting never breaks an older footman.

## Rebrand it as your own CLI

`fm` and `footman` are just the default-branded instance of a public `App`.
Point your own console script at an `App` with your project's names and version,
and every message the user sees uses *your* branding — so an internal tool can
ship under its own name (say `acme`) while being footman underneath:

```python
# acme/cli.py
from footman import App

app = App(name="Acme", prog="acme", version="1.4.0")

def main() -> None:
    raise SystemExit(app.run())
```

```toml
# acme/pyproject.toml
[project.scripts]
acme = "acme.cli:main"
```

```console
$ acme --version
Acme 1.4.0
$ acme nope
acme: expected a task name, got 'nope' (know: build, test, deploy)
```

`name` is the long/display name (the `--version` banner), `prog` the short
command name (error prefix and hints), `version` your own version (optional —
footman's is used otherwise). Tasks and completion are unchanged: `acme`
discovers the `tasks.py` cascade exactly like `fm`, and `acme --complete` stays
on the same stdlib-only fast path.

## Running tools

Task bodies run tools through `run()` and the typed `tools.*` wrappers. `run()`
captures output and stays quiet on success, **replaying it only on failure** —
so a green run is calm and a red one shows exactly what broke:

```python
from footman import task, run, tools

@task
def check():
    tools.ruff("check", "src", fix=False)   # subprocess (ruff is a binary)
    tools.pytest("-x")                        # in-process via pytest.main
    run("mkdocs build --strict")              # any command; a callable also works
```

- **In-process where possible** — Python-native tools (pytest) skip the process
  spawn; binaries (ruff, basedpyright, uv) run as subprocesses. Either way the
  wrapper gives you typed, autocompletable options and a typo-proof command line.
- **`run()`** takes a command (string or list) or a Python callable; it raises on
  a non-zero exit (`nofail=True` returns the code instead), honours `--dry-run`
  (prints the command instead of running it), and records a step for `--json`.
- **No `ctx` needed** — `run()` and `passthrough()` read the current task's
  context implicitly, so `def check():` stays boilerplate-free. Declare a first
  `ctx: Context` parameter only if you want the object; footman keeps it out of
  the CLI mapping:

```python
from footman import Context

@task
def test(ctx: Context):
    tools.pytest(*ctx.passthrough)          # fm test -- -k mytest -x
```

Under `--json`, every `run()` becomes a structured step (command, code, duration,
captured output) inside the task's entry.

## Instant completion

Completion answers from a JSON manifest cached under your XDG cache dir, keyed
by directory (so each folder of a monorepo caches its own merged cascade). The
hot path is stdlib-only — it reads one file, parses JSON, and walks the tree; it
never imports footman or your tasks. That is the whole latency story (measured
cold-process on an M-series Mac):

| variant                                   |   mean |
| ----------------------------------------- | -----: |
| interpreter startup (floor)               | 14 ms  |
| standalone resolver (baked-in path)       | 19 ms  |
| `python -m footman --complete`            | 24 ms  |

The manifest is regenerated for free on any execution-path run (footman is
importing your code anyway) and rewritten only when the command surface
actually changed.

Run `uv run python scripts/bench_completion.py` to reproduce.

## `--json` for CI and agents

```console
$ fm --json test
{
  "schema": 1,
  "results": [
    {"task": "test", "ok": true, "code": 0, "duration_ms": 812.4, "output": "...", "steps": [], "error": null}
  ]
}
```

Task output — including anything a subprocess writes — is captured into the
payload, so stdout stays pure machine-readable JSON. No incumbent has this.

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
| `--install-completion SH` | install the bash/zsh/fish completion hook       |

`--help` is the one global allowed *anywhere* before `--`: `fm deploy --help`
is a read-only help request, never an execution. `fm --help` documents the
runner and its globals, `fm --help docs` a group, `fm --help deploy` a task.

## Status

**Alpha.** The core is built and tested (coverage gated in CI): the registry,
signature→CLI manifest, the completion hot path with chain-aware resolution
and shell installers (`--install-completion bash|zsh|fish`), the chain grammar
(all six rules with taught errors), typed execution (unions, one-or-many,
`dict[K, V]`, comma-splitting with `nosplit`, custom types via their
constructors, validation markers), dynamic completion, the
`run()`/`tools` execution layer with capture and replay-on-failure, the DAG
scheduler (parallel-by-default with `pre`/`post` dependencies, `parallel()`, and
grouped non-interleaved output), the monorepo cascade (root-to-cwd task merge
with defining-dir cwd and per-directory completion), its config discovery
(`[tool.footman]` / `footman.toml` / `--config`), task composition
(`when=`, `include()`, `footman.tasks` entry-point plugins), the
`footman.testing` harness with pytest fixtures, and per-task `--help`.
What's next:

- a live TTY progress spinner and richer `tools.*` coverage;
- pwsh/nushell completion.

See [ROADMAP.md](ROADMAP.md) for the full road to 1.0. MIT licensed.
