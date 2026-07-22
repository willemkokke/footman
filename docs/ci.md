# CI & automation

A task runner spends most of its life in CI, so footman keeps the automation
surface small and stable: exit codes that mean something, one JSON envelope
for machines, and flags that make chains behave under supervision.

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
- `-s / --sequential` — one thing at a time, for constrained runners or when
  you're bisecting an ordering suspicion. The request reaches inside task
  bodies too: `parallel()` calls run one at a time under it, so `-s` means
  no concurrency anywhere. (A project can make this the default with
  `sequential = true` in `[tool.footman]`.)

One rule governs the streams: **stdout is the answer, stderr is the
commentary.** Task output — and footman's own answers: listings, help,
`--json` envelopes — lands on stdout; the per-task `ok`/`FAIL` summary, the
live progress line, warnings, and errors are stderr. So `fm build > out.log`
captures exactly what the tasks produced, and a wrapper that treats stderr
bytes as failure (cron's mail rule, say) should pass `-q` to silence the
summary.

Without a TTY there is no progress bar, but timing still works both ways:
CI runs are recorded into the duration history, and when footman has a
confident estimate it prints a single `eta ~5.8s` line to stderr at run
start. `--no-progress` (or `progress = false` in `[tool.footman]`) turns
the line and the recording off.

## `--json`: the machine surface

```console
$ fm --json check
{
  "schema": 1,
  "results": [
    {"task": "lint", "ok": true, "code": 0, "duration_ms": 812.4,
     "output": "...", "steps": [...], "error": null}
  ]
}
```

Everything a task (or anything it spawned) wrote is captured into the
payload, so **stdout stays pure JSON** — pipe it straight into `jq` or hand
it to an agent. Tasks can return their own structured data (`returned`),
refusals emit an error envelope instead of bare stderr text, and
`--list`/`--dry-run`/`--version` all have machine forms. The full contract —
every envelope, field by field, with recipes — lives on one page:
**[JSON output](json.md)**.

The CI shape-check:

```sh
fm --json check | jq -e '.error == null and (.results | all(.ok))'
```

## Exit codes

`0` all green · `1` a task raised · `N` a task (or its `run()` command)
exited N · `2` footman refused (a taught message says why) · `130`
interrupted. The full table is part of the machine contract:
[JSON output § exit codes](json.md#exit-codes).

Exit 2 before anything runs is a *feature* in CI: a typo'd workflow fails in
milliseconds with a taught message, not after twenty minutes of setup.

## Agents

Everything above is what coding agents want too: one command, structured
results, captured output, honest exit codes. A paste-ready instructions
snippet and edit/stop hook recipes live on [AI agents](agents.md). Three
commands to know:

- `fm --json --list` (or bare `fm --json`) prints the whole task tree as an
  envelope — every task and group with its parameters, types, choices, and
  defaults. One call, full catalog.
- `fm --json --dry-run <chain>` prints the parsed plan as an envelope — cheap
  validation of a proposed command line, nothing executed.
- `fm --help <task>` renders a task's full typed surface from the manifest,
  read-only, wherever `--help` appears on the line.

## Conditional tasks in CI

`@requires_env` re-checks live on every run, so gating a task on the
environment works naturally:

```python
@task
@requires_env("CI")
def publish_coverage(): ...
```

Locally it's listed as `(unavailable: CI only)` and refuses to run; on the
runner it just works. Remember the contract: a `pre`/`post` dependency on an
unavailable task is a **hard failure** — CI must never silently skip a step
you declared.
