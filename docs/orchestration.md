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

## When a task fails: fail-fast & keep-going

A run is **fail-fast** by default: the first failure stops it. New tasks don't
start, *and* the sibling subprocesses still running are terminated — the whole
tree, each child *and its own children*, so a tool's workers (pytest-xdist,
`make -j`, a script's background jobs) die with it rather than orphaning. So a
doomed run dies at once instead of waiting out a long test suite. The kill is
SIGTERM, escalating to SIGKILL after a short grace if a tool ignores it. A task
cut off this way reports as **cancelled**, not failed, and the exit code is the
genuine failure's, never a kill signal. `Ctrl-C` reaps in-flight trees the same
way.

`--keep-going`/`-k` runs every independent branch regardless, so you see every
failure in one pass. `--fail-fast` forces the default back when a task declares
otherwise. Which wins is **three-state — command line > declared > built-in**:

```python
@task(keep_going=True)      # this gate wants to surface every problem at once
def check(): ...
```

- `fm check` keeps going — its own declaration.
- `fm --fail-fast check` overrides it for this run.
- A task that declares nothing gets the built-in fail-fast.

The policy is **scoped per subtree**, not run-wide. A keep-going gate keeps its
own prerequisites going with it, while an independent task in the same run keeps
its own policy — so `fm check deploy`, with `check` keep-going and `deploy`
fail-fast, surfaces every `check` failure *and* still bails `deploy` on the first
one. A command-line `-k`/`--fail-fast` overrides every scope at once; a task's
own (or `.opts()`-set) policy always wins over one inherited from a gate above
it, so an explicit fail-fast prerequisite stays a fail-fast boundary. The kill is
scoped too: a failure reaps the fail-fast subprocess trees still in flight but
leaves a keep-going task's child running.

Three escape hatches for the kill:

- `@task(atomic=True)` opts a task's subprocesses out — they run to completion,
  so a formatter rewriting a file can't be truncated mid-write.
- An `@task(interactive=True)` task owns the real terminal, so its subprocess
  stays attached to it and isn't group-isolated — it keeps its controlling tty
  and its own `Ctrl-C`.
- An **in-process** `run()` (a `tools.*` entry point, a plain callable) has no
  subprocess to signal, so it always finishes on its own.

### Override a task's options per use: `.opts()`

`keep_going`, `atomic`, and the rest are set on the `@task` decorator, once. When
one *use* wants a different policy, `.opts()` overrides it there — without
touching the registered task:

```python
@task(pre=[fmt.opts(atomic=True), lint])   # protect fmt's writes here, not everywhere
def check(fix: Forward[bool] = False): ...
```

`.opts()` returns the same task with the options overridden for that use only — a
`pre=`/`post=` target, or a body call — and reads everywhere a bare task does:
same name, same signature, same call. It takes the policy options `keep_going`,
`atomic`, `interactive`, `progress`, `confirm`, and `infinite`.

It takes **policy, not parameters**. A task's own arguments go in the call; the
options ride beside it — `deploy.opts(atomic=True)("prod")` — the same split
`tools.*` draw with their `.opts()`. Passing a task parameter to `.opts()` is a
taught error. A runnable group has `.opts()` too, riding its default action:
`pre=[lint.opts(keep_going=True)]` scopes keep-going to that prerequisite's
subtree (see per-subtree scoping above).

An opted reference with a *different* policy is a distinct prerequisite from a
bare one — a different policy is a different run, so both appear in the graph —
while identical policies deduplicate to one node, exactly as a shared bare
prerequisite runs once. Deduplication keys on `(task, options)`, so an empty
`.opts()` is just the bare task, and options must be hashable values.

## Interactive input

One parallelism consequence belongs here: a run that contains an
`@task(interactive=True)` task goes **fully sequential** — that task owns the
real terminal, so it can't share it with parallel siblings, and the live status
line steps aside so its repaints can't scribble over a prompt. The three ways to
ask the person at the keyboard — `ask()` for a value, `@task(confirm=…)` for a
gate, and `interactive=True` for a mid-task wizard, all CI-safe by construction —
have their own page: [Asking for input](input.md).

## Dependencies with `pre` / `post`

Declare prerequisites and follow-ups on the task; footman schedules them
(deduping shared deps, so a prerequisite pulled in twice runs once) and skips a
task whose prerequisite failed:

```python
@task(pre=[fmt, lint, typecheck, test])   # all four run before check
def check(): ...

@task(post=[notify])        # notify runs after deploy succeeds
def deploy(): ...
```

`check`'s four prerequisites have no edges *between* them, so footman runs all
four at once and only starts `check` when the last finishes:

``` mermaid
graph LR
  fmt --> check
  lint --> check
  typecheck --> check
  test --> check
```

This is the **declared** graph: static, so `--dry-run` and completion show it
without running anything, and deduped by identity — a cycle in it is a taught
error naming the loop. A dep is named by reference, so it runs with its
**defaults**: a task used as a prerequisite needs every parameter defaulted (a
required one errors with `missing required argument(s)`). To run a prerequisite
with specific arguments, name it in the chain — `fm build --release deploy` runs
`build --release` once, and `deploy`'s `pre=[build]` waits on that same run.

### Forward a value to what a task dispatches

