# Chaining & parallelism

footman's **execution model** in one page: how a command line becomes a plan,
what runs concurrently versus one at a time, and how you steer it. Independent
tasks run in parallel by default; `-s`, `-j`, and `-k` control the concurrency,
and a few rules decide when footman falls back to sequential.

The examples on this page share a small cast of stand-in tasks:

```python
from footman import task

@task
def fmt(): ...

@task
def lint(): ...

@task
def typecheck(): ...

@task
def test(): ...

@task
def notify(): ...
```

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

## Failing a task

A task **succeeds** unless it says otherwise. Four ways to say otherwise, most to
least deliberate:

- **`fail("reason")`** — the blessed way to stop with an explanation. The reason
  prints on the failure line and lands in the [`--json`](json.md) `error` field,
  verbatim; `fail("…", code=3)` picks the exit code too. It is a *function*, not a
  `raise`, so it stays clean under a strict linter (flake8-errmsg's `EM101`,
  tryceratops' `TRY003` flag a string literal in a `raise`) — the same reason
  `sys.exit()` and `pytest.fail()` are functions.
- **`return N`** — a bare non-zero exit code, no message. `return 0` (or falling
  off the end) is success.
- **`sys.exit("reason")` / `sys.exit(2)`** — the stdlib idiom, honoured: a string
  reason surfaces like `fail()`'s, an int is the code.
- **raise any exception** — a *crash*. Its type and message show
  (`RuntimeError: …`), signalling a bug rather than a chosen stop; the exit code
  is 1.

```python
from footman import task, fail

def open_pr() -> bool: ...   # your own lookup

@task
def release(armed: bool = False):
    if not open_pr():
        fail("no open PR for setup — run `fm create repo` first")
    ...
```

A `run()` command that exits non-zero raises `RunFailed` on your behalf (unless
`nofail=True`), so `fm` mirrors the command's own code — you rarely raise that
one yourself. To *catch* a deliberate `fail()`, `except footman.Failed:`.

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

<!-- example: revision -->
```python
from footman import Forward

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

Declare prerequisites and follow-ups on the task; footman schedules them (a
prerequisite pulled in twice runs once) and skips a task whose prerequisite
failed:

<!-- example: revision -->
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

<!-- example: revision -->
```python
from typing import Annotated
from footman import task
from footman.params import forward

@task(pre=[fmt, lint, test])
def check(fix: Annotated[bool, forward] = False):
    "fm check --fix reaches fmt & lint; test (no `fix`) runs defaulted."
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

<!-- example: revision -->
```python
from footman import task, parallel

@task
def check():
    parallel(lambda: fmt(check=True), lint, typecheck, test)
```

Four things can go in, and the difference is worth knowing:

| you pass | it runs as | reported as |
| --- | --- | --- |
| `lint` — a task | a full request | its own row, shares, hooks fire |
| `functools.partial(fmt, check=True)` | a full request | same, under the task's name |
| `lambda: fmt(check=True)` | a full request | same, but the *line* shows `…` |
| `lambda: shutil.rmtree(d)` — a thunk | plain Python, in a child context | no row: it is not a task |

A **task** reaching `parallel()` any of the first three ways is a request like
any other: its own `TaskResult`, sharing with the rest of the run, the
lifecycle hooks around it. Only the live status line can tell the spellings
apart, because a lambda is opaque from the outside — it shows as `…` where a
handle or a `functools.partial` shows the task's name. Counting is not
affected: one piece of work is one unit whichever way you wrote it.

A **plain callable** is honest work and runs happily, but it is not a task, so
nothing task-shaped happens around it — no result row, no availability gate,
no `confirm=`. Reach for one when there is genuinely no task to name.

### The block form, when you want the values

Passing arguments through thunks is fine for two or three; past that, writing
the calls plainly reads better — and it is the only form that hands the
return values back:

<!-- example: fragment -->
```python
@task
def release():
    with parallel() as p:
        build("web")
        build("api")
    web, api = p.results
    publish(web, api)
```

Inside the block a task call is **queued, not run**, so it has no value
there — using one is a taught error rather than a silent `None`. Everything
runs when the block ends, under exactly the rules a call has anywhere else:
own result row, sharing, hooks, `-s`/`-j`. `p.results` is in the order you
wrote them; `p` itself is still the list of exit codes `parallel()` returns.

footman only owns *its own* `__call__`, so a call to something that is not
a task runs where it stands rather than joining the fan-out. When one
straggler wants to come along, say so — `p.also(fn, *args)` queues it with
the rest, and its return value lands in `results` in written order:

<!-- example: fragment -->
```python
with parallel() as p:
    build("web")
    p.also(shutil.rmtree, stale)
```

The one other difference from a plain call: a queued failure surfaces when
the block ends, not at the line that queued it.

### Making a task while the run is going

`@task` is ordinary Python, so it also works *inside* a body — the decorator
runs when the body does, and what it makes is a real task: its own row,
sharing, hooks, and a place in a `parallel()` block like any other call. It
lives for the run that made it and is swept when the run ends, because the
manifest was written before any of it happened and a task no listing can
show has no business outliving its run.

That makes `task(fn)` the general way to run a plain callable *as a task*,
in a block or out of one:

<!-- example: fragment -->
```python
with parallel() as p:
    build("web")
    task(tidy_up)("stale")     # a plain function, run as a real task
```

Names are the one wrinkle. A duplicate written in a tasks file is a mistake
and stays a taught error; a duplicate made mid-run is not, so it is
numbered — `rmtree` then `rmtree-2` — which is what lets a `lambda` in a
loop, or the same helper used twice, each be their own piece of work.

Unlike `pre`/`post`, a `parallel()` fan-out is **in-body** — footman can't see
it without running the task. That is the trade: declared deps are static and
show up in `--dry-run`; an in-body fan-out is dynamic — its shape can depend on
a `run()`'s output — but opaque to the planner, which stops at the task body.
Reach for declared deps when you want the plan to *see* the work, and
`parallel()` when the fan-out has to be computed at run time.

??? note "Passing data between tasks"

    Result data flows *within* a task — `run()` hands back a `Result` (the exit
    code, which the value *is*, plus `.stdout`/`.stderr`), a called function its
    return — and out of a `parallel()` fan-out through its block form
    (`p.results`), or through a shared closure the thunks write to (they run
    in-process, so a captured list just works). `parallel(*calls)` itself
    returns exit *codes*, not values, and the declared graph carries no data
    between tasks: `pre`/`post` are ordering, not a pipe.

## Steps you make yourself

`run()` makes a step out of a command. `step()` makes one out of *your own
code* — one name, three positions:

<!-- example: fragment -->
```python
from footman import step

@step                         # 1. a function that IS a step
def clean():
    shutil.rmtree("build", ignore_errors=True)

with step("prepare") as s:    # 2. record a block, where it stands
    write_fixtures()
    s.title = "prepared 3 fixtures"

archive = step(make_archive, title="archive")   # 3. wrap someone else's
```

One honesty note, learned from Python itself: **calling a lifted function
builds its step, it doesn't run it** — `clean()` hands you a bound piece
of work ready to schedule, the same way `range(10)` hands you a range
without counting anything. Hand it to `parallel(clean(), archive("dist"))`
(a zero-argument maker is welcome bare: `parallel(clean, …)`), or call the
built item to run it right here. Either way it earns a full record — a
receipt with a real duration, captured output, a place in `--json` — and
the maker carries the step's policy: `.opts(timeout=…, capture=…,
recorded=…, title=…, pre_record=…)` per use, `@clean.pre_record` for its
reviewer and `@clean.post_step` to watch its sealed record, permanently.

A step can also be a generator, which buys two things with one keyword:

<!-- example: fragment -->
```python
@step
def convert(images: list[Path]):
    view = yield                     # the step's own record, mid-work
    for done, image in enumerate(images, start=1):
        view.title = f"converting {done}/{len(images)}"
        to_webp(image)
        yield                        # a checkpoint, once per image
