# JSON output

`--json` makes one promise: **stdout is exactly one JSON document, whatever
happened.** A run, a refusal, a listing, a dry-run, a `--version` — if
`--json` is on the line, the answer is a single envelope you can hand
straight to `jq`, a CI dashboard, or an agent. Everything a task (or
anything it spawned) writes is captured into the payload, so stdout never
mixes prose with data.

This page is the whole contract. Every other page that mentions `--json`
links here.

## The items envelope

A run prints **one flat list of records, in the order the work was
created** — tasks and their steps alike, every item carrying its
`address`: the path of requests that led to it, with an ordinal once a
label repeats (`check/git`, `check/git#2`). An address prefix names a
subtree, so the tree is always recoverable — it just never makes a reader
recurse to ask a flat question. A row has `"task"`; a step has
`"command"` — that is the kind test:

```console
$ fm --json check
{
  "schema": 1,
  "total_ms": 5412.7,
  "items": [
    {
      "task": "lint",
      "address": "lint",
      "ok": true,
      "code": 0,
      "duration_ms": 812.4,
      "output": "...",
      "error": null,
      "returned": {"files": 42}
    },
    {
      "command": "ruff check src tests",
      "address": "lint/ruff",
      "code": 0,
      "duration_ms": 790.1,
      "stdout": "...",
      "stderr": "",
      "audit": [["body", "ruff check src tests", 0]],
      "failed_at": null
    }
  ]
}
```

Top-level, `total_ms` is wall-clock for the whole run — the human summary's
`took` line, as a number. Per task: `task` (dotted name), `address`, `ok`,
`code`, `duration_ms`, `output` (all captured text), `error` (`null`, or
the exception as a string) — and, when the task returns a value,
`returned`. Its steps follow it in the list — one item per
[`run()`, tool, or `step()`](tools.md) call, each with `command`,
`address`, `code`, `duration_ms`, split `stdout`/`stderr`, its `audit`
(the verdict's provenance: one `[moment, actor, code]` entry per actor
that touched it), and `failed_at`, the moment a failure came from
(`null` on success — a red tool reviewed green *is* green). Looking a
task up **by name returns a list** by contract: the same label can name
distinct pieces of work, distinct by address. `state` is the one word for what happened — `ok`, `failed`,
`cancelled`, `shared`, `skipped` — and it is an **open set**: tolerate values
you don't know. A node the run never started is a `skipped` row with
`blocked_by` naming what prevented it, seated directly after that cause — so
the envelope accounts for every node the plan had, not only the ones that
ran; a `shared` row carries none of that blame — nothing blocked it, it
was answered, and it seats at the moment it was. A row whose node waited on
prerequisites also carries `queued_ms`: how long it sat ready after its last
prerequisite finished, waiting for a worker — launch latency, never part of
`duration_ms`. A row that executed carries `thread` and `thread_id` — the
worker's stable name and its OS thread id, the correlation keys a profiler's
timeline uses; while a task runs, its worker wears the task's name
(`fm:build`, badged `[serial]`/`[exclusive]` under a lane hold), so a
sampling profiler reads as tasks rather than pool threads.

## `returned`: a task's own data

Return a value from a task and it lands in the task's entry — no decorator,
no context API, the `return` statement is the whole feature:

```python
from pathlib import Path
from footman import task

@task
def coverage() -> dict:
    "Measure coverage."
    ...
    return {"percent": 94.2, "failed": [], "report": Path("htmlcov/index.html")}
```

```console
$ fm --json coverage | jq '.items[0].returned'
{"percent": 94.2, "failed": [], "report": "htmlcov/index.html"}
```

The rules, all of them:

- `None` (the usual case) omits the key entirely.
- An `int` return keeps its long-standing meaning — the task's **exit
  code**, never data. Return `{"count": 42}` when you mean data, or declare
  `Stdout[int]` (below) when the number *is* the document. Bools are data.
- The types footman coerces *in* serialise on the way *out*: `Path` → string,
  `Enum` → its value, `datetime`/`date`/`time` → ISO format, `UUID` →
  string, `Decimal` → string (precision kept), dataclasses → dicts, sets →
  sorted lists. Dicts, lists, strings, numbers, bools pass through as
  themselves.
- Anything else is refused *loudly but locally*: the entry gets a
  `returned_error` note naming the type, stderr gets a warning, and the
  run's exit code stays the task's own — a payload problem never turns a
  green build red, and never hides in silence either.

In tests, the same value is `Runner.invoke(...).results[n].returned` — see
[Testing your tasks](testing.md). Without `--json`, return values are
simply ignored — unless the task claims stdout, below.

## The document on stdout: `Stdout[T]`

A task can declare that its return value *is* the document on stdout, in
the signature, where the rest of its contract lives:

```python
from footman import Stdout, task

@task
def status() -> Stdout[dict]:
    "Where the repo stands."
    return {"branch": "main", "dirty": False}
```

```console
$ fm status | jq .branch
"main"
```

No flag at any call site — `fm status` is a filter the way `sort` and `jq`
are filters. The return type decides the bytes, mirroring `stdin`:
`Stdout[str]` emits the string verbatim plus a trailing newline,
`Stdout[bytes]` writes raw bytes, and anything structured is JSON —
pretty-printed at a terminal, one compact line into a pipe, encoded by the
same rules as `returned` above (dataclasses to dicts, `Secret` redacted).

