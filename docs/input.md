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

`fm release --version=1.2.3` uses the flag; `fm release` asks `version:` and
runs the answer through coercion — a `Literal` is a typed choice, a bad value
re-asks. The precedence is **CLI > `env` > default > prompt**: a default *is*
the answer, so `ask()` only prompts a parameter that has none. (An `ask()`
parameter is a CLI-optional option, so it never becomes a required positional.)

The safety is the point: off a terminal, under `--no-input`, or in `--json`,
`ask()` **errors naming the flag** instead of hanging — an unattended run fails
loudly, and CI passes the value as a flag like any other.

![Animated: fm release prompts version, the typed answer runs through coercion, and the release runs](_generated/shots/ask-cast.svg)

## Secrets: `Secret` and `secret=True`

Two halves of the same idea — how a value is *collected*, and how it is
*shown*.

`secret=True` is the collection half: `ask(secret=True)` on a parameter, or
`prompt(secret=True)` mid-task, hides the typing (no echo) and hands back a
`Secret`.

```python
from footman import Secret, Stdout, ask, task

@task
def login(token: Annotated[str, ask(secret=True)]): ...

@task
def publish(token: Secret): ...        # a flag or env() value, still redacted
```

`Secret` is the display half, and it stands alone: annotate any parameter
with it and whatever fills it — a flag, an `env()` fallback, a default —
redacts wherever footman *shows* it. Its repr is `Secret('***')`, so
tracebacks, logs and debuggers can't leak it, and structured surfaces
serialise it as `***`: the `--json` envelope, a `Stdout[…]` document, baked
manifest defaults.

### What redaction does not cover, on purpose

The bytes a task deliberately writes. `Secret` is a real `str`, so every
string operation on it yields a plain one:

```python
@task
def env_export(token: Secret) -> Stdout[str]:
    return f"export TOKEN={token}"     # emits the real value — no switch needed
```

That is what makes footman usable as a filter for a task whose *job* is to
print a credential (`eval "$(fm env-export)"`), without a run-wide flag to
disarm protection everywhere else. The flip side is worth knowing: a secret
f-stringed into a log message loses its redaction the same way, because
neither footman nor Python can tell the two apart.

Where a `Secret` *would* survive into a structured surface and you mean it
to be emitted, say so:

```python
@task
def creds(token: Secret) -> Stdout[dict]:
    return {"token": token.reveal()}   # deliberate; a plain str from here on
```

`reveal()` exists so that intent is greppable — every deliberate exposure in
a codebase is one search away, which no global "don't redact" option could
give you.

## Gate a task: `@task(confirm=…)`

A yes/no question asked *before* the task and its prerequisites run:

<!-- example: revision -->
```python
@task(confirm="Deploy to production?")
def deploy(): ...
```

Deny it and the task never runs, the run exits non-zero, and anything that
depended on it skips, blamed on the denial. `--yes` auto-answers it (for CI
and scripts), and off a terminal without `--yes` the answer is no — footman
never proceeds unasked.

A task that asks for confirmation gets it **however it is reached**: named on
the command line or pulled in as a `pre=`/`post=` prerequisite, the question
comes up front with the run's other questions — one reference, one question,
however many ways the plan reaches it. A body call is the one reach that
cannot be known up front, so it asks at the moment of the call.

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

A piped stdin binds to typed parameters — `Stdin[str]` for the text, a
dataclass for a whole JSON document, `stdin(lines=True)` for one element
per line — with precedence **CLI > stdin > env > default > prompt**, so one
signature serves both spellings. The contract lives on
[Pipelines](pipelines.md). In tests, `Runner.invoke(..., stdin="payload")`
is the pipe; its absence means a terminal, so a test never reads the
harness's own stream.
