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
- **shareable identity** — (declaration, frozen overrides); declared
  items only; what dedup keys on.
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
  sharing exists only where shareable identity exists; a shared item's
  subtree rides with it.
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
| hse's djlint fix | `pre_record` + I2 (override the code, verdict follows) | derives |
| `@step` three-position grammar | lifting (definition) + I1; the decorator mark mitigates build-not-run by convention, not invariant | derives (ergonomics ruled, 2026-07-31) |
| yield contract | I7 verbatim | axiom |
| `recorded=False` | I4 verbatim | axiom (spelling ruled) |
| forged receipt refused | I3 verbatim | axiom |
| cwd lane (and custom resources) | I8 + lanes definition; the in-process-only scope derives from `run()` injecting `cwd=` for subprocesses | derives |
| `parallel()` takes work items only | I12 if it lands; without the ban, coercion rules persist | derives conditionally on decision 8 |
| `p.also` retires | I12 + deferability of makers | derives conditionally on decision 8 |
| no step-grain observer | I6 (steps carry no shareable identity; address is parent-derived — out of context they are storyless) + the dispatch refusal + I5's enforced windows; liveness routes via the Status units today and record-stream export under the horizon | derives (opinion ruled 2026-07-31) |
| dry-run skips observed steps | I3 (a dry-run record is a declared-not-executed state) + title-at-entry from the `@step`/`with` shapes | derives |
| per-step duration history | address (universal, cross-run-matchable) | derives — future work, nothing blocks it |
| display policy (collapse green) | receipt = rendered record (record family); pure presentation over I5's committed records | derives — scope still open (decision 4) |
| **`recorded=False` at task grain** | Can a *row* be off the record? I1 says the property exists on the substrate, so yes-by-uniformity — but an unrecorded row that is also *shared* would be work others depend on with no record of it, and `skipped` semantics reference records. I6 and I3 collide here. | ⚠ doesn't fit yet |
| **`parallel()`'s return shape** | Today: `list[int]` of codes (and `Fanout` as codes). Under I1+I2 the natural return is the items' committed `Result`s (which ARE their codes — I2 makes the legacy shape a subset). Migration is easy; *deciding the shape* rides open decision 1's tree. | ⚠ doesn't fit yet |
| **`confirm=`/gates on steps** | Policy table says steps inherit + per-call overrides; a `confirm=` on an anonymous item mid-parallel-run has no sane prompt-ordering story. Likely answer: confirm is boundary policy (declared items only) — but that's a wall the "defaults not walls" framing must own explicitly. | ⚠ doesn't fit yet |
| **The generator pump vs lanes** | A generator step that holds a lane (I8: acquired at boundary) suspends at yields while holding it. Fine for cwd; for a future counted resource, a long-suspended holder is a starvation story. Needs a sentence in the lanes design, not a mechanism. | ⚠ doesn't fit yet |
| **`Fanout` in the model** | The `with parallel()` block is today a list of codes + queue + results. Under I1 it wants to be "a work item whose children are the queued items" — which would give it an address and a record and dissolve its special-ness. Rides decision 1. | ⚠ doesn't fit yet |

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
