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

Independent tasks run **in parallel by default**. footman builds a dependency
graph (a DAG — no cycles allowed, and a cycle is a taught error) from the
chain and each task's declared dependencies, then runs everything that isn't
waiting on something else concurrently. Tasks spend most of their life waiting
on subprocesses — a `run()` call releases Python's interpreter lock while it
waits — so threads give real wall-clock speedups without process isolation:

```sh
fm a b c            # three 1s tasks -> ~1.0s, not 3.0s
fm -s a b c         # -s/--sequential runs them one at a time -> ~3.0s
```

!!! note "Output never interleaves"

    Each task's stdout is buffered and flushed as one contiguous block when it
    finishes, so concurrent tasks never scramble each other's lines. The
    block guarantee is about stdout — the run summary and the live status
    line are stderr commentary, so redirecting stdout captures task output
    alone.

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

`parallel()` runs task functions — or no-argument lambdas, when you need to
bind arguments — concurrently, waits, and fails if any fail. Under
`-s/--sequential` it runs them one at a time instead: the flag means no
concurrency anywhere, task bodies included. `-j/--jobs N` (or `jobs = N` in
`[tool.footman]`) caps the width the same way, in both the scheduler and
task bodies; unset, footman uses one less than your core count, never
fewer than two.

```python
from footman import task, parallel

@task
def check():
    parallel(lambda: format(check=True), lint, typecheck, test)
```

Tasks run stop-on-first-failure by default; `-k/--keep-going` runs every
independent branch even if one fails.

On a TTY, every run keeps one live status line on stderr: a **progress
bar** when footman has seen this exact invocation enough to estimate
honestly — five recent green runs with a steady spread; the bar fills
against the history's 90th percentile and labels elapsed vs. typical
time — and a bouncing pulse with elapsed time when it hasn't. Both
parallel engines feed the same line, so a chain and a `parallel()` inside
a task body present identically, with running names appearing the moment
each unit starts. It always clears itself before any output lands, so
blocks and live step lines stay clean. Without a TTY, a confident
estimate prints once as `eta ~5.8s` on stderr instead — the same honesty,
one line.

Green runs teach: wall totals are stored per invocation shape and
directory beside the completion manifests (`$FOOTMAN_CACHE_DIR` moves
every footman cache at once). Three off switches: `--no-progress` for one
run, `progress = false` in `[tool.footman]` permanently, and
`@task(progress=False)` for a task whose duration has no rhyme — a run
containing one never records and only ever pulses. The line is absent
entirely under `--no-color`/`NO_COLOR`/`TERM=dumb`, `--quiet`, `--json`,
or when stderr is piped.

## JSON for CI and agents

Pass `--json` and stdout becomes exactly one JSON document: per-task results
(with captured output, structured `run()` steps, and the task's own
`returned` data), or an error envelope when footman refuses the line. The
whole contract lives on [JSON output](json.md); the CI recipes on
[CI & automation](ci.md).
