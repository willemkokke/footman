# Asking for input

Most values should be flags — typed, completable, and CI-safe. But some runs
genuinely need to ask the person at the keyboard: a version string, a
production confirmation, a pick from a list computed at run time. footman has
three shapes for it, and all three are **CI-safe by construction** — off a
terminal they fail loudly or take a supplied answer, never hang.

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

Because it owns the terminal, an interactive task can't share it with parallel
siblings: **a run that contains one goes fully sequential** — every task, one at
a time — and the live status line steps aside so its repaints can't scribble
over a prompt. (It also can't run under `--json`.)

![Animated: fm scaffold prompts for a project name, then a numbered what-kind menu picked by number](_generated/shots/interactive-cast.svg)
