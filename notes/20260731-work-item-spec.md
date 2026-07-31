# The work-item spec (move 1: core and derivation ledger)

**Status: DRAFT — the normative core distilled from
[the thinking record](20260731-work-item-model.md), which holds the
evidence, the register, and the history. This note is the other
direction: definitions and invariants first, and every feature either
derives or gets cut. Ledger rows marked ⚠ are where incoherence is
currently hiding — they are the work.**

## Definitions

All terms per the settled register (see the thinking record). One-liners
only; the register carries nuance.

- **work item** — the one substrate: a piece of managed work with a
  record. (Notes-word; users meet only tasks and steps.)
- **task / step** — the two default bundles of the substrate. Task:
  named at import, CLI-addressable, dedups by declaration, full policy.
  Step: anonymous, unique per mention, inherits policy.
- **the ladder** — foreign (just code) → observed (a step made from body
  code) → owned (declared; schedulable). Readings: schedulability,
  placement (local-only → placeable → shippable), generator-shaped or
  not.
- **the record** — what is kept about an item: title, verdict/code,
  shown output, duration, provenance. Its rendered form is the
  *receipt*; its draft is a **`ResultView`**; committed, it is a
  **`Result`**.
- **off the record** (`recorded=False`) — executed under full
  management, no record. How a task learns something.
- **address** — a node's tree-derived name (parent-path + label +
  ordinal). Universal, referential, line-number-stable.
- **shareable identity** — (declaration, frozen overrides, bound
  arguments in normal form — defaults applied); declared items only;
  what dedup keys on. Unkeyable arguments (no frozen form) mean unique
  work — graceful degradation, never a guess. "Prereqs run defaulted"
  is the degenerate case, not a separate rule. (Verified in
  `_futures._key`, 2026-07-31: the runtime already does exactly this;
  the spec was behind the code.)
- **(resource) lane** — a serialised claim on a named resource (process
  globals; custom user resources by ruling).
- **lifting** — changing an item's grain at the use site (`@step`,
  `with step():`, `step(fn)`, `@task`…).

## Invariants

- **I1 — One substrate.** Every piece of managed work footman performs
  is a work item; task and step are default bundles, not kinds.
- **I2 — A Result is its exit code.** The committed record IS its code;
  `ResultView` is its only draft, writes phase-gated by the item's
  lifecycle.
- **I3 — No record without work.** Every record belongs to a work item
  that executed (or was dry-run-declared, or was skipped — states of
  real items). The forged receipt is unspellable, not discouraged.
- **I4 — Separable concerns, composed by default.** Execution and record
  are distinct; `run()` composes both; `recorded=False` is execution
  alone; a record without execution violates I3.