```

Every bare `yield` is a **checkpoint**: the only kind of place footman
will ever cancel the step, and only for three reasons — the run is failing
fast, Ctrl-C, or the step ran past its own `timeout=`. All three arrive
the same way: the loop simply never resumes, any `try`/`finally` around
the yield runs, and the worst case costs one trip around the loop. The
value a step returns is **data, never an exit code** — a step fails by
raising (or by what its reviewer rules), and a failing item raises
`RunFailed` exactly as a failing command does. The `with step():` block is
the one form that creates no execution boundary: its statements run
exactly as they would bare — dry-run included — and only the record is
new. Deferred makers, by contrast, are footman's to execute, so `--dry-run`
fakes them like any subprocess.

## Runnable groups

A group is a namespace: `fm lint.markdown` runs a task under `lint`, but bare
`fm lint` is an error. Give the group a **default action** with `@group.default`
and the bare form runs — while the surfaces stay addressable:

<!-- example: fresh-session -->
```python
from footman import group, run, task
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
- `fm lint.markdown` / `fm lint.markdown --fix` runs one surface.
- The default's **signature is the group's whole CLI surface, positionals
  included**: `fm lint src/` hands `src/` to the default the way any task
  takes an argument. There is no ambiguity to guard against — a nested task
  always keeps its own dotted address, so a bare word after the group is
  the default's value. When a value happens to equal (or nearly equal) a
  child's name, the run carries a one-line stderr note pointing at the
  dotted spelling — `note: ran lint's default with 'markdown'; for the
  subtask: fm lint.markdown` — and a path-shaped value (`fm lint
  ./markdown`) keeps quiet.
