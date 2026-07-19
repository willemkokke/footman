# JSON output

`--json` makes one promise: **stdout is exactly one JSON document, whatever
happened.** A run, a refusal, a listing, a dry-run, a `--version` — if
`--json` is on the line, the answer is a single envelope you can hand
straight to `jq`, a CI dashboard, or an agent. Everything a task (or
anything it spawned) writes is captured into the payload, so stdout never
mixes prose with data.

This page is the whole contract. Every other page that mentions `--json`
links here.

## The results envelope

A run prints one entry per executed task, in dependency order (a task
skipped because its prerequisite failed doesn't appear):

```console
$ fm --json check
{
  "schema": 1,
  "total_ms": 5412.7,
  "results": [
    {
      "task": "lint",
      "ok": true,
      "code": 0,
      "duration_ms": 812.4,
      "output": "...",
      "steps": [
        {"command": "ruff check src tests", "code": 0, "duration_ms": 790.1, "output": "..."}
      ],
      "error": null,
      "returned": {"files": 42}
    }
  ]
}
```

Top-level, `total_ms` is wall-clock for the whole run — the human summary's
`took` line, as a number. Per task: `task` (dotted name), `ok`, `code`,
`duration_ms`, `output` (all
captured text), `error` (`null`, or the exception as a string), `steps` —
one entry per [`run()`/`tools.*`](tools.md) call, each with `command`,
`code`, `duration_ms`, `output` — and, when the task returns a value,
`returned`.

## `returned`: a task's own data

Return a value from a task and it lands in the task's entry — no decorator,
no context API, the `return` statement is the whole feature:

```python
@task
def coverage() -> dict:
    "Measure coverage."
    ...
    return {"percent": 94.2, "failed": [], "report": Path("htmlcov/index.html")}
```

```console
$ fm --json coverage | jq '.results[0].returned'
{"percent": 94.2, "failed": [], "report": "htmlcov/index.html"}
```

The rules, all of them:

- `None` (the usual case) omits the key entirely.
- An `int` return keeps its long-standing meaning — the task's **exit
  code**, never data. Return `{"count": 42}` when you mean data. Bools are
  data.
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
simply ignored.

## Refusals

A line footman refuses — a typo'd task, a misplaced flag, a broken tasks
file, a bad `--config`, Ctrl-C — emits an error envelope, with the same
taught message on stderr for humans:

```console
$ fm --json chekc
{
  "schema": 1,
  "error": {
    "code": 2,
    "message": "expected a task name, got 'chekc' — did you mean 'check'? (know: docs, lint, test, check)"
  },
  "results": []
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
| `variadic`), plus whichever apply: `required`, `choices`, `types`,
`multiple`, `mapping`, `nosplit`, `path`, `min`/`max`, `env`, `dynamic`,
and `doc` — the author's [per-parameter help](typing.md#validation-markers),
whether from a `doc("…")` marker or a parsed docstring. A task node carries
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
{"schema": 1, "name": "footman", "version": "0.12.0"}
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
| 2 | footman refused before or while binding: parse, tasks-file, config, availability |
| 130 | interrupted (Ctrl-C) |

Exit 2 before anything runs is a feature in CI: a typo'd workflow fails in
milliseconds with a taught message, not after twenty minutes of setup.

## Recipes

A shape-check in CI — guard `.error` too, because an empty `results` list
on a refusal would pass `all(.ok)` vacuously:

```sh
fm --json check | jq -e '.error == null and (.results | all(.ok))'
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
