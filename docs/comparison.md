# Comparison

How footman compares to the incumbent Python task runners — measured on the
**same seven-task surface** (`lint`, `format`, `typecheck`, `test`, `check`,
`dist build`, `dist clean`) implemented five ways. The runnable head-to-head
lives in the repository's [`comparison/`](https://github.com/willemkokke/footman/tree/main/comparison)
directory; reproduce the numbers with `uv run python comparison/bench_compare.py`.

Versions measured: duty 1.9.0, invoke 3.0.3, poethepoet 0.48.0, typer 0.27.0,
CPython 3.13 on an M-series Mac.

!!! note "Verified, not assumed"

    Every claim below was checked against the tools directly. Notably: modern
    **duty already has** real `--flags`, chaining with flags, and required
    positionals — those are *not* where footman pulls ahead. The real
    differences are validation, discovery, dependencies, and completion latency.

## Completion latency — the headline

Cold-process wall time per `<TAB>`, mean of 15 fresh processes. **Δ import** is
the decisive column: completion time with a 0.25 s project-import cost minus
completion time with none. A runner that re-imports your tasks on every TAB
shows a ~0.25 s delta; one that answers from a cache shows ~0.

| runner  | completion (per TAB) | Δ import | re-imports per TAB?      |
| ------- | -------------------: | -------: | ------------------------ |
| footman |            **23 ms** |    ~0 ms | **no** (cached manifest) |
| poe     |                45 ms |    ~0 ms | no (reads TOML)          |
| duty    |               346 ms |   286 ms | **yes**                  |
| invoke  |               360 ms |   289 ms | **yes**                  |

- **duty and invoke re-import your whole project on every TAB** — their
  completion scripts call the tool, which loads your tasks module first. footman
  is **~15× faster** because completion reads a cached JSON manifest and never
  imports the framework or your code.
- **footman pays the import cost too — but only on the execution path.** Its
  `--list` (~313 ms) sits right alongside the others, because listing runs your
  code. Completion is the one thing that must be instant, and is.
- **poe is fast at completion for a different reason:** its tasks are shell
  strings in TOML, so there's no Python to import — at the cost of everything in
  the feature matrix below.

## Is "just write a typer app" too heavy?

typer is footman's closest feature-peer, and a real alternative. Isolated
cold-process import cost over the bare-interpreter baseline:

| import           | cost over baseline |
| ---------------- | -----------------: |
| `import footman` |          **+4 ms** |
| `import typer`   |          **+24 ms** |

typer's import genuinely is ~6× heavier (it ships its own parser plus `rich` and
`shellingham`). A single no-op launch is still nearly tied (footman ~38 ms,
typer ~40 ms) — footman spends its budget on real work, typer on imports. The
weight compounds where it matters: a typer app's **completion** re-invokes the
app, paying the typer import *plus* your project import on every TAB, while
footman answers from cache.

## Feature matrix

| capability                                  | footman | typer  | duty            | invoke        | poe      |
| ------------------------------------------- | ------- | ------ | --------------- | ------------- | -------- |
| Typed Python-function tasks                 | yes     | yes    | yes             | yes           | no       |
| No `ctx`/`c` boilerplate param              | yes     | yes    | no              | no            | n/a      |
| Real `--flags`                              | yes     | yes    | yes             | yes           | yes      |
| `Literal`/`Enum` → validated choices        | yes     | yes    | no              | no            | no       |
| Union / one-or-many / `dict[K,V]` params    | yes     | partial| no              | no            | no       |
| Native nested groups                        | yes     | manual | no              | manual        | no       |
| Zero-boilerplate discovery (module = group) | yes     | no     | no              | no            | no       |
| Separator-free chaining                     | yes     | no     | reserved-word   | reserved-word | seq task |
| Parallel-by-default DAG (`pre`/`post`)      | yes     | no     | serial pre/post | serial pre/post | yes    |
| `run()` capture / replay-on-failure         | yes     | no     | yes (`ctx.run`) | partial       | no       |
| Monorepo `tasks.py` cascade                 | yes     | no     | no              | no            | no       |
| Custom-branded CLI as a library             | yes     | yes    | no              | no            | no       |
| Completion without re-importing             | yes     | no     | no              | no            | yes\*    |
| Zero runtime dependencies                   | yes     | no     | no              | no            | no       |

\* poe avoids re-importing only because its tasks aren't Python functions.

**Where footman still trails:** shell-completion *installers* aren't wired yet
(the resolver works today via `fm --complete`), and typer's `--help` formatting
is richer. Both are on the roadmap.

## If you're coming from…

### duty

The closest migration. Drop the `ctx` parameter and shell out through `run()`:

```python
# duty
@duty
def lint(ctx, fix: bool = False):
    ctx.run("ruff check ." + (" --fix" if fix else ""))

# footman
@task
def lint(fix: bool = False):
    run("ruff check ." + (" --fix" if fix else ""))
```

Chaining (`duty format lint test` → `fm format lint test`) and `--flags` work
the same. You **gain** eager choice/type validation (duty accepts an invalid
`Literal`; footman rejects it), native nested groups, and instant completion.
Note the flag syntax: duty also accepts `lint fix=true`; footman uses
`--fix` / `lint --fix`.

### invoke

Drop the `c` parameter, and delete the manual `Collection` wiring — in footman a
module *is* a group and a `group()` opens a sub-command:

```python
# invoke: hand-assembled namespaces
ns = Collection(); ns.add_task(lint); ns.add_collection(dist)

# footman: nothing to assemble
dist = group("dist")
@dist.task
def build(): ...
```

`inv dist.build` becomes `fm dist build` (a space, not a dot). `c.run(...)` →
`run(...)`.

### typer

Your typed signatures port almost verbatim — footman reads the same annotations.
Delete the app object and the per-parameter `typer.Option`/`Argument` wrappers;
use plain defaults plus footman's `Annotated` markers (`suggest`, `Many`,
`nosplit`) where needed. `typer.Typer()` + `add_typer(sub)` → a module or a
`group()`. You lose typer's rich help (for now) and gain cached completion, zero
dependencies, and separator-free chaining.

### poe

Move each TOML task to a Python function — you trade declarative strings for real
Python, types, and validation:

```toml
# poe
[tool.poe.tasks.lint]
cmd = "ruff check ."
args = [{ name = "fix", options = ["--fix"], type = "boolean" }]
```

```python
# footman
@task
def lint(fix: bool = False):
    run("ruff check ." + (" --fix" if fix else ""))
```

You keep parallelism (poe's `[[parallel]]` → footman is parallel by default) and
pay the project import only on execution, never on completion.

### make / just

Recipes become `@task` functions and shell lines become `run(...)`; you keep
chaining and gain parallel-by-default execution and typed arguments. What you
give up is the file-target / up-to-date model — footman runs commands, it is not
a build system (see `doit` for that niche).

## Other runners

Not benchmarked here, and why: **taskipy** (pyproject shell aliases, no
Python-function tasks), **doit** (a build system with file-target/up-to-date
tracking — a different niche), **nox** / **tox** (environment & test-matrix
orchestration), and the non-Python **just** / **go-task** / **mise** / **make**
(great UX and completion, no Python dynamism). `uv`'s own task support is
[in design](https://github.com/astral-sh/uv/issues/5903) and will cover the
simple-named-command case; footman's niche is typed Python-function tasks with
real CLI semantics.
