# footman vs. the incumbents

The **same seven-task surface** (`lint`, `format`, `typecheck`, `test`, `check`,
`dist build`, `dist clean`) implemented five ways, so we can compare completion
latency, launch overhead, invocation syntax, and verboseness on equal footing.

- [`footman/tasks.py`](footman/tasks.py) — footman
- [`typer/app.py`](typer/app.py) — [typer](https://typer.tiangolo.com/) ("just write a typer app")
- [`duty/duties.py`](duty/duties.py) — [duty](https://pawamoy.github.io/duty/)
- [`invoke/tasks.py`](invoke/tasks.py) — [invoke](https://www.pyinvoke.org/)
- [`poe/pyproject.toml`](poe/pyproject.toml) — [poethepoet](https://poethepoet.natn.io/)

Each Python-based file imports [`_project.py`](_project.py), which sleeps to
simulate a real project's import cost. Reproduce with:

```console
uv run --group comparison python comparison/bench_compare.py
```

## Timing (measured, not assumed)

Cold-process wall time, mean of 15 fresh processes each (M-series Mac, CPython
3.13). The **Δ import** column is the decisive part: it is completion time with
a 0.25 s project-import cost minus completion time with none. A runner that
re-imports your tasks on every TAB shows a ~0.25 s delta; one that does not
shows ~0.

| runner  | completion (per TAB) | Δ import | re-imports per TAB?      | `--list` |
| ------- | -------------------: | -------: | ------------------------ | -------: |
| footman |            **23 ms** |    ~0 ms | **no** (cached manifest) |   313 ms |
| duty    |               346 ms |   286 ms | **yes**                  |   343 ms |
| invoke  |               360 ms |   289 ms | **yes**                  |   355 ms |
| poe     |                45 ms |    ~0 ms | no (reads TOML)          |    51 ms |

Reading it:

- **duty and invoke re-import the project on every TAB.** This is measured
  here, independently — duty's `completions.bash` calls `duty --complete`, which
  loads `duties.py` (and therefore the whole project) before answering. The
  286 ms delta is that import. At completion time footman is **~15× faster**.
- **footman pays the import cost too — but only on the execution path.** Its
  `--list` is 313 ms, right alongside the others, because listing runs your
  code. Completion is the only thing that must be instant, and it is: it reads a
  cached JSON manifest and never imports the framework or your tasks.
- **poe is also fast at completion — for a different reason.** Its tasks are
  shell/command strings in TOML, so completion parses the TOML and never imports
  Python at all. (poe *can* call Python via `script`/`expr` tasks, but the CLI is
  declared in the TOML `args` table, never derived from a function signature.)
  The trade-off is the rest of this page: no signature→CLI, no eager validation.

### Execution overhead

Cold-process wall time to run a no-op task. At **cost 0** this is the runner's
own dispatch overhead on top of your task's real work; at **cost 0.25 s** it also
includes the project import the Python-based runners pay on every run.

| runner  | framework overhead (@0) | with project import (@0.25 s) |
| ------- | ----------------------: | ----------------------------: |
| footman |                   38 ms |                        313 ms |
| typer   |                   40 ms |                        320 ms |
| duty    |                   69 ms |                        348 ms |
| poe     |                   76 ms |                         67 ms |
| invoke  |                   78 ms |                        356 ms |

- On execution *with* a real project, everyone who imports Python tasks pays for
  it (~0.25 s here) — footman included. Execution is dominated by your project;
  **completion is the path where the architecture matters.**
- poe stays flat because it never imports the project. (Its `noop` is an
  in-process `expr`; a `cmd` task would add a subprocess spawn on top.)

### Launch overhead: is a typer app too heavy?

The reason for measuring launch and not just completion — "just write a typer
app" is a real alternative, and typer has a reputation for slow startup. Isolated
cold-process import cost over the bare-interpreter baseline (warm `.pyc`):

| import | cost over baseline |
| --- | ---: |
| `import footman` | **+3.7 ms** |
| `import typer` | **+24 ms** |

typer's import genuinely is ~6–7× heavier — the reputation is real. (typer 0.27
ships its own parser + `rich` + `shellingham`; it no longer depends on `click`.)
Yet the full no-op *launch* above is nearly tied (footman 38 ms, typer 40 ms):
footman spends its budget on real work (manifest sync + parse + bind) while typer
spends it on imports. So footman didn't "get bad" on launch — it's on par per
command. typer's weight resurfaces where it **compounds**, not on a single call:

- **Completion**: a typer app's completion re-invokes the app, paying the +24 ms
  typer import *plus* your project import on **every TAB** — footman answers from
  the cached manifest (~25 ms, no imports).
- **Cold cache** (fresh CI container, first run after install,
  `PYTHONDONTWRITEBYTECODE`): typer pays a one-time bytecode compile of its
  module tree (~100 ms+); footman has almost nothing to compile.
- **Dependencies**: typer pulls in `rich` + `shellingham`; footman ships zero, so
  nothing to install, resolve, or keep out of conflict.

## Syntax, side by side

A flag'd task and a nested group:

**footman** — no `ctx`, real `--flags`, native groups:

```python
@task
def lint(fix: bool = False):
    "Lint with ruff."

dist = group("dist", help="Build and publish")

@dist.task
def build():
    "Build the sdist and wheel."
```

```console
fm lint --fix
fm dist build
fm format lint --fix test      # chain, no separator
```

**duty** — must accept `ctx`; real flags but no choice validation; no groups:

```python
@duty
def lint(ctx, fix: bool = False):
    "Lint with ruff."
    ctx.run("ruff check src tests" + (" --fix" if fix else ""))

@duty
def dist_build(ctx):
    "Build the sdist and wheel."
    ctx.run("uv build")
```

```console
duty lint --fix                # real flags work — as does `duty lint fix=true`
duty dist-build
duty format lint --fix test    # chains AND takes flags (verified, duty 1.9.0)
duty rel env=nonsense          # BUT: invalid choice accepted, not validated
```

**invoke** — explicit `c`, real `--flags`, groups assembled by hand:

```python
@task
def lint(c, fix=False):
    "Lint with ruff."
    c.run("ruff check src tests" + (" --fix" if fix else ""))

@task(name="build")
def dist_build(c):
    "Build the sdist and wheel."
    c.run("uv build")

dist = Collection("dist")
dist.add_task(dist_build)
ns = Collection()
ns.add_task(lint)          # …repeat for every task
ns.add_collection(dist)
```

```console
inv lint --fix
inv dist.build
inv format lint test
```

**poe** — tasks are strings in TOML; every option needs its own table:

```toml
[tool.poe.tasks.lint]
cmd = "ruff check src tests"
args = [{ name = "fix", options = ["--fix"], type = "boolean" }]

[tool.poe.tasks]
dist-build = "uv build"
```

```console
poe lint --fix
poe dist-build
```

## Feature matrix

| capability                                  | footman | typer  | duty            | invoke        | poe      |
| ------------------------------------------- | ------- | ------ | --------------- | ------------- | -------- |
| Typed Python-function tasks                 | yes     | yes    | yes             | yes           | no       |
| No `ctx`/`c` boilerplate param              | yes     | yes    | no              | no            | n/a      |
| Real `--flags`                              | yes     | yes    | yes             | yes           | yes      |
| `Literal`/`Enum` → validated choices        | yes     | yes    | no              | no            | no       |
| Native nested groups                        | yes     | manual | no              | manual        | no       |
| Zero-boilerplate discovery (module = group) | yes     | no     | no              | no            | no       |
| Separator-free chaining                     | yes     | no     | reserved-word   | reserved-word | seq task |
| Completion without re-importing             | yes     | no     | no              | no            | yes*     |
| Zero runtime dependencies                   | yes     | no     | no              | no            | no       |
| Output capture / replay-on-failure          | yes     | no     | yes (`ctx.run`) | partial       | no       |
| DAG / parallel-by-default                   | yes     | no     | serial          | serial        | yes      |

\* poe avoids re-importing only because its tasks aren't Python functions.

**typer is footman's closest feature-peer, not a laggard.** It matches footman on
the typed-CLI basics — no `ctx`, real flags, nested groups (via `add_typer`), and
`Enum`/`Literal` validation. The contrast is *architectural*, not features:
footman discovers tasks with zero app wiring (a module *is* a group), ships zero
dependencies, chains segments without a separator, and — above all — answers
completion from a cache instead of re-importing typer + your project on every
TAB. See the launch-overhead section for the numbers.

**Duty is closer than its reputation.** Testing duty 1.9.0 directly (not trusting
prior notes) showed it already supports real `--flags`, chaining with flags, and
bare required positionals — so those are *not* where footman pulls ahead.
footman's verified edges over current duty are: no `ctx` boilerplate, native
nested groups, **eager choice/type validation** (duty accepts an invalid
`Literal` value; footman rejects it), `Literal`/`Enum`-driven completion, and
completion that doesn't re-import your project (~15× faster per TAB).

**Where footman is still behind:** shell-completion installers aren't wired yet
(the resolver works via `fm --complete`), and typer's `--help` formatting is
richer. Both are on the roadmap. (footman has since gained a `run()`/`tools`
capture layer and a parallel-by-default DAG scheduler — the matrix above
reflects that.)

## Other Python task runners worth contrasting

Measured above: **duty**, **invoke** (pyinvoke), **poethepoet**, and **typer**
(the DIY baseline). Others in the space, and why they are or aren't
apples-to-apples:

- **taskipy** — `[tool.taskipy.tasks]` shell aliases in pyproject. Like poe's
  string model but simpler; no Python-function tasks, no real completion.
- **doit** — a build system (targets, file-deps, up-to-date checks, incremental
  runs). Owns the "rebuild only what changed" niche footman doesn't target;
  dated UX.
- **nox** — `@nox.session` Python functions, but the niche is *environments/test
  matrices*, not a general command runner. Closest Python-function cousin after
  invoke.
- **tox** — env orchestration via config; not a general task runner.
- **uv `[tool.uv.tasks]`** — in design (astral-sh/uv#5903). Will eat the "simple
  named command" segment; footman's defensible niche is typed Python-function
  tasks with real CLI semantics.
- **Non-Python, common baselines:** `just`, `go-task`, `mise`, `make` — great UX
  and completion, zero Python dynamism.

Adding a `taskipy`, `doit`, or `nox` equivalent here is a small lift if we want
more data points — say the word.