Running defaulted is a *floor*, not a ceiling. Mark a parameter `forward` and
its value threads to every task this one dispatches — its `pre`/`post`
prerequisites and a [runnable group](#runnable-groups)'s surfaces — that declares
a parameter of the same name:

```python
from typing import Annotated
from footman import task
from footman.params import forward

@task(pre=[format, lint, test])
def check(fix: Annotated[bool, forward] = False):
    "fm check --fix reaches format & lint; test (no `fix`) runs defaulted."
```

`Forward[bool]` is the shorthand (`Forward[T]` ≡ `Annotated[T, forward]`, like
`Many[T]`). The rules:

- **Partial reach.** Only tasks that declare the parameter receive it; the rest
  run on their own defaults — `check --fix` fixes what's fixable and lints the
  rest.
- **It chains.** A callee that re-declares `forward` passes the value on, so it
  reaches a group's surfaces through the group's default.
- **Overrides a default, never rescues a required one.** A prerequisite stays
  runnable on its own; forwarding only changes a value that already has a
  default.
- **Conflicts are taught, not guessed.** Two tasks forwarding different values to
  one shared prerequisite is an error, not a silent last-wins.

Forwarding threads *values*, not graph structure, so `--dry-run` and completion
are unchanged. The explicit hand-forwarding of
[`inherited()`](cookbook.md#extend-an-inherited-task-instead-of-replacing-it)
stays for the override case — calling a task you *shadow* and changing what it
gets.

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

??? note "Passing data between tasks"

    Result data flows *within* a task — `run()` hands back a `Result` (the exit
    code, which the value *is*, plus `.stdout`/`.stderr`), a called function its
    return — and out of a `parallel()` fan-out through a shared
    closure the thunks write to (they run in-process, so a captured list just
    works). `parallel()` itself returns exit *codes*, not values, and the
    declared graph carries no data between tasks: `pre`/`post` are ordering, not
    a pipe.

## Runnable groups

A group is a namespace: `fm lint markdown` runs a task under `lint`, but bare
`fm lint` is an error. Give the group a **default action** with `@group.default`
and the bare form runs — while the surfaces stay addressable:

```python
from footman import group, run
from footman.params import Forward
from footman.tools import ruff, markdownlint, cspell

lint = group("lint")

@lint.task
def python(fix: bool = False):   ruff("check", "src", fix=fix)
@lint.task
def markdown(fix: bool = False): markdownlint("**/*.md", fix=fix)
@lint.task
def spelling():                  cspell("lint", "**/*")      # no --fix

@lint.default
def lint_all(fix: Forward[bool] = False):
    "Lint everything; --fix reaches the surfaces that support it."
```

- `fm lint` fans out every surface; `fm lint --fix` fixes what's fixable and
  lints the rest (the `forward` marker carries `--fix` to the surfaces that take
  it — see [above](#forward-a-value-to-what-a-task-dispatches)).
- `fm lint markdown` / `fm lint markdown --fix` runs one surface.
- The default's **signature is the group's options**, so it takes flags/options
  only. A positional is a load-time error, because a bare word after a group
  names a child, not a value — model a positional action as a task instead.
- An **empty body** fans out the group's own tasks; a non-empty body is the
  escape hatch where you write the fan-out yourself.
- On an empty-body default, **mark a parameter `Forward` if you want it to reach
  the surfaces.** The default has no body, so a plain parameter binds to it and
  goes nowhere — `fix: bool` accepts `--fix` and then nothing happens with it.
  `fix: Forward[bool]` threads the value to every surface that declares `fix`.
  (A parameter the default *does* use in a custom body needs no `Forward`; the
  marker is only for values that must travel onward.)
- The default takes the same **policy options** as `@task` —
  `@lint.default(pre=[...], keep_going=True, confirm="…", atomic=True)` and the
  rest — with no `name` (the group already names it). `interactive=True` needs a
  real body: an empty-body default fans out in parallel, so there is no single
  body to own the terminal, and asking for one is a load-time error.

The group tab-completes (`fm lint <Tab>` offers `--fix` and the surface names)
and `fm --help lint` renders it as a first-class command. And it composes: a
`check` gate reaches its surfaces through the group with one forwarded flag —
`@task(pre=[format, lint, test]) def check(fix: Forward[bool])`, and
`fm check --fix` threads all the way down.

A runnable group is also **callable from a task body**, the way a task is:

```python
@task
def check(fix: bool = False):
    lint(fix=fix)      # runs lint's default — fans out, or runs its body
    if fix:
        run("./stamp-version.sh")
```

`lint(fix=fix)` runs the default's action synchronously and in order — its body
as written, or, for an empty-body default, the group's own tasks, each handed
the arguments it declares. Like every body call it forwards arguments explicitly
and runs to completion before the next statement; reach for `pre=`, a chain, or
`parallel()` when you want prerequisites or concurrency. The declarative
`pre=[lint]` form above is usually cleaner — a body call is for when you need
real control flow.

## Progress & the live status line

Both parallel engines — the scheduler and a `parallel()` inside a task body —
feed one live status line, so a chain and an in-body fan-out present
identically: a real progress bar once footman has learned the run's timing, a
bouncing pulse until then. A task can also report its own progress
(`track()` / `progress()`) and the bar fills from that instead of an estimate.
The whole story — the status line, the timing history, and the off switches —
is on [Progress & timing](progress.md).

## JSON for CI and agents

Pass `--json` and stdout becomes exactly one JSON document: per-task results
(with captured output, structured `run()` steps, and the task's own
`returned` data), or an error envelope when footman refuses the line. The
whole contract lives on [JSON output](json.md); the CI recipes on
[CI & automation](ci.md).
