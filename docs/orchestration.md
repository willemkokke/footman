# Chaining & parallelism

footman's **execution model** in one page: how a command line becomes a plan,
what runs concurrently versus one at a time, and how you steer it. Independent
tasks run in parallel by default; `-s`, `-j`, and `-k` control the concurrency,
and a few rules decide when footman falls back to sequential.

## Chaining

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

## Parallel by default

Independent tasks run **in parallel by default** — that is the concurrency
model. footman builds a dependency graph (a DAG — no cycles allowed, and a
cycle is a taught error) from the chain and each task's declared dependencies,
then runs everything that isn't waiting on something else concurrently. Tasks
spend most of their life waiting on subprocesses — a `run()` call releases
Python's interpreter lock while it waits — so threads give real wall-clock
speedups without process isolation:

```sh
fm a b c            # three 1s tasks -> ~1.0s, not 3.0s
fm -s a b c         # -s/--sequential runs them one at a time -> ~3.0s
```

Two flags size the concurrency, and they reach **both** engines — the scheduler
and a `parallel()` inside a task body:

- `-s/--sequential` runs one task at a time — no concurrency anywhere.
- `-j/--jobs N` caps the width; unset, footman uses one less than your core
  count, never below two.

Set either permanently as `sequential` or `jobs` in `[tool.footman]`. A run
stops on the first failure; `-k/--keep-going` runs every independent branch even
if one fails, and a task whose prerequisite failed is skipped.

!!! note "Output never interleaves"

    Each task's stdout is buffered and flushed as one contiguous block when it
    finishes, so concurrent tasks never scramble each other's lines. The
    block guarantee is about stdout — the run summary and the live status
    line are stderr commentary, so redirecting stdout captures task output
    alone.

## Interactive input

A bare `input()` doesn't work in a task: its prompt goes to stdout, which
footman buffers so parallel output can't interleave — so the prompt is
swallowed and the task looks hung. There are two better shapes, and both are
CI-safe by construction.

### Ask for a value: `ask()`

Mark a typed parameter `ask()` and footman prompts for it when the command line
and its `env()` don't supply one, coercing the answer through the same pipeline
as a flag:

```python
from typing import Annotated, Literal
from footman import ask, task

@task
def release(version: Annotated[str, ask()]): ...

@task
def deploy(env: Annotated[Literal["staging", "prod"], ask()]): ...
```

`fm release --version 1.2.3` uses the flag; `fm release` asks `version:` and
runs the answer through coercion — a `Literal` is a typed choice, a bad value
re-asks. The precedence is **CLI > `env` > default > prompt**: a default *is*
the answer, so `ask()` only prompts a parameter that has none. (An `ask()`
parameter is a CLI-optional option, so it never becomes a required positional.)

The safety is the point: off a terminal, under `--no-input`, or in `--json`,
`ask()` **errors naming the flag** instead of hanging — an unattended run fails
loudly, and CI passes the value as a flag like any other.

![Animated: fm release prompts version, the typed answer runs through coercion, and the release runs](_generated/shots/ask-cast.svg)

### Gate a task: `@task(confirm=…)`

A yes/no question asked *before* the task and its prerequisites run:

```python
@task(confirm="Deploy to production?")
def deploy(): ...
```

Deny it and the task is skipped and the run exits non-zero. `--yes` auto-answers
it (for CI and scripts), and off a terminal without `--yes` the answer is no —
footman never proceeds unasked.

![Animated: fm deploy asks Deploy to production, answered yes, then deploys](_generated/shots/confirm-cast.svg)

### Own the terminal: `@task(interactive=True)`

`prompt()`, `confirm()`, and `select()` ask mid-task, but they are **guarded**:
called inside an ordinary task they raise a taught error, because the prompt
would be swallowed by the capture buffer or race a parallel sibling. A task that
genuinely runs a wizard or a REPL declares itself interactive — it then owns the
real terminal, uncaptured, with sole stdio:

```python
from footman import prompt, select, task

@task(interactive=True)
def scaffold():
    name = prompt("project name? ")
    kind = select("what kind?", ["library", "app", "plugin"])
    ...
```

