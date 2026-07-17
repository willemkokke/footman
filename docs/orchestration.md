# Chaining & parallelism

## Chaining

`fm format lint --fix test` runs three tasks from one line — duty's muscle
memory, but with real flags. The split is driven by the manifest, so it is
deterministic; `+` is always available as an explicit boundary, and `--dry-run`
prints the parsed plan:

```console
$ fm --dry-run format lint --fix test
  globals: --dry-run
  -> format
  -> lint  --fix
  -> test
```

## Parallel by default

Independent tasks run **in parallel by default**. footman builds a DAG from the
chain and each task's declared dependencies, then runs everything that isn't
waiting on something else concurrently. Tasks are almost always I/O-bound (they
shell out through `run()`, releasing the GIL), so threads give real wall-clock
speedups without process isolation:

```sh
fm a b c            # three 1s tasks -> ~1.0s, not 3.0s
fm -s a b c         # -s/--sequential runs them one at a time -> ~3.0s
```

!!! note "Output never interleaves"

    Each task's stdout is buffered and flushed as one contiguous block when it
    finishes, so concurrent tasks never scramble each other's lines.

## Dependencies with `pre` / `post`

Declare prerequisites and follow-ups on the task; footman schedules them
(deduping shared deps, so a prerequisite pulled in twice runs once) and skips a
task whose prerequisite failed:

```python
@task(pre=[fmt, lint])      # fmt and lint run (concurrently) before check
def check(): ...

@task(post=[notify])        # notify runs after deploy succeeds
def deploy(): ...
```

## Fan out from inside a task

`parallel()` runs task functions — or thunks, when you need arguments —
concurrently, waits, and fails if any fail:

```python
from footman import task, parallel

@task
def check():
    parallel(lambda: format(check=True), lint, typecheck, test)
```

Tasks run stop-on-first-failure by default; `-k/--keep-going` runs every
independent branch even if one fails.

On a TTY, a parallel run keeps one live status line
(`/ 2/5  running: lint, test`) between the finished tasks' output blocks.
It is event-driven, always cleared before a block lands (so output stays
non-interleaved), plain text under `--no-color`/`NO_COLOR`, and absent
entirely under `--quiet`, `--json`, or when output is piped.

## JSON for CI and agents

Pass `--json` and footman prints machine-readable results:

```console
$ fm --json test
{
  "schema": 1,
  "results": [
    {"task": "test", "ok": true, "code": 0, "duration_ms": 812.4, "output": "...", "steps": [], "error": null}
  ]
}
```

Task output — including anything a subprocess writes — is captured into the
payload, so stdout stays pure machine-readable JSON. Every `run()` inside a task
becomes a structured step (command, code, duration, captured output) in the
task's entry. The envelope is versioned (`schema`) and changes will only ever
be additive — this is the surface to build CI and agent integrations on.
