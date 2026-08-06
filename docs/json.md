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
request address (`address`): the path of requests that led to it, with an ordinal once a
label repeats (`check/git`, `check/git#2`). An address prefix names a
subtree, so the tree is always recoverable — it just never makes a reader
recurse to ask a flat question. Flat is affordable because addresses are
deterministic: parentage lives in the name, so the tree derives from the
list instead of nesting inside it. A row has `"task"`; a step has
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
      "address": "lint/ruff-check",
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
`took` line, as a number.

A **task row** carries `task` (the dotted name), its request address
(`address`), `ok`, `code`, `duration_ms`, `output` (all captured text),
`error` (`null`, or the exception as a string) — and, when the task returns
a value, `returned`. Its steps follow it in the list, one item per
[`run()`, tool, or `step()`](tools.md) call: `command`, the request
address, `code`, `duration_ms`, split `stdout`/`stderr`, the `audit` (the
verdict's provenance — one `[moment, actor, code]` entry per actor that
touched it), and `failed_at`, the moment a failure came from (`null` on
success — a red tool reviewed green *is* green). The same name can appear
twice — distinct work, distinct request addresses — so looking a task up
**by name returns a list**.

`state` is the one word for what happened — `ok`, `failed`, `cancelled`,
`shared`, `skipped` — and it is an **open set**: tolerate values you don't
know. The remaining fields appear when they have something to say:

- **`blocked_by`** — on a `skipped` row, what prevented it; the row seats
  directly after its cause, so the envelope accounts for every node the
  plan had, not only the ones that ran. A `shared` row carries no blame:
  nothing blocked it, it was answered, and it seats at the moment it was.
- **`queued_ms`** — how long a node sat ready after its last prerequisite
  finished, waiting for a worker. Launch latency, never part of
  `duration_ms`.
- **`lane_waits`** — when a lane claim actually waited:
  `[{"lane": "cspell-cache", "waited_ms": 812.4}, …]`, labelled with the
  claim's own name (a named lane, `serial`, `exclusive`, `console`). Which
  lane serialised what, and for how long, answerable from the report
  without re-running with eyes on the terminal; a claim granted on arrival
  records nothing.
- **`thread` / `thread_id`** — on a row that executed, the worker's stable
  name and OS thread id: the correlation keys a profiler's timeline uses.
  While a task runs, its worker wears the task's name (`fm:build`, badged
  `[serial]`/`[exclusive]` under a lane hold), so a sampling profiler
  reads as tasks rather than pool threads.
- **`after`** — on a row with prerequisites, the addresses it waited for:
  the plan's edges.
- **`sections`** — a task's own recorded timing (see
  [Profiling a run](profiling.md)):
  `[{"name": "resolve", "at_ms": 12.5, "duration_ms": 830.2}, …]`, each
  placed relative to the task's start. `stream` names a parallel timeline
  when the record used one, and a negative `at_ms` is legal there — a
  retroactive window may predate the task. A step's entry carries the same
  placement as `at_ms`, so a reader can rebuild the timeline the profile
  plugin draws.

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
ignored — unless the task claims stdout, below.

## The declared shape: `returned_schema`

The return *annotation* is the output contract, the same way a typed
signature is the input contract — no decorator, no schema language:

```python
from dataclasses import dataclass
from footman import task

@dataclass
class Affected:
    tasks: list[str]
    reason: str
    since: str

@task
def affected() -> Affected:
    """The tasks a change reaches.

    Returns:
        Which tasks the change reaches, and why.
    """
    ...
```

A declaring task's entry carries `returned_schema` beside `returned` —
data and how to read it, one call — in footman's own compact shape (the
same vocabulary the catalog speaks; `--describe`, below, renders standard
JSON Schema):

```console
$ fm --json affected | jq '.items[0].returned_schema'
{
  "kind": "object",
  "name": "Affected",
  "fields": {
    "tasks": {"kind": "list", "items": {"kind": "str"}},
    "reason": {"kind": "str"},
    "since": {"kind": "str"}
  }
}
```

The describable set is exactly the serialisable set above, recursively:
dataclasses (nested), `TypedDict` (`NotRequired` fields marked),
`NamedTuple` (described as a `row` — it serialises as an *array*),
`list`/`tuple[T, ...]`/`set`/`dict[str, T]`, `Path`, `Enum` and `Literal`
as choices, `datetime`/`date`/`time`, `UUID`, `Decimal`, and `T | None` as
nullable. `dict[str, Any]` describes as an object with no field claims.
An annotation *outside* the set — a wider union, an exotic generic, a
broken name — declares nothing: the task runs and serialises
exactly as it always did. "Describable" is a subset of "returnable",
never a new gate. Bare `int` stays the exit-code channel, so it declares
nothing either; `Stdout[T]` describes `T` — one declaration, two doors.