- **I5 — One record, committed once.** Review (`pre_record`, the
  generator's view, the block handle) happens before commit; after
  commit the record is immutable and reported exactly once. Review is
  per EXECUTION (a shared row: one review, observer events per
  request); a maker's hook reviews the record its maker makes, never
  its children's. After the review window closes, verdict-bearing
  fields (code, ok, title) are read-only — observers see, never judge.
- **I6 — Dedup only at declared identity.** Address is universal;
  sharing exists only where shareable identity exists (declaration +
  overrides + normal-form bound arguments; unkeyable → unique); a
  shared item's subtree rides with it.
- **I7 — Yields are never scheduling points.** Bare `yield` =
  checkpoint; every yield evaluates to the item's `ResultView`; yielding
  a value is a taught error. Concurrency stays threads and processes.
- **I8 — Lane acquisition is boundary-atomic.** All resource claims at
  the item's boundary; mid-body escalation does not exist.
- **I9 — Manifest presence only at import.** Runtime lifting can grant a
  report label, never a CLI address; the completion hot path reads the
  manifest and nothing else.
- **I10 — The record is an interface, not a blob.** Fields absent by
  circumstance (a `None` return) belong on the shared view; surfaces
  absent by kind (plugin `state`, CLI binding) stay off it.
- **I11 — Projections, not privileged shapes.** The report tree
  (who-requested-what) and the dependency DAG (what-needs-what) are both
  derivable views over one item set; committing to a storage shape is
  open decision 1, and whichever is chosen must keep the other cheap.
- **I13 — Declaration is the commitment boundary.** Shareable identity,
  boundary policy (confirm, gates, shared, forward), and the guarantee
  of a record exist exactly where a declaration does; execution policy
  (cwd, env, timeout, lanes, capture, recorded) is item-general. Every
  wall in "defaults, not walls" is an instance of this one.
- **I12 (conditional, decision 8) — Footman holds no ungraded code.**
  If the bare-callable ban lands: everything handed to footman has a
  chosen grain; the foreign rung exists only inside bodies.

## Derivation ledger

*derives* = follows from the invariants; *axiom* = is one (or a ruling);
*⚠ doesn't fit yet* = the model cannot yet say it cleanly — the open work.

| feature / claim | derivation | status |
| --- | --- | --- |
| `pre_record` (per-tool `.opts()`) | I5: review before commit; attachment-as-dispatch from I1 (a tool's items are just items) | derives |
| `@pre_record(fn)` stacked on makers | I1 (one substrate → stacks on `@step` and `@task` alike) + I5; the `requires=`→stacked-`@requires` history as precedent; `.opts()` form remains for def-less attachments; phase rule reviewed → observed → committed | derives (ruled 2026-07-31) |
| hse's djlint fix | `pre_record` + I2 (override the code, verdict follows); walk 2 walked it concrete — one line plus a reviewer, both old lies (fake duration, hidden work) die, `nofail` subsumed. Plus two derived rules: review sees what was captured; a raising reviewer fails the item. | derives (walk 2, concrete) |
| `@step` three-position grammar | lifting (definition) + I1; the decorator mark mitigates build-not-run by convention, not invariant | derives (ergonomics ruled, 2026-07-31) |
| yield contract | I7 verbatim | axiom |
| `recorded=False` | I4 verbatim | axiom (spelling ruled) |
| forged receipt refused | I3 verbatim | axiom |
| cwd lane (and custom resources) | I8 + lanes definition; the in-process-only scope derives from `run()` injecting `cwd=` for subprocesses | derives |
| `parallel()` takes work items only | I12 if it lands; without the ban, coercion rules persist | derives conditionally on decision 8 |
| `p.also` retires | I12 + deferability of makers | derives conditionally on decision 8 |
| no step-grain observer | I6 (steps carry no shareable identity; address is parent-derived — out of context they are storyless) + the dispatch refusal + I5's enforced windows; liveness routes via the Status units today and record-stream export under the horizon | derives (opinion ruled 2026-07-31) |
| dry-run semantics | Walk 3 falsified the old row ("skips observed steps" — PEP 377 makes with-bodies unskippable, and dry-run never skipped body code anyway). Correct rule derives from the ladder: dry-run fakes what footman owns the execution of (run payloads, deferred makers, generator steps); inline body code always runs, with-form records without an execution boundary. | derives (walk 3, corrected) |
| per-step duration history | address (universal, cross-run-matchable) | derives — future work, nothing blocks it |
| display policy (collapse green) | receipt = rendered record (record family); pure presentation over I5's committed records | derives — scope still open (decision 4) |
| **`recorded=False` at task grain** | Walk 4: refused — sharing IS record-reuse, and declared items are the run's accounting. Declared ⟹ recorded (I13). Taught error, not a capability. | resolved (walk 4) |
| **`parallel()`'s return shape** | Walk 1: nothing wants codes over Results; I2 makes Results backward-readable. Rides decision 1 only. | promoted (walk 1) — conditional on decision 1 |
| **`confirm=`/gates on steps** | Walk 4: refused via I13 — boundary policy (confirm, gates, shared, forward) requires a declaration to resolve at; execution policy (cwd, env, timeout, lanes, capture, recorded) is item-general. The policy table splits on this line. | resolved (walk 4) |
| **The generator pump vs lanes** | Walk 5: suspended-at-yield is exactly what close() cancels — GeneratorExit unwinds, finally releases (boundary-atomic = one release site). Between yields: uninterruptible, bounded by the longest inter-yield stretch, documented. Lanes never force-stripped. | resolved (walk 5) |
| **`Fanout` in the model** | Walk 1: both parallel() forms are an anonymous grouping item whose children are the fanned items — not special, just an anonymous parent. Shape known; rendering rides decision 1. | promoted (walk 1) — conditional on decision 1 |
| **I6 and bound arguments** | Walk 1 found the gap; source verified same day (`_futures._key`): identity already includes normal-form bound args, unkeyable → unique. I6 and the definition restated. The runtime was ahead of the spec. | resolved (walk 1) |
| **partial-of-a-task defeats interception** | Walk 1: real today (footman's own tasks.py) — silent grain demotion, possible unit double-count (verify empirically). Under I12: taught refusal. | ⚠ NEW (walk 1) — strengthens decision 8 |

## Move 2 — the payload walks

### Walk 1: footman's own `fm check` (2026-07-31)

The payload: `check` fans out `parallel(functools.partial(format,
check=True), lint, typecheck, typecomplete, covered)`; `typecheck`
itself fans `parallel(based, mypy_linux, mypy_darwin, mypy_win32,
run_ty, run_pyrefly)` — all local defs. Mixed grains in one expression,
twice over.

**Today's items, walked:** `check` is a row. `lint`/`typecheck`/
`typecomplete` are owned references → intercepted → rows (deduped
identity). `partial(format, check=True)` is a *bare callable wrapping an
owned task* — footman doesn't own `partial.__call__`, so interception is
silently defeated; it executes as foreign, and only the inner
`format(check=True)` body-call re-enters as a request. `covered` and the
four typecheck defs are foreign; their `run()` steps fold onto their
parents' step lists. The report renders every row FLAT and chronological
(`results[]`); the nesting that obviously exists (`typecheck`'s four
checkers "under" it) is expressed only by steps-riding-rows.

**Findings:**

1. **NEW ⚠ — I6 is incomplete: where do bound arguments live in
   shareable identity?** The definition says (declaration, frozen
   overrides). But `format(check=True)` and a bare `format` are
   different work; historically deps never exposed this (pre=/post=
   run defaulted, so shared nodes never differed in args), and chain
   mentions are each their own node. Body-call dedup vs arguments needs
   verifying in `_futures.call` — and whatever the answer, I6 must
   *state* it. Candidate: shareable identity = (declaration, overrides,
   bound args), with "runs defaulted" the degenerate case. VERIFY IN
   SOURCE before promoting.
2. **The partial footgun strengthens the ban (decision 8).** A partial
   of a task defeating interception is not hypothetical — footman's own
   tasks.py does it (it works only because the body-call re-intercepts;
   whether the status line double-counts that unit — parallel child AND
   inner request — needs an empirical check). Under I12 this is a
   taught refusal instead of a silent demotion. The walk found a
   today-bug-class, not just tomorrow's ergonomics.
3. **`parallel()`'s return shape: promoted toward derives.** `check`
   ignores the codes entirely (it relies on the raise); nothing in this
   payload wants `list[int]` over `list[Result]`, and I2 makes Results
   backward-readable as codes. Remaining tie to decision 1 only.
4. **`Fanout`-as-item: sharpened.** Walked, the bare-call and block
   forms produce identical children; the block adds only an addressable
   parent. Uniform answer: BOTH forms are an anonymous grouping item
   (the fan-out) whose children are the fanned items — under the tree
   projection `typecheck`'s checkers finally nest where they belong;
   under today's flat report the grouping item is invisible. So the row
   rides entirely on decision 1, but its shape is now known: not
   special, just an anonymous parent.
5. **Under the model + ban, the payload reads better than it does
   today:** every foreign def lifts (`@step` on `covered` and the four
   checker defs — each gaining a receipt, which `typecheck`'s live line
   already fakes via `__name__` assignment today: the lift replaces a
   convention with a record), and `format(check=True)` moves into the
   block form where owned calls carry arguments naturally. The
   `__name__ = "..."` idiom in tasks.py is a hand-rolled title= — more
   evidence the record surface was always being improvised.

**Promotions:** finding 3 (conditional-on-1 only), finding 4 (shape
known, rides 1). **New work:** finding 1 (I6 gap → verify + restate),
finding 2 (empirical double-count check).

### Walk 2: the djlint gate (hse's payload)

Before, five lines and two lies (`run_titled`): real work `recorded`-off
with `nofail=True`, output read, then a forged receipt lambda carrying
the title (0.0s duration, no provenance) — plus the `cwd` workaround the
forged callable dragged in. After, one line and a reviewer:

    djlint.opts(pre_record=dj_outcome).reformat(...)

Walked moment by moment: the tool call makes one step; it executes
(capture on); code finalises at 1; `pre_record` fires once for this
execution with the draft — reads `view.stdout + view.stderr`, and
either sets `view.title` and `view.code = 0` (reformatted / no files),
or sets the failure title and leaves the code. Commit follows; the
raise-on-nonzero decision reads the post-review code, so the caller
writes no `nofail` — "fail by this tool's definition of failure" comes
free. The receipt shows the REAL duration and the real command in
`.raw`; both of the old pattern's lies die, and hse deletes
`run_titled`, its twin, both lambdas, and the cwd workaround with its
caveat comments.

Invariants touched, all clean: I2 (the code override IS the verdict),
I3 (no second item exists to forge), I4 (the work is recorded again —
the report gains a step it had lost to `recorded=False` hiding), I5
(one review, one commit).

Two derived rules the walk forces into words:

- **Review sees what was captured.** The draft exposes the streams the
  run kept; an uncaptured (`capture=False`) run reviews code alone. Not
  a limitation to fix — a consequence of what a record is.
- **A raising reviewer fails the item with the hook's error** — the
  same shape as every other hook error (the enter-hook precedent).

### Walk 3: dry-run

The walk starts by falsifying a ledger row I wrote: "dry-run skips
observed steps" is wrong twice over. First, mechanically: a `with`
block's body cannot be skipped by its context manager (PEP 377 was
rejected); no CM can fake its block. Second, semantically: dry-run has
NEVER skipped body code — task bodies run under `--dry-run` today; it
is `run()` payloads that fake (including `run(callable)`, whose dry
branch returns before the callable executes).

The correct rule falls straight out of the ladder:

- **Dry-run fakes what footman owns the execution of** — subprocess
  payloads, callables handed to `run()`, deferred `@step` makers,
  generator steps (never pumped). Their records are declared-not-
  executed (I3's dry-run state), titles from the maker/entry.
- **Inline body code always runs** — lifted by a `with step():` or not.
  The with-form is the observed rung: a record-only lift with no
  execution boundary, so there is nothing for dry-run to fake; its
  inner `run()`s fake individually, exactly as the same statements
  would bare. No regression against today, and the asymmetry is not a
  wart: **dry-run capability is a property of the rung** — fakeable is
  what ownable means at dry-run time.

Consequence for the docs when they come: the with-form's sentence must
say "records the block; does not create an execution boundary" — the
one honest difference from the deferred forms, now load-bearing in two
places (dry-run and deferability).

### Walk 4: `recording()` — and the declaration principle

Payload: a test drives a task through `recording()` (built on dry-run +
quiet) whose body has recorded steps, an off-the-record read, a
reviewer, and body-called sub-tasks.

**First, a correction walk 4 forces onto walk 3.** Shipped doctrine:
off-the-record calls EXECUTE under recording and dry-run ("a value read
is not the story being recorded, and faking it would corrupt the story
that is" — the read feeds the real steps downstream). So walk 3's rule
refines: **dry-run fakes what is owned AND recorded.** Off-the-record
work executes always — it feeds the story, it isn't in it. The faked
set and the recorded-owned set are the same set; that symmetry is the
rule.

**Then the ⚠ rows fall — two at once, to one principle:**

- **`recorded=False` at task grain: refused, and now we know why.** A
  shared answer IS the record reused — sharing mechanically requires a
  record (I6 meets I3). And a declared item is a public commitment: the
  run's rows are its accounting (`--json` completeness, the exit
  summary). So: declared ⟹ recorded, definitionally. The taught error
  for `some_task.opts(recorded=False)`: a task is part of the story by
  declaration — make the read a step, or a helper function.
- **`confirm=` on steps: refused by the same principle.** Confirm (and
  the availability gates, sharing, forwarding) resolve at the REQUEST
  BOUNDARY — before work, answerable as a denied row. A mid-execution
  anonymous confirm has no boundary to resolve at. Split the policy
  table on this line: **boundary policy** (confirm, gates, shared,
  forward) requires declaration; **execution policy** (cwd, env,
  timeout, lanes, capture, recorded) is item-general.

Both walls, and walk 1's identity wall, have one root — promoted to an
invariant:

- **I13 — Declaration is the commitment boundary.** Shareable identity,
  boundary policy, and the guarantee of a record exist exactly where a
  declaration does. "Defaults, not walls" holds everywhere else; the
  walls that remain are all this one wall.

### Walk 5: Ctrl-C mid-run — lanes and the generator

Payload: full abort with children in flight, one generator step
suspended at a yield holding the cwd lane, one running between yields.

The mechanism designed for cancellation IS the answer walked: a
SUSPENDED generator is exactly what `close()` works on — fail-fast
closes it at its yield, `GeneratorExit` unwinds, `finally` releases the
lane (boundary-atomic acquisition means release is one place). A
generator RUNNING between yields is uninterruptible until its next
yield — the cooperative contract's cost, bounded by the longest
inter-yield stretch, documented rather than mechanized. Lanes are never
force-stripped from a live holder: correctness over liveness, same as
today's serial lane. Children reap per the latch exactly as shipped
(and as the 2026-07-31 flake taught us to test). The ⚠ resolves into
three sentences for the lanes design, no new mechanism.

### Walk 6: a horizon sketch (mini-ETL)

`fetch → parse → aggregate` as declared tasks with typed returns; two
consumers of `parse` in one run. Walked: I6-with-arguments already
gives run-scoped memoization (both consumers share the one `parse`
execution — the model is dataflow-shaped WITHIN a run today); placement
reads off the ladder (fetch's subprocess spec shippable; an in-process
aggregate local-only); nothing in I1–I13 blocks the horizon. What the
walk confirms as the two rendezvous, both already noted: durable
(cross-run) identity — the cache — and serializable typed returns (the
parked structured-results thread) for placement-ready hand-off. The
horizon needs those two threads, not a different model.

**Move 2 complete: every ⚠ resolved or promoted-conditional-on-1.**

## What move 2 does with this

The six payload walks (djlint gate; footman's own `check`; dry-run;
`recording()`; Ctrl-C mid-run; one horizon dataflow case) run against
these invariants specifically to attack the ⚠ rows: each walk must
either turn a ⚠ into *derives* (with the derivation written down) or
sharpen it into a named open decision. No walk, no promotion.

## Links

- Thinking record: [20260731-work-item-model.md](20260731-work-item-model.md)
- Process-globals v2 (lanes' parent): 20260725-process-globals.md
- The saga's origin: the hse receipt request, 2026-07-30 (recorded in
  the thinking record's causal chain).