The rules, all of them:

- **An explicit `--json` wins.** The envelope keeps stdout and the document
  rides inside `items[].returned`, where a return value already lives.
- **Only the addressed task emits.** A declaring task reached as a `pre=`/
  `post=` dependency or a group fan-out member is suppressed, not refused —
  composing a filter into a bigger task stays legal.
- **Two declaring tasks in one chain is a refusal**, at plan time: "whose
  document?" has no answer worth guessing.
- **`None` returned means empty stdout, exit 0.** Nothing to say, said
  nothing. `Stdout[dict | None]` is the house spelling for that signature.
- **Declaring `Stdout[int]` makes the number the document** — the bare
  `-> int` exit-code channel applies only to undeclared returns, so a
  counting filter is possible.
- **A failed task emits nothing**; the exit code talks.
- **Everything that is not the document goes to stderr**: a declaring
  task's prints and `run()` lines replay there, beside the summary, so
  `fm status > out.json` captures exactly the document.
- **A body call is unaffected** — `status()` from another task returns the
  value; stdout is a boundary concern.

`Stdout[T]` and `interactive=True` cannot both hold (an interactive task
owns the real terminal, uncaptured) — that is a taught error at declaration
time, not a surprise in a pipeline.

## Refusals

A line footman refuses — a typo'd task, a misplaced flag, a broken tasks
file, a bad `--config`, Ctrl-C — emits an error envelope, with the same
taught message on stderr for humans:

```console
$ fm --json chekc
{
  "schema": 1,
  "error": {
    "code": 64,
    "message": "expected a task name, got 'chekc' — did you mean 'check'? (know: docs, lint, test, check)"
  },
  "items": []
}
```

So a wrapper needs exactly one parser: `error` is `null` or absent when
things ran; present when footman refused.

## The catalog: `fm --json --list`

The machine twin of `--list`/`--tree` (bare `fm --json` does the same): the
full task tree, every task and group with its parameters — kinds, types,
choices, bounds, env fallbacks, required-ness, and the one-line help:

```console
$ fm --json --list
{
  "schema": 1,
  "tree": {
    "help": "",
    "tasks": {
      "lint": {
        "help": "Lint with ruff.",
        "params": [{"name": "fix", "kind": "flag"}]
      }
    },
    "groups": {
      "docs": {"help": "Documentation", "tasks": {"serve": "..."}, "groups": {}}
    }
  }
}
```

Each parameter always has `name` and `kind` (`flag` | `option` | `argument`
| `variadic` | `stdin` — the last is a whole-document parameter with no
token spelling; see [Pipelines](pipelines.md)), plus whichever apply:
`required`, `choices`, `types`, `multiple`, `mapping`, `nosplit`, `path`,
`min`/`max`, `env`, `stdin` (how the value binds the pipe), `shape`,
`dynamic`, and `doc` — the author's
[per-parameter help](typing.md#validation-markers), whether from a
`doc("…")` marker or a parsed docstring. A task node carries
`help` (the docstring's first line) and, when the docstring has a body,
`long`.
This is one command's answer to "what can I run here?" — the discovery
call for agents and tooling.

## The plan: `fm --json --dry-run`

Validates a command line and prints what would run — nothing executes:

```console
$ fm --json --dry-run lint --fix test -- -x
{
  "schema": 1,
  "globals": ["--json", "--dry-run"],
  "plan": [
    {"task": "lint", "values": {"fix": true}, "variadic": [], "passthrough": null},
    {"task": "test", "values": {}, "variadic": [], "passthrough": ["-x"]}
  ]
}
```

## `--version`

```console
$ fm --json --version
{"schema": 1, "name": "footman", "version": "0.27.1"}
```

## The two exceptions

- `--help` always renders human text — its machine twin is
  `fm --json --list`. (A `--help` *refusal*, a typo'd name, still emits the
  error envelope.)
- `--where TASK` prints a bare `file:line` — already a machine format.

## Exit codes

The process exit code tells the same story as the envelope:

| code | meaning |
| ---- | ------- |
| 0 | everything ran and succeeded |
| 1 | a task raised an exception |
| N | a task returned N / its `run()` command exited N — first failure wins |
| 64 | footman refused before or while binding: parse, tasks-file, config, availability |
| 130 | interrupted (Ctrl-C) |

Exit 64 before anything runs is a feature in CI: a typo'd workflow fails in
milliseconds with a taught message, not after twenty minutes of setup.

## Recipes

A shape-check in CI — guard `.error` too, because an empty `items` list
on a refusal would pass `all(.ok)` vacuously:

```sh
fm --json check | jq -e '.error == null and ([.items[] | select(.task)] | all(.ok))'
```

Pull one task's data out of a pipeline:

```sh
fm --json coverage | jq -r '.results[] | select(.task == "coverage").returned.percent'
```

## Stability

The envelope is versioned: `schema` is `1`, bumped only if a field ever has
to change meaning. **Post-1.0, changes are additive only** — parse what you
know, ignore what you don't, and pin `schema == 1` if you're strict.
`--dry-run`'s *human* output carries no such promise; the plan envelope
does.
