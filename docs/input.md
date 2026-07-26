# Asking for input

Most values should be flags — typed, completable, and CI-safe. But some runs
genuinely need to ask the person at the keyboard: a version string, a
production confirmation, a pick from a list computed at run time. footman has
three shapes for it, and all three are **CI-safe by construction** — off a
terminal they fail loudly or take a supplied answer, never hang. (A fourth
input is not a question at all: a document piped in on stdin — the last
section below.)

A bare `input()` doesn't work in a task: its prompt goes to stdout, which
footman buffers so parallel output can't interleave — so the prompt is
swallowed and the task looks hung. Reach for one of these instead.

## Ask for a value: `ask()`

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

## Gate a task: `@task(confirm=…)`

A yes/no question asked *before* the task and its prerequisites run:

```python
@task(confirm="Deploy to production?")
def deploy(): ...
```

Deny it and the task is skipped and the run exits non-zero. `--yes` auto-answers
it (for CI and scripts), and off a terminal without `--yes` the answer is no —
footman never proceeds unasked.

![Animated: fm deploy asks Deploy to production, answered yes, then deploys](_generated/shots/confirm-cast.svg)

## Own the terminal: `@task(interactive=True)`

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

Owning the terminal is a *lane*, not a lockdown: the interactive task runs
on the real stdio while the parallel pool keeps working around it,
captured — a sibling that finishes mid-prompt has its output held until
the terminal frees, and the live status line suspends for exactly the
ownership window, so nothing scribbles over a prompt.

![Animated: fm scaffold prompts for a project name, then a numbered what-kind menu picked by number](_generated/shots/interactive-cast.svg)

## Read the pipe: `stdin`

A parameter marked `stdin` binds from whatever the caller piped in, which
makes a task a real pipe target: `git diff | fm review` and
`fm review < changes.patch` both work, with no flag to remember. The
annotation decides how the bytes are interpreted:

```python
from typing import Annotated
from footman import Stdin, stdin, task

@task
def review(diff: Annotated[str, stdin] = ""): ...        # the stream as text
@task
def digest(data: Annotated[bytes, stdin] = b""): ...     # raw bytes
@task
def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...  # one JSON field
@task
def rm(paths: Annotated[list[str], stdin(lines=True)] = ()): ...  # a line each
```

`Stdin[str]` is the shorthand for the bare marker, like `Forward[T]` and
`NoSplit[T]`. `stdin("field")` reads one top-level key of a JSON document;
`stdin(lines=True)` turns each line into one list element, coerced exactly
like a repeated flag — `list[int]` and `list[Path]` behave as they would on
the command line.

Precedence is **CLI > stdin > env > default > prompt**: an explicit option
always wins, so one signature serves both spellings. The stream is read
once, fully, at the boundary and shared by every parameter that asks —
task bodies never touch stdin, so `stdin`-bound tasks stay fully parallel
and need no `interactive=True`. A terminal on stdin means "not provided":
nothing ever blocks on a read, a defaulted parameter falls back, and a
required one refuses with a taught message naming the fix.

In tests, `Runner.invoke(..., stdin="payload")` is the pipe; its absence
means a terminal, so a test never reads the harness's own stream.