- The default registers as the child task named **`default`** — a fixed,
  well-known name, so the decorator you wrote is the address you type:
  `@lint.default` ↔ `fm lint.default`. Bare `fm lint` stays the idiomatic
  spelling, the way `GET /` serves `/index.html` — and like the index
  document, the default has a well-known name, not whatever the author
  called the function (which stays private; renaming it is free). The
  **name is the mechanism**: `@group.default` is sugar, and any *task*
  named `default` — `@task(name="default")`, or one arriving through
  `include()` — is its group's default through the same validations. So
  naming a task `default` *means something*, exactly like `tasks.py`
  itself does. A *group* named `default` is a load-time error (a
  group-typed default would make bare `fm lint` resolve to another bare
  group — turtles).
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

### One execution per request, however it was asked for

A run performs a task's work **once per task and arguments**, and every way of
asking counts the same: a prerequisite, a chain segment, a body call. So
`fm check check` runs `check` once and reports the second mention as `shared`,
exactly as two `check()` calls in a body would, and exactly as two tasks that
both declare `pre=[check]` do. Nothing about how you reached a task changes how
often it runs.

Different arguments are different work and run — `fm build web build api` builds
twice. A different policy is a different invocation too, so
`pre=[build.opts(atomic=True)]` does not reuse a plain `build`. And a task (or
one reference to it) that declares [`shared=False`](#work-that-is-never-shared-sharedfalse)
runs for every request, which is how you say "this must happen again".

### A call is part of the run

Calling a task is not a shortcut around footman: the callee gets a real task
boundary — its own context and working directory, its `@requires` and `confirm`
gates, its own entry in the report — and the run performs its work **once per
task and arguments**, whoever asks for it. So a prerequisite you also call hands
back what it already produced, which is how a task reads a value `pre=` cannot
pass:

```python
@task
def build() -> str:
    ...
    return "dist/app.tar"

@task(pre=[build])
def publish():
    artifact = build()      # the build that already ran, not a second one
    run(f"./upload {artifact}")
```

Whether a task was reached by declaration or by a call makes no difference to
how often it runs, so you never have to hold that distinction in your head. The
same rules follow from it: different arguments are different work and run;
calling a task that is running on another thread waits for that run rather than
starting a second; and a call that could never return — a task calling itself,
or two tasks calling each other — is refused by name instead of hanging.

Two calls footman refuses outright, because a call has nowhere to put them: a
`serial=`/`exclusive=` task (its lane is taken at the task boundary, never
mid-body, which is what keeps the arbiter deadlock-free) and an `infinite` task
(a call that never returns). Declare those with `pre=` instead.

### A call binds like a segment

A parameter the caller leaves out consults the same sources an absent option
does — stdin, then its `env()` variable, then the default, with a defaultless
`ask()` prompting as the last resort — so a task behaves the same however it is
asked for:

<!-- example: revision -->
```python
from typing import Annotated
from footman import env

@task
def build(target: Annotated[str, env("DEPLOY_ENV")] = "dev") -> str: ...

@task
def release():
    build()         # $DEPLOY_ENV if set, "dev" otherwise — exactly like `fm build`
    build("dev")    # explicit, so env is never consulted
```

footman sees the call before Python fills in defaults, so leaving a parameter
out is not the same request as passing the default's value yourself: an
explicit value wins over env, exactly as a value on the command line does. And
because resolution happens before the work is keyed, a segment, a prerequisite
and a call that resolve to the same values are one piece of work.

An explicit value runs the annotation's validators — choices, bounds, path
requirements, `check(fn)` — because the annotation is the contract wherever
the value comes from. It is never *coerced*, though: a Python caller passes
real values under the signature's types, and the type checker polices those;
coercion exists because the command line only has strings. Outside a run, a
task is the plain function it looks like: a unit test that calls it gets
Python's own semantics, nothing more.

### Work that is never shared: `shared=False`

Some work exists to happen again — a notification, a timestamp, a scratch
clean. `@task(shared=False)` says exactly that: every request for the task
runs, whether the request is a call, a chain segment, or a `pre=` edge. One
rule, so the spelling you used never changes the answer.

Sharing is a property of the *request*, resolved in this order: the reference's
own `.opts(shared=…)`, then the task's declaration, then whatever asked for it,
then shared. `.opts(shared=False)` asks for one unshared run without changing
the task — on a call or on a declared edge alike:

```python
@task
def stamp(): ...

@task
def deploy():
    stamp()                       # shared: the run's one stamp
    stamp.opts(shared=False)()    # this one runs, whatever came before
```

An unshared run gets its own value but never rewrites what the run already
remembers: the first result stands, so later shared requests stay stable. A
request answered by an earlier execution is reported as `shared`, so the run
never looks like it did less than you asked.

!!! warning "Unsharing propagates down the subtree"

    An unshared request asks unshared for everything it needs — otherwise the
    promise would be a half-truth — so one `shared=False` unshares that task's
    **whole dependency subtree**. A `compile` shared by two unshared builds
    runs twice, and a deep tree multiplies. Pin anything that genuinely is
    reusable with `shared=True`, which beats an inherited answer.

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
