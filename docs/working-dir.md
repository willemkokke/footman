# Working directory & environment

footman runs independent tasks in parallel, in one process. A process has
exactly one working directory and one environment — so if tasks changed them
directly, they would trample each other. footman's answer: **in a parallel
task, the working directory and the environment are per-task data, not
process state.** Every task knows its own directory (`ctx.cwd`) and its own
environment overlay (`ctx.env`); every subprocess a task spawns gets both
applied at spawn, where they are per-child and race-free. Nothing global
moves, so nothing needs a lock — and the few tasks that genuinely need the
real thing say so with one word.

## Where a task runs

A task's directory is resolved once, before its body runs, from a ladder —
nearest wins:

1. `.opts(cwd=…, rel=…)` — this one use of the task
2. `@task(cwd=…, rel=…)` — the task's own declaration
3. `[tool.footman] cwd = "…"` — the run-wide default
4. `taskfile` — the built-in default: today's behaviour, now named

`cwd` takes a policy token or an absolute path:

| Token | The task runs in |
|-------|------------------|
| `"taskfile"` | the directory of the tasks file that defined it *(default)* |
| `"root"` | the highest tasks file's directory in the cascade |
| `"asinvoked"` | where you launched `fm`, pinned as a snapshot |
| `"unmanaged"` | footman stays out: children inherit the live process cwd |
| an absolute path | itself |

`rel=` appends a relative suffix to the resolved base — a nearer `rel`
replaces a farther one, never stacks. A relative `cwd=`, an absolute `rel=`,
and `rel=` under `unmanaged` are errors that say what to use instead.

```python
from footman import run, task

@task(cwd="root", rel="services/api")
def deploy():
    run("docker compose up -d")     # spawned from <root>/services/api
```

Inside the body, `ctx.cwd` is always a concrete `Path`, and
`footman.cwd()` hands it to you for path arithmetic:

```python
import footman

@task
def bundle():
    out = footman.cwd() / "dist"    # the task's own directory, not the
    out.mkdir(exist_ok=True)        # process's — safe under parallelism
```

Per call, `run()` and the tools bridge take the same pair:

```python
from footman import tools

run("npm run build", rel="web")             # this one call, in <cwd>/web
tools.npm.opts(rel="web").run("build")      # same, through the bridge
web_npm = tools.npm.opts(rel="web")         # or bind it once
```

The rule that makes all of this one idea: **`rel` is a suffix on whatever
base is in force at the point it appears** — the ladder's base on a task,
`ctx.cwd` at a call site.

`cwd="unmanaged"` is accepted per call too, and means what the task-level
token means: footman has no directory opinion for that one call — a child
inherits the live process cwd — while the task keeps `ctx.cwd` for
everything else.

## In-process calls and the directory

An in-process call runs inside footman's own process, which has exactly one
working directory. Nothing ever chdirs it out from under the other tasks, so
the directory a call needs decides how that call runs:

- An in-process call whose target equals the live process cwd (the common
  single-package case) runs untouched, fully parallel.
- A `tools.*` call that would run in-process but needs a *different*
  directory runs as its subprocess twin instead — same command, right
  directory, still parallel; the startup saving is the only loss.
- A bare `run(callable, cwd=…)` pointing somewhere foreign is an error
  naming the exits: use the subprocess form, build paths from
  `footman.cwd()`, declare `@task(cwd="unmanaged")`, or pass
  `cwd="unmanaged"` on the call itself if only that one call genuinely
  doesn't care.

`os.chdir` in a parallel task is an error for the same reason, and
`os.getcwd` earns a one-time note pointing at `footman.cwd()` — in a
parallel run the process cwd can be anyone's.

## The environment

Reads and writes of `os.environ` inside a task go through footman's
environment router:

- **Reads** see the environment as it was when the run started, plus the
  task's own overlay — exactly what a subprocess spawned by the same task
  would receive. In-process and subprocess tool calls read the same world.
- **Writes** scope to the task: `os.environ["API_KEY"] = "…"` is visible to
  the task's own reads and every child it spawns, and invisible to
  siblings. A one-time note names the deliberate spellings (`env=` per
  call, `ctx.env` for the task).
- **Deletes** have no scoped meaning and error with the alternatives.
- `os.putenv`/`os.unsetenv` error too — they bypass `os.environ` even in
  plain Python.

Outside a run, `os.environ` behaves exactly as stock Python.

## Raw subprocesses

Code that spawns with `subprocess` directly — yours or a library's — gets
the task's context filled in when it passes neither `cwd=` nor `env=`: the
child starts in the task's directory with the task's environment, exactly
as `run()` would spawn it, with a one-time note. Explicit arguments always
win (`env={}` stays a deliberately clean environment), and the `unmanaged`
token switches the filling off entirely. `os.fork` and `multiprocessing`
draw a note rather than help — an in-process worker inherits the *real*
environment, so the serial lane below is the honest home for them.

## When a task really needs the process to itself

Some tasks genuinely need the real globals — legacy helpers that chdir,
tools driven through APIs that only read the process state. Declare it:

```python
@task(serial=True)
def legacy_build():
    with footman.chdir(rel="vendor"):   # a real chdir — legal here
        run("make")
```

- **`serial=True`** — the task owns the process globals. At most one serial
  task runs at a time, but it *overlaps* the parallel pool: everything else
  keeps running. footman applies the task's resolved cwd with a real chdir
  and its overlay onto the real environment, restores both afterwards, and
  the guards stand down for the duration.
- **`exclusive=True`** — the full drain, for benchmarks and migrations that
  must see a quiet machine: the task runs with nothing else in flight.
- **`footman.chdir(target=None, rel=None)`** — real directory changes as a
  context manager, serial/exclusive tasks only. The default target is the
  task's own `ctx.cwd`; arguments follow the marker grammar; `ctx.cwd`
  stays in sync so a nested `run()` roots where the block does.

!!! note "Serialising a parallel tool costs less than it sounds"
    Tools that parallelise themselves — pytest with xdist, build systems —
    saturate the machine on their own. Putting one in the serial lane
    forgoes only the *other* tasks running beside it, which such a tool
    leaves no room for anyway.

Waiting is never silent: a task queued behind a serial or exclusive holder
prints a note naming the holder after a couple of seconds.

## The terminal

The terminal is a process global too — one stdin, consumed rather than
mutated. An `interactive=True` task owns it for its duration: it runs on
the real stdio while the parallel pool keeps running around it, captured;
a sibling that finishes mid-prompt has its output held until the terminal
frees, so nothing lands across your typing. A bare `input()` in a plain
parallel task is an error naming the two honest spellings: declare the
value with `ask()`, or mark the task `interactive=True`. (A *piped* stdin
is different again: a parameter marked `stdin` binds it at the boundary,
before any task runs, so the pipe's payload reaches bodies as data and the
guard never enters into it — see [Pipelines](pipelines.md).)
