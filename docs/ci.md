# CI & automation

A task runner spends most of its life in CI, so footman's automation surface
is deliberately small and stable: exit codes that mean something, one JSON
envelope for machines, and flags that make chains behave under supervision.

## The one-liner

The command you run locally is the command CI runs — that's the point of a
task runner:

```yaml
# .github/workflows/ci.yml
- run: uv run fm check
```

`check` composed with `pre=[format, lint, typecheck, test]` runs its
prerequisites **in parallel** on the runner too, and the first failure sets
the job's exit code. Output never interleaves — each task's output lands as
one block, so CI logs stay readable.

Two flags earn their keep in CI:

- `-k / --keep-going` — run every independent branch even after a failure,
  so one red step doesn't hide three others. The exit code is still the
  first failure's.
- `-s / --sequential` — one task at a time, for constrained runners or when
  you're bisecting an ordering suspicion. (A project can make this the
  default with `sequential = true` in `[tool.footman]`.)

## `--json`: the machine surface

```console
$ fm --json check
{
  "schema": 1,
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
      "error": null
    }
  ]
}
```

Everything a task (or anything it spawned) wrote is captured into the
payload, so **stdout stays pure JSON** — pipe it straight into `jq` or hand
it to an agent. The envelope is versioned and the contract is simple:

- `schema` — currently `1`; bumped only if a field ever has to change
  meaning.
- `results` — one entry per executed task, dependency order. Skipped tasks
  (a failed prerequisite) don't appear.
- Per task: `task`, `ok`, `code`, `duration_ms`, `output`, `error`
  (`null`, or the exception as a string), and `steps` — one entry per
  `run()`/`tools.*` call, each with `command`, `code`, `duration_ms`,
  `output`.
- **A task's return value lands in `returned`.** Return a dict (or list,
  string, bool, …) and it appears in the task's entry; return `None` and the
  key is absent. An `int` return stays what it always was — the exit code,
  not data. The types footman coerces *in* (`Path`, `Enum`, `datetime`,
  `UUID`, `Decimal`, dataclasses, sets) serialise on the way out; anything
  else is dropped with a `returned_error` note in the entry and a warning on
  stderr — the run's exit code is never changed by a payload.
- **Refusals keep the contract.** A line footman refuses — a typo'd task, a
  bad flag, a broken tasks file — emits one envelope too:
  `{"schema": 1, "error": {"code": 2, "message": "…"}, "results": []}`, with
  the same taught message on stderr. With `--json` on the line, stdout is
  always exactly one JSON document; the one exception is `--help`, whose
  machine twin is `fm --json --list`.
- **Post-1.0, changes are additive only.** Parse what you know, ignore what
  you don't, and pin `schema == 1` if you're strict.

A shape-check in CI is two lines of `jq` (check `.error` too — an empty
`results` list on a refusal would otherwise pass an `all(.ok)` vacuously):

```sh
fm --json check | jq -e '.error == null and (.results | all(.ok))'
```

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | all tasks succeeded |
| 1 | a task raised |
| N | a task (or its `run()` command) exited N — first failure wins |
| 2 | footman refused: parse error, tasks-file error, config error, unavailable task |
| 130 | interrupted |

Exit 2 before anything runs is a *feature* in CI: a typo'd workflow fails in
milliseconds with a taught message, not after twenty minutes of setup.

## Agents

Everything above is what coding agents want too: one command, structured
results, captured output, honest exit codes. Three extras help:

- `fm --json --list` (or bare `fm --json`) prints the whole task tree as an
  envelope — every task and group with its parameters, types, choices, and
  defaults. One call, full catalog.
- `fm --json --dry-run <chain>` prints the parsed plan as an envelope — cheap
  validation of a proposed command line, nothing executed.
- `fm --help <task>` renders a task's full typed surface from the manifest,
  read-only, wherever `--help` appears on the line.

## Conditional tasks in CI

`when=` availability re-checks live on every run, so gating a task on the
environment works naturally:

```python
@task(when="CI" in os.environ, reason="CI only")
def publish_coverage(): ...
```

Locally it's listed as `(unavailable: CI only)` and refuses to run; on the
runner it just works. Remember the contract: a `pre`/`post` dependency on an
unavailable task is a **hard failure** — CI must never silently skip a step
you declared.
