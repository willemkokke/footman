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
command groups, and no `ctx` boilerplate in task signatures. A measured
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
| `paths: list[Path] \| None`            | repeatable option (`--paths a --paths b`)            |
| `items: Many[str \| int]`              | one or more values, each coerced                     |
| `tags: Annotated[list[str], csv]`      | repeatable, and splits `--tags a,b,c`                |
| `env: dict[str, int]`                  | `--env KEY=VAL` pairs (repeatable; `csv`-splittable) |
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

Multi-value **options** repeat the flag (`--tag a --tag b`) so values stay opaque
(a value may contain commas). Opt into comma-splitting per option with `csv`:

```python
@task
def build(tags: Annotated[list[str], csv] = ()):
    "..."
```

```console
fm build --tags a,b,c        # ["a", "b", "c"]  — also works as --tags a --tags b
```

`csv` splits on `,` and nothing else; a value that must contain a comma uses the
repeated-flag form. (This is deliberately shell-portable — a comma-joined token
survives bash, zsh, and PowerShell intact, unlike a bare comma *separator*.)

### Dictionaries

`dict[K, V]` becomes a repeatable `KEY=VALUE` option; keys and values are typed
and validated like everything else, and it composes with `csv`:

```python
@task
def deploy(env: Annotated[dict[str, int | str], csv] | None = None,
           ports: dict[str, list[int]] | None = None):
    ...
```

```console
fm deploy --env DEBUG=1 --env HOST=prod          # {"DEBUG": 1, "HOST": "prod"}
fm deploy --env=DEBUG=1,HOST=prod                # same — csv splits the pairs
fm deploy --ports web=8080 --ports web=8443      # {"web": [8080, 8443]}  (repeat appends)
```

Values split on the *first* `=` (so a value may contain one); a scalar value's
duplicate key is last-wins, and a `dict[K, list[E]]` appends on repeat. Under
`csv` a value can't contain a comma — same opt-in trade as lists.

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

## Chaining

`fm format lint --fix test` runs three tasks in sequence — duty's muscle memory,
but with real flags. The split is driven by the manifest, so it is
deterministic; `+` is always available as an explicit boundary, and `--dry-run`
prints the parsed plan:

```console
$ fm --dry-run format lint --fix test
  globals: --dry-run
  -> format
  -> lint  --fix
  -> test
```

Tasks run stop-on-first-failure by default; `-k/--keep-going` runs them all.

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
by project path. The hot path is stdlib-only — it reads one file, parses JSON,
and walks the tree; it never imports footman or your tasks. That is the whole
latency story (measured cold-process on an M-series Mac):

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
[
  {"task": "test", "ok": true, "code": 0, "duration_ms": 812.4, "output": "...", "error": null}
]
```

Task output — including anything a subprocess writes — is captured into the
payload, so stdout stays pure machine-readable JSON. No incumbent has this.

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
| `-k`, `--keep-going`      | run every segment even if one fails             |
| `-q`, `--quiet`           | suppress the per-task summary                   |
| `--timings`               | show per-task durations                         |
| `--json`                  | machine-readable results (captures task output) |
| `-C`, `--directory PATH`  | run as if launched from PATH                    |
| `-f`, `--tasks-file PATH` | use a specific tasks file                       |

Accepted but not yet wired: `--install-completion SHELL` (prints guidance for
now), a per-command `--help` (currently lists tasks), `-v`/`--verbose`,
`--no-color`, and `--refresh-manifest` (the manifest already refreshes on every
run).

## Status

**Alpha.** The core is built and tested (~95% coverage): the registry,
signature→CLI manifest, the completion hot path, the chain grammar (all six
rules with taught errors), typed execution (unions, one-or-many, `dict[K, V]`,
`csv`, custom types via their constructors), dynamic completion, the
`run()`/`tools` execution layer with capture and replay-on-failure, and the
global-option set. What's next:

- shell-native completion installers (`--install-completion` for
  bash/zsh/fish/pwsh/nushell) — today the resolver works via `fm --complete`;
- a live TTY progress spinner and richer `tools.*` coverage;
- chain-aware completion, and DAG/parallel orchestration.

See the design notes for the full roadmap. MIT licensed.
