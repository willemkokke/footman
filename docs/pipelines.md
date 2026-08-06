# Pipelines

footman is a first-class citizen in a pipeline: stdin binds to typed
parameters, a task can own stdout with its return value, and the exit code
distinguishes "footman did not understand you" from "the work failed". One
signature, both directions:

```python
from typing import Annotated
from footman import Stdout, stdin, task

@task
def summarise(diff: Annotated[str, stdin] = "") -> Stdout[dict]:
    "Reduce a diff to the numbers."
    added = sum(1 for line in diff.splitlines() if line.startswith("+"))
    return {"added": added}
```

```console
$ git diff | fm summarise | jq .added
12
```

## The pipe in: `stdin`

A parameter marked `stdin` binds from whatever the caller piped. The
annotation decides how the bytes are interpreted:

| annotation | reads |
| --- | --- |
| `Annotated[str, stdin]` / `Stdin[str]` | the stream as UTF-8 text |
| `Annotated[bytes, stdin]` | raw bytes |
| `Annotated[str, stdin("prompt")]` | one top-level key of a JSON document |
| `Annotated[list[int], stdin(lines=True)]` | one line per element, each line coerced like a CLI token |
| a dataclass / `dict` / `list` | the whole JSON document, typed |

Precedence is **CLI > stdin > env > default > prompt** — an explicit option
always wins, which buys the Unix `-` convention with no sentinel argument.
The stream is read once, fully, at the boundary, and shared by every
parameter in the run that asks; task bodies never touch stdin, so bound
tasks stay fully parallel and need no `interactive=True`. A terminal on
stdin means "not provided" — nothing ever blocks on a read. `ask()`,
confirm gates, and the interactive forms live on
[Asking for input](input.md).

The dataclass form is the one to reach for when a machine sends you JSON —
an agent-hook payload, a webhook body, another tool's `--json` output:

```python
from dataclasses import dataclass, field

@dataclass
class ToolInput:
    file_path: str = ""

@dataclass
class Event:
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False

@task(hidden=True)
def on_edit(event: Annotated[Event, stdin]) -> None:
    if event.tool_input.file_path.endswith(".py"):
        ...
```

Unknown keys are ignored (a producer may grow fields without breaking
you), missing keys follow the dataclass's own defaults, nested access is
plain attributes, and every refusal names the exact JSON path. A dataclass
parameter is **boundary-only** — a document is not one token, so there is
no `--event` flag; the pipe is its source, and

```console
$ fm on-edit < fixture.json
```

replays the real parse — keep a fixture next to the wiring and every hook
is testable by hand. In tests, `Runner.invoke("on-edit", stdin=payload)`
is the same replay in-process.

## The pipe out: `Stdout[T]`

A task declares that its return value is the document on stdout — in the
signature, so no call site has to remember a flag. `fm summarise` *is* a
filter, the way `sort` and `jq` are filters. The return type decides the
bytes, mirroring `stdin`: `Stdout[str]` verbatim, `Stdout[bytes]` raw,
anything structured JSON — pretty-printed at a terminal, one compact line
into a pipe. Prints and `run()` lines replay on stderr, where the summary
already lives, so redirecting stdout captures exactly the document. The
full rule set lives on [JSON output](json.md).

## Feeding a child: `run(input=…)`

The two sides above are the task at a pipeline's edge. In the middle of
one, a task also *writes* a child's standard input — some payloads have no
argv spelling at all (`uv pip install -r -` reads its requirements there):

```python
from footman import run, task

@task
def install(requirement: str) -> None:
    run("uv pip install -r -", input=requirement)
```

The string arrives whole and the pipe closes, so a child that reads to EOF
finishes rather than waits; it is encoded the way the capture is decoded
(`encoding=`). Without `input=` the child inherits the stdin the process
had — footman never opens an instantly-empty pipe on its behalf. An
in-process tool has no standard input to feed, so `input=` on one is a
taught `TypeError`.

## The exit code is the contract

A caller reads the whole boundary from two observables:

| exit | stdout | meaning |
| --- | --- | --- |
| `0` | a document | the task's result (it declared `Stdout[T]`) |
| `0` | empty | ran, nothing to say |
| `64` | empty | **footman refused** — the command line was wrong |
| other | empty | the task failed; the code is the task's own |

Refusals — an unknown task, a flag that will not coerce, a malformed
chain — exit 64 (`EX_USAGE`), never a code a task could mean on purpose,
so a typo in a wired-up command line can never impersonate a real verdict.
The low codes belong to tasks: `fail("reason", code=2)` means exactly what
it says to whoever reads 2. Interrupt is 130. (A chatty task that does not
declare `Stdout[T]` still prints to stdout — the table's stdout column is
about the tasks you address, and you wrote the address.)

## Not an in-process pipe

`fm transform validate` does **not** feed transform's document into
validate's stdin. A chain's tasks share the *process's* stdin — the same
payload, each with its own interpretation — and the document goes to the
process's stdout. Piping task into task is two invocations:

```console
$ fm transform < in.json | fm validate
```

(That is also why two `Stdout[T]` tasks in one chain refuse at plan time:
one process, one stdout, one document.)