Declaring buys **drift protection** on both sides of a repo boundary:

- **Producer side.** Every reported value with a declared shape is walked
  against it at the boundary. A break — a renamed key, a wrong type, an
  undeclared extra — warns on stderr in every mode and rides the entry as
  a `returned_mismatch` note naming the first broken path
  (`"returned.tasks[1]: expected text, got int"`). Like `returned_error`,
  it is loud but local: the value still serialises and the exit code never
  moves. The rename goes red in your own gate, before any consumer
  integrates.
- **Consumer side.** Check the [`--describe`](#the-contract-without-a-run-describe)
  output into the consuming repo and diff it in CI — a contract change
  becomes a visible diff at integration time, replacing the hand-written
  key-pinning test you never write again.

The docstring's `Returns:` section (Google, NumPy, or Sphinx style) rides
beside the schema as `returned_doc` in the manifest — `--help` shows it on
a `returns:` line, and [task docs pages](taskdocs.md) render the fields.

## The contract without a run: `--describe`

`fm --describe` prints the whole tree's input+output API as one JSON
document, without running anything — every task's parameters (the same
specs the catalog bakes) and its declared return shape rendered as **JSON
Schema** (2020-12 vocabulary), with the `Returns:` prose beside it.
`fm --describe=docs.build` answers for one task, and a group address
answers for its whole subtree — the same prefix-names-a-subtree rule
addresses speak everywhere, so `fm --describe=docs` is every task under
`docs.`, nested groups included. A runnable group's default rides in that
list under its real `group.default` address, which also answers alone.
There is no wildcard syntax and none is needed: the group address *is*
the pattern, and `jq` filters the rest. Plain JSON on stdout either way,
like `--where`: the output already is the machine format.

```console
$ fm --describe=affected
{
  "schema": 1,
  "tasks": [
    {
      "task": "affected",
      "help": "The tasks a change reaches.",
      "params": [],
      "returns": {
        "schema": {
          "title": "Affected",
          "type": "object",
          "properties": {
            "tasks": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "since": {"type": "string"}
          },
          "additionalProperties": false,
          "required": ["tasks", "reason", "since"]
        },
        "doc": "Which tasks the change reaches, and why."
      }
    }
  ]
}
```

The document is built for pinning: tasks sort by address (invariant to
declaration order), hidden tasks are included and marked (`"hidden":
true` — a machine is exactly who calls them), availability is left out
(it varies per machine), and a dynamic completer's baked choices are
dropped from the params (runtime data, not contract). The rendered
schema is itself **contract, not presentation** — snapshots pin it, so a
rendering change is envelope-grade and belongs in the changelog.

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

Each parameter always has `name` and `kind` (`flag` | `option` | `positional`
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

## The rehearsal: `fm --json --dry-run`

A dry-run answers in the same items envelope a real run does, because a
dry-run *is* a run — bodies execute, and footman's own recorded work is
faked into plan-line records (empty audit, zero duration). A typo'd
chain still refuses with the error envelope and exit 64 before anything
runs:

```console
$ fm --json --dry-run release 1.2.0
{
  "schema": 1,
  "total_ms": 3.7,
  "items": [
    {"task": "release", "address": "release", "ok": true, "code": 0, ...},
    {"command": "git tag v1.2.0", "address": "release/git-tag",
     "code": 0, "duration_ms": 0.0, "audit": [], "failed_at": null, ...},
    {"command": "git push origin v1.2.0", "address": "release/git-push",
     "code": 0, "duration_ms": 0.0, "audit": [], "failed_at": null, ...}
  ]
}
```

## `--version`

```console
$ fm --json --version
{"schema": 1, "name": "footman", "version": "0.32.0"}
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
fm --json coverage | jq -r '.items[] | select(.task == "coverage").returned.percent'
```

Pin a producer's contract from a consuming repo — the snapshot is the
version pin, and a rename over there becomes a visible diff over here:

```sh
fm --describe > tests/producer-contract.json   # once, checked in
fm --describe | diff tests/producer-contract.json -   # in CI
```

## Stability

The envelope is versioned: `schema` is `1`, bumped only if a field ever has
to change meaning. **Post-1.0, changes are additive only** — parse what you
know, ignore what you don't, and pin `schema == 1` if you're strict.
`--dry-run`'s *human* output carries no such promise; its items
envelope does.
