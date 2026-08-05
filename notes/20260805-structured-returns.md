# Structured returns — the return annotation becomes the output contract

Status: DESIGN PASS, 2026-08-05 — nothing built, every decision below
marked **open** awaits Willem's call. Supersedes the 2026-07-22 working
draft (which predated `Stdout[T]`, the `items[]` envelope, and the
per-parameter annotation fallback); prioritised ahead of the tools-dist
split (2026-08-05, Willem: "more utility than a separate tools library,
though I still want that too").

## The ask, and who is asking

footman turns a typed *input* signature into a structured surface —
flags, completion, the manifest. The mirror move: turn the **return
annotation** into a structured *output* surface, so a consumer can rely
on the shape without running the task and can learn it without reading
the body.

The first named consumer is hse (affected-graph feedback, item 3,
2026-08-05): their CI scope steps consume `items[].returned`
programmatically, a key rename today is a silent break, and they pin key
names with a hand-written test. Their ask, verbatim in spirit: a task
*declares* its return shape; consumers rely on the keys; `--help`/docs
show what a task returns.

## What already works (re-verified 2026-08-05)

- A task's return value lands in the `--json` envelope at
  `items[].returned` — every node in the run, `pre=` prerequisites
  included, one flat address-carrying list (the 07-22 draft's
  `results[]` spelling is stale; the envelope reshaped since).
- Serialisation is `_describe.json_default` (context: "a task may return
  what it accepts"): `Path`→str, `Enum`→value, `datetime/date/time`→ISO,
  `UUID`→str, `Decimal`→str, dataclasses→`asdict`, `set`/`frozenset`→
  sorted list. Anything else refuses loudly-but-locally
  (`returned_error` + stderr warning, exit code untouched).
- Bare `int` is the exit-code channel, never data; `bool` is data.
- `Stdout[T]` (0.21) makes the return value the stdout *document*, and
  under an explicit `--json` the document rides `items[].returned` — one
  value, two doors, same serialisation.
- In tests the same value is `Runner.invoke(...).results[n].returned`.
- hse runs all of this in production today (`hse graph.affected` returns
  its dataclass dict; 0.30.0 integrated).

So the data path needs nothing. The work is **discovery** — and, new
since 07-22, **drift protection**.

## The model

> schema = static (annotation → manifest); data = dynamic (`--json` run).

```python
@dataclass
class Affected:
    tasks: list[str]
    reason: str
    since: str

@task
def affected() -> Affected: ...
```

The annotation *is* the declaration — no decorator, no schema language,
the same philosophy that makes `fix: bool = False` the whole input
contract. From it:

1. **`returned_schema` in the manifest**, baked beside the param specs in
   `_task_node`. Static, so completion/`--dry-run` semantics are
   untouched; the hot path parses the same one file (this repo's baked
   manifest is ~8 KB today — a schema block per returning task adds
   single-digit KB; confirm at build time, but the TAB budget does not
   move for that).
2. **`returned_schema` per entry in the `--json` envelope** — data and
   how to read it, one call, additive under `schema: 1`.
3. **A static describe surface** — the shape without a run (spelling
   open, below).
4. **Help/docs**: `--help` gains a returns line for declaring tasks;
   the task-docs renderer shows the fields.

### The describable set

Exactly `json_default`'s set, recursively: dataclasses (nested),
`list[T]`/`tuple[T, ...]`/`dict[str, T]`, the scalar bridge types
(`Path`, `Enum` — with its values as an enum schema, `datetime`, `UUID`,
`Decimal`), `str`/`int`/`float`/`bool`/`None`, `Literal[...]` as an
enum, `T | None` as nullable (mirroring param-union handling). A return
annotation *outside* the set is not an error — the task still runs, the
value still serialises or refuses at runtime exactly as today — it just
gets no schema. "Describable" ⊆ "returnable", never a new gate on
registration.

A **broken** return annotation now degrades gracefully for free: the
per-parameter fallback (0.30.0) resolves annotations individually and
warns once naming the task and the error — a schema is simply absent,
siblings unaffected.

### Drift protection — the new half

hse's actual pain is the silent rename, and the 07-22 draft's answer
("the return type is the contract, checked by the type-checker") does
not reach across a repo boundary — hse's CI is not type-checking
footman-side task bodies.

Two mechanisms, not exclusive:

- **Consumer-side snapshot.** The describe surface gives a stable JSON
  shape; a consumer checks it into their repo and diffs it in CI
  (`fm --json <describe spelling> > expected.json`). A producer rename
  becomes a visible diff at integration time, replacing hse's
  hand-written key test with one they never write again. Zero runtime
  cost, zero new semantics — this falls out of the describe surface
  existing at all.
- **Producer-side check.** footman validates the returned value against
  the declared schema at the boundary and, on mismatch, attaches a
  loud-but-local note (`returned_mismatch`, same family as
  `returned_error`) — the rename goes red in the *producer's* own gate,
  before any consumer integrates. The 07-22 draft ruled runtime
  validation out; hse's cross-repo pain is the new fact that reopens it.
  Cost: a recursive walk of the returned value per declaring task, paid
  only by tasks that declare. Never an exit-code change — a payload
  problem stays a note, per the existing rule.

Lean: snapshot-first (it is free once describe exists), producer-side
check as a fast follow if silent breaks survive in practice. **Open.**

## Where it plugs in

- `_manifest._task_node` — introspect the return annotation into a
  `returned` spec beside the param specs (bumps `tree_hash` once; caches
  rebuild, expected).
- `_describe` — type→schema generation, reusing the param-spec
  introspection; `json_default` already fixes the target set. Reuse
  `_coerce.emitted` (the `Stdout[T]` detector) so `Stdout[Report]`
  describes `Report` — the document and the returned value are one
  declaration.
- `_app` — envelope: `returned_schema` per entry read from the manifest;
  the describe spelling; the `--help` returns line.
- `_complete` — untouched: reads the same baked file, ignores the new
  key.
- Zero runtime deps throughout: stdlib `dataclasses`/`typing`
  introspection, never pydantic.

## Open calls — awaiting rulings

1. **Schema dialect.** (a) footman-native, shaped like the param specs
   (consistent: inputs and outputs described in one vocabulary, compact
   in the manifest); (b) standard JSON Schema (ecosystem validators work
   out of the box — hse could `jsonschema.validate` a payload today);
   (c) native in the manifest, JSON Schema *rendered* on request by the
   describe surface. Lean: (c) — bake once compactly, respell at the
   door that wants interop.
2. **The describe spelling and scope.** A global (`fm --describe task`),
   task-suffixed, or both; and whether a bare `fm --describe` dumps the
   whole tree's input+output contract — the "hand an agent the entire
   API" move, which is just a manifest walk. Lean: whole-tree allowed;
   it is the version hse's snapshot wants anyway.
3. **Drift protection.** Snapshot-only in v1, or producer-side
   `returned_mismatch` too (the reopened 07-22 ruling — see above).
4. **The describable-set edges.** `TypedDict`/`NamedTuple`: both
   serialise today (they are dicts/tuples at runtime) — describe them
   in v1 or start dataclass-only and widen? Plain `dict[str, Any]`
   returns: schema as `object`, or no schema? Lean: dataclass +
   scalars + containers in v1; `TypedDict` next; bare `dict` gets
   `object` and no field claims.
5. **`returned_doc`.** The 07-22 MVP (docstring `Returns:` surfaced as
   prose) is no longer the point — hse wants keys, not prose — but it
   is cheap garnish beside the schema. In v1 or dropped?
6. **`pre=` aggregation.** Carried over, with the 07-22 lean intact:
   per-entry reading is enough, the consumer keys by task/address —
   `check` composing its prerequisites' reports needs a body and buys
   little. Confirm the lean so it stops being open.
7. **Adapters stay out.** The 07-22 draft's first-party adapter library
   (pytest/ruff reports as dataclasses) and the "tools-bridge `report()`
   verb" idea both defer — hse returns its *own* dataclasses, which is
   the v1 story. The adapter question then lands naturally in the
   tools-dist thread (notes/20260801-tools-namespace-package.md), where
   `report()`-shaped verbs would live. Confirm deferral.

## Not in scope

- Runtime *coercion* of returns (they are already Python values).
- A new result channel — `items[].returned` is the channel.
- Graph effects — a schema is static node metadata.
- pydantic or any schema framework.
- Cross-run schema versioning/registry — the consumer snapshot *is* the
  version pin, pre-1.0.
