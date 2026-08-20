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

**Read this first: most of what follows is academic.** Your test suite takes
thirty seconds; your linter takes four; a docker build takes minutes. Against
that, every runner on this page is a rounding error, and a dozen milliseconds
between them changes nothing about your day. Nobody should choose a task
runner on the launch-overhead table, and we are not asking you to.

One number here is different, and it is the reason this page exists:
**completion latency**. A <kbd>Tab</kbd> press happens between your fingers
and your thought, dozens of times an hour, and it is the one place where a
task runner's own cost is the *whole* cost — there is no real work behind it
to hide in. 30 ms feels instant; 360 ms feels like the shell is thinking, and
you stop pressing <kbd>Tab</kbd>. That difference is architectural, not a
matter of tuning: a runner that re-imports your project to answer a keystroke
cannot get there from here, and one that reads a cached manifest cannot lose.

So: the **Δ import** column below is the finding. The launch and orchestration
tables are included for honesty — a page that showed only its best number
would deserve the suspicion — and because the orchestration gap *does* grow
teeth once the steps are real (four two-second checks, not four sleeps).

Cold-process wall time: median of 40 fresh processes after three discarded
warmups, `±` half the p10–p90 spread (M-series Mac, CPython 3.14). Means over
15 runs with no warmup — what this page used to report — could not reproduce
themselves on a machine doing anything else. The **Δ import** column is the decisive part: it is completion time with
a 0.25 s project-import cost minus completion time with none. A runner that
re-imports your tasks on every TAB shows a ~0.25 s delta; one that does not
shows ~0.

| runner  | completion (per TAB) | Δ import | re-imports per TAB?      | `--list` |
| ------- | -------------------: | -------: | ------------------------ | -------: |
| footman |          **20±1 ms** |     0 ms | **no** (cached manifest) |  307±5 ms |
| poe     |              45±1 ms |     0 ms | no (reads TOML)          |   60±4 ms |
| invoke  |            336±20 ms |   263 ms | **yes**                  | 359±19 ms |
| duty    |             349±9 ms |   289 ms | **yes**                  |      378 ms |

Reading it:

- **duty and invoke re-import the project on every TAB.** This is measured
  here, independently — duty's `completions.bash` calls `duty --complete`, which
  loads `duties.py` (and therefore the whole project) before answering. The
  289 ms delta is that import — the whole of it, on every keystroke.
- **footman pays the import cost too — but only on the execution path.** Its
  `--list` is ~307 ms, right alongside the others, because listing runs your
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
| typer   |                   75 ms |                        331 ms |
| duty    |                   75 ms |                        354 ms |
| footman |                   82 ms |                        346 ms |
| invoke  |                   87 ms |                        363 ms |
| poe     |                   93 ms |                         85 ms |

- **On launch overhead the Python runners are a near-tie**, and footman sits in
  the middle of it: a dozen milliseconds separate typer, duty, footman and
  invoke, which is within the run-to-run spread on a laptop. footman spends
  its share on things the others do not do at all — reading the config
  cascade, resolving the task tree it will parse the command line against —
  and that is the trade, not a win to claim.
- On execution *with* a real project, everyone who imports Python tasks pays for
  it (~0.25 s here) — footman included. Execution is dominated by your project;
  **completion is the path where the architecture matters.**
- poe stays flat because it never imports the project. (Its `noop` is an
  in-process `expr`; a `cmd` task would add a subprocess spawn on top.)

### Orchestration: the same `check`, composed each tool's way

The composite everyone actually runs all day: four check steps, each an
identical in-process 0.5 s sleep (the honest stand-in for an I/O-bound tool
run — a real lint/test step spawns a subprocess and waits, which parallelises
exactly like a sleep). Each tool composes them **idiomatically**, and
fairness cuts both ways: a tool with parallel support gets to use it, a tool
without runs its native serial form.

Floors: 0.5 s parallel, 2.0 s serial. Reproduce with
`uv run --group comparison python comparison/bench_check.py`.

| runner  | composition                    | wall (mean) | overhead over floor |
| ------- | ------------------------------ | ----------: | ------------------: |
| footman | parallel (pre-deps, *default*) |  **592 ms** |               92 ms |
| poe     | parallel (`parallel` task)     |      617 ms |              117 ms |
| typer   | serial (no orchestration)      |     2080 ms |               80 ms |
| duty    | serial (pre-duties)            |     2131 ms |              131 ms |
| invoke  | serial (pre-tasks)             |     2210 ms |              210 ms |

- **The gap that matters is ~3.5×, and it isn't overhead — it's architecture**
  (4× before overhead: a 0.5 s parallel floor against a 2.0 s serial one).
  duty and invoke run pre-tasks serially (no parallel option exists to turn
  on); the same four steps cost the sum instead of the max.
- **poe genuinely has parallelism** (a dedicated `parallel` task type since
  0.48) — credit where due. The difference is spelling: in poe you declare a
  `parallel` composite per case; footman's `pre`-deps are parallel *by
  default* and go serial only when you ask (`-s`).
- **typer gives you no orchestration at all** — four calls in a row. You can
  hand-roll a `ThreadPoolExecutor` in the command body, but then you wrote
  the scheduler yourself, which is the job a task runner exists to do.

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
Yet the full no-op *launch* above is a near-tie across the Python runners (typer and duty 75 ms, footman 82 ms, invoke 87 ms):
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
| Native nested groups                        | yes     | yes    | no              | manual        | no       |
| Zero-boilerplate discovery (module = group) | yes     | no     | no              | no            | no       |
| Separator-free chaining                     | yes     | no     | reserved-word   | reserved-word | seq task |
| Completion without re-importing             | yes     | no     | no              | no            | yes*     |
| Zero runtime dependencies                   | yes     | no     | no              | yes†          | no       |
| Output capture / replay-on-failure          | yes     | no     | yes (`ctx.run`) | partial       | no       |
| DAG / parallel-by-default                   | yes     | no     | serial          | serial        | yes      |

\* poe avoids re-importing only because its tasks aren't Python functions.

† invoke declares no dependencies either. It gets there by vendoring:
`invoke/vendor/` ships fluidity, lexicon and PyYAML inside the wheel. footman
carries no third-party code at all — the difference is where the dependency
lives, not whether you install one.

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
completion that doesn't re-import your project at all.

**Where footman is still behind:** typer's `--help` formatting is richer —
`rich`-painted panels where footman prints plain text. An optional renderer is
on the roadmap. Completion is not on that list: `fm --install-completion`
writes the hook and the rc line for bash, zsh, fish, PowerShell or nushell,
detects the shell when you don't name one, and `--uninstall-completion` takes
it back out again. The suite drives all five shells for real.

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