`select()` picks one — or `multiple=True` picks several — from a list computed
at run time, the case a flag can't cover. Two globals cover the rest: `--yes`
auto-answers every confirm, and `--no-input` refuses to prompt (a required
prompt errors instead).

Because it owns the terminal, an interactive task can't share it with parallel
siblings: **a run that contains one goes fully sequential** — every task, one at
a time — and the live status line steps aside so its repaints can't scribble
over a prompt. (It also can't run under `--json`.)

![Animated: fm scaffold prompts for a project name, then a numbered what-kind menu picked by number](_generated/shots/interactive-cast.svg)

## Dependencies with `pre` / `post`

Declare prerequisites and follow-ups on the task; footman schedules them
(deduping shared deps, so a prerequisite pulled in twice runs once) and skips a
task whose prerequisite failed:

```python
@task(pre=[fmt, lint])      # fmt and lint run (concurrently) before check
def check(): ...

@task(post=[notify])        # notify runs after deploy succeeds
def deploy(): ...
```

This is the **declared** graph: static, so `--dry-run` and completion show it
without running anything, and deduped by identity — a cycle in it is a taught
error naming the loop. A dep is named by reference, so it runs with its
**defaults**: a task used as a prerequisite needs every parameter defaulted (a
required one errors with `missing required argument(s)`). To run a prerequisite
with specific arguments, name it in the chain — `fm build --release deploy` runs
`build --release` once, and `deploy`'s `pre=[build]` waits on that same run.

## Fan out from inside a task

`parallel()` runs task functions — or no-argument lambdas, when you need to
bind arguments — concurrently, waits, and fails if any fail. It honours the same
`-s` and `-j` as the scheduler (one worker under `-s`), so concurrency stays
controlled in one place:

```python
from footman import task, parallel

@task
def check():
    parallel(lambda: format(check=True), lint, typecheck, test)
```

Unlike `pre`/`post`, a `parallel()` fan-out is **in-body** — footman can't see
it without running the task. That is the trade: declared deps are static and
show up in `--dry-run`; an in-body fan-out is dynamic — its shape can depend on
a `run()`'s output — but opaque to the planner, which stops at the task body.
Reach for declared deps when you want the plan to *see* the work, and
`parallel()` when the fan-out has to be computed at run time.

!!! note "Passing data between tasks"

    Result data flows *within* a task — `run()` hands back its output, a called
    function its return — and out of a `parallel()` fan-out through a shared
    closure the thunks write to (they run in-process, so a captured list just
    works). `parallel()` itself returns exit *codes*, not values, and the
    declared graph carries no data between tasks: `pre`/`post` are ordering, not
    a pipe.

## Progress & the live status line

A finished run reads as a receipt — mark, name, command, time — captured
from a real terminal:

![fm format lint: green check marks, task names in cyan, dim commands, and a took line](_generated/shots/run.svg)

On a TTY, every run keeps one live status line on stderr: a **progress
bar** when footman has seen this exact invocation enough to estimate
honestly — five recent green runs with a steady spread; the bar fills
against the history's 90th percentile and labels elapsed vs. typical
time — and a bouncing pulse with elapsed time when it hasn't. Both
parallel engines feed the same line, so a chain and a `parallel()` inside
a task body present identically, with running names appearing the moment
each unit starts. It always clears itself before any output lands, so
blocks and live step lines stay clean. Without a TTY, a confident
estimate prints once as `eta ~5.8s` on stderr instead — the same honesty,
one line.

Green runs teach: wall totals are stored per invocation shape and
directory beside the completion manifests (`$FOOTMAN_CACHE_DIR` moves
every footman cache at once). Three off switches: `--no-progress` for one
run, `progress = false` in `[tool.footman]` permanently, and
`@task(progress=False)` for a task whose duration has no rhyme — a run
containing one never records and only ever pulses. The line is absent
entirely under `--no-color`/`NO_COLOR`/`TERM=dumb`, `--quiet`, `--json`,
or when stderr is piped.

## JSON for CI and agents

Pass `--json` and stdout becomes exactly one JSON document: per-task results
(with captured output, structured `run()` steps, and the task's own
`returned` data), or an error envelope when footman refuses the line. The
whole contract lives on [JSON output](json.md); the CI recipes on
[CI & automation](ci.md).
