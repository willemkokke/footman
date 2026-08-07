# Task timeout, and retry — the scheduler learns two new reasons to stop

**Status: DESIGNED, not built.** Willem ruled the retry semantics on
2026-08-07 (this note records those rulings); task timeout is assessed but
unruled beyond "do it first, it's the cheap half". Nothing is implemented.
The roadmap entry these close is *Per-task timeout, and retry* in the
after-1.0 backlog.

Related: [20260731-work-item-model.md](20260731-work-item-model.md) and
[20260731-work-item-spec.md](20260731-work-item-spec.md) (the record model
this leans on entirely), [20260725-process-globals.md](20260725-process-globals.md)
(boundary-atomic claims — why cancellation happens where it does).

## Why one note for two features

They are the same sentence from the scheduler's side: *a task can now stop
for a reason that isn't "the body finished" or "another task failed".* One
adds a deadline, the other adds an attempt counter, and both land on the
same assumption — that failure is final — in the same two places. Designing
them apart would mean visiting that assumption twice.

They are otherwise independent: timeout is cheap and touches no semantics;
retry is cheap in mechanism and expensive in meaning. **Build timeout
first.**

## Part 1 — `@task(timeout=…)`

### The machinery already exists

Nothing about cancellation needs inventing. `run(..., timeout=30)` and a
step maker's `.opts(timeout=…)` already kill the process tree at a deadline
and answer exit 124 with `Result.timed_out` set. Two facts make the task
level a re-aiming rather than a build:

- The kill path is already shared. `context.py`'s `_KILL_GRACE` is
  commented *"shared by fail-fast and by a timeout, so 'ask, then insist'
  means the same interval whichever one asked."*
- A checkpoint already consults a deadline and the abort latch side by
  side, in that order (`_step.py`, the pump loop):

      if deadline is not None and time.perf_counter() > deadline:
          timed_out = True; gen.close(); return None
      if _context._aborting.is_set() and not ctx.keep_going:
          gen.close(); return None

So a task deadline is the fail-fast event, scoped to one task: stop
starting new work, terminate that task's in-flight subprocess trees, let
generator steps unwind at their next checkpoint.

### The work

`timeout` joins `TaskOpts` (which today carries `keep_going`, `atomic`,
`interactive`, `progress`, `confirm`, `infinite`, `shared`, `cwd`, `rel`,
`lanes`, `serial`, `exclusive`); a deadline rides the task's context; the
existing checkpoint and kill paths consult it. Small — a day, most of it
tests.

### The limit, which must be stated as loudly as the feature

It cannot interrupt arbitrary Python between checkpoints. design.md
already commits to this for cancellation — *"Between checkpoints the step
cannot be interrupted, and that is not timidity but Python's own rule — a
running generator refuses to be closed from outside, by design."*

So `@task(timeout=…)` means **cancelled at the first checkpoint or
subprocess boundary past the deadline**, not a hard stop. A body running
`while True: pass` runs forever, exactly as it does under fail-fast today.
Ship the sentence with the feature or it becomes the next false promise.

### Interaction with fetch

None to build. `fetch()` is a step (*"A fetch is a step: same grid, same
`--json` entry, same `recording()`"*), so a fetch inside a task is already
subject to the task's deadline and the same cancellation path.

## Part 2 — `@task(retries=N)`

### The objection, and why it dissolves

The first draft of this design failed on honesty: three attempts happen,
so one row is a lie by omission and N rows demand an addressing scheme.
Willem's framing removes both halves:

> a task is only retried if it failed. we need a separate state as that is
> failed but not terminally. only when attempts are at 1 and it fails will
> it go to the failed state. So the task will be scheduled up to retry
> times. the json report can distinguish between retry and fail.

Every attempt is a real row with real timing, output and audit. Nothing is
merged, nothing is hidden, and *records are never fiction* holds without
special pleading — because each attempt **is** a record.

Two pieces of existing machinery carry it, which is the strongest argument
that the shape is right:

- **The state set is already open.** json.md: *"`state` is the one word for
  what happened — `ok`, `failed`, `cancelled`, `shared`, `skipped` — and it
  is an **open set**: tolerate values you don't know."* A `retried` state is
  additive and pre-sanctioned; consumers were told to expect unknown values.
  No schema break, no version bump.
- **Addresses already number repeats.** A label appearing twice takes an
  ordinal (`check/git`, `check/git#2`). Three attempts get distinct
  addresses for free.

### The rulings (Willem, 2026-08-07)

1. **A retriable failure does not trigger fail-fast** — *"it hasn't failed
   yet"*. This is accurate accounting, not an exception carved into
   fail-fast: there is no failure to react to until attempts are spent.
   Dependents wait for the same reason — nothing has failed, so nothing is
   blocked, and no `skipped`/`blocked_by` row is written.
2. **All attempts count as one unit on the progress bar.** The report stays
   honest at N rows; the bar stays stable because a retried task is still
   one piece of work the user asked for. This is the existing display/record
   split (*noise is a display problem, never a recording problem*), and it
   preserves progress.md's written promise verbatim: *"how you spell a call
   never changes the total."*
3. **Retry is the user's choice, with no theory about what deserves it.**
   *"if they put it on a task that is not recoverable it will do the same
   three times and eventually fail. that's what would be expected."* So
   `fail()` retries like anything else. The rejected alternative — footman
   deciding a deliberate `fail()` is terminal while a crash is retriable —
   would have given footman a private theory of which failures are real,
   and given `fail()` two meanings depending on a decorator argument.

### Derived rules

- **`pre=` runs once.** A retried attempt re-runs the body only;
  prerequisites already ran and are shared.
- **A share binds to the terminal attempt.** A shared task that succeeds on
  attempt 2 hands *that* record to every later requester.
- **Fail-fast still wins over a pending retry.** If a *different* task fails
  terminally, the abort latches and an unstarted attempt never starts —
  fail-fast means "no new work", and an unstarted attempt is new work.
- **Idempotence is the user's problem, and must be said.** A task that
  half-deployed then failed re-runs its body from the top. The dry-run page
  is the precedent for how plainly to say which side effects footman does
  and does not manage.

### What actually changes

Finality moves from *"a task failed"* to *"a task failed with no attempts
left"*, in exactly two places:

1. the abort trigger (fail-fast latching on first failure), and
2. skip-propagation (`blocked_by` marking dependents the moment a
   prerequisite fails).

Everything else is plumbing that exists. Those two are why this is a note
and not a PR: the failure-is-final assumption is cheap to change
deliberately and expensive to change by discovery.

### Interaction with fetch

Retry's best case, because `fetch()` revalidates rather than
re-downloads — it sends `If-None-Match` and treats `304` as *"the cached
copy stands"*. Attempt 2 of a task containing a completed fetch costs one
round trip, not the file again. Retry and content-addressed caching
compose well.

**One caution to document:** with `[fetch] backend = "curl"`, curl already
retries internally (`--retry 2`). Add `@task(retries=2)` and two
declarations multiply to as many as six attempts. Decide and state that a
task retry is *outer* to whatever a tool does on its own.

## Open, for whoever builds this

- Does a `retried` row carry its successor's address, or does the terminal
  row carry a list of its attempts? The audit's `[moment, actor, code]`
  shape suggests attempts could ride there instead of as sibling rows —
  worth one walk-through before choosing.
- Is `retries=` per task only, or does a step maker take it too? `run()`
  and steps already take `timeout=`; symmetry argues yes, the record
  question argues wait.
- Backoff. Nothing has been said about delay between attempts; a fixed
  `retries=N` with no wait is the honest minimum, and anything else wants
  its own ruling.
