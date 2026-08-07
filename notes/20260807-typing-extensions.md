# Typing extensions — tuples, and hidden parameters

**Status: DESIGNED, not built.** Willem ruled the tuple semantics on
2026-08-07 in conversation; the hidden-parameter half is leaned but
**unruled** and marked so below. These close the last entry in the
after-1.0 backlog: *"From the typing table's post-1.0 rows: hidden
parameters, and fixed-arity `tuple[X, Y]` in comma form"*.

Related: [20260726-plugin-architecture.md](20260726-plugin-architecture.md)
(the lexical-grammar ruling this leans on — no escaping, no second pass),
[20260725-dotted-addressing.md](20260725-dotted-addressing.md) (one
spelling per concept).

## What is true today

Measured, not remembered — a scratch project against 0.33.0:

- `tuple[int, int]`, `tuple[str, int]` **and** `tuple[str, ...]` all warn
  *"annotation … is not a usable type; values are passed through as text"*.
  The body receives the raw string: `--size=800,600` → `'800,600'`.
- So **variable-arity tuples are unsupported as parameters too**, while
  json.md documents `tuple[T, ...]` as a describable *return* shape. The
  output contract accepts a shape the input contract rejects.
- `Many = list` literally (`params.py`) — `Many[T]` *is* `list[T]`, no
  runtime marker. So `tuple[str, ...]` and `Many[str]` differ only in the
  container handed to the body, never in grammar.
- A module-level type alias resolves transparently: `Ports = Many[int]`
  renders `--ports=INT ...` in help and coerces to a real list. So
  `mytype = Many[tuple[str, int]]` needs nothing special.
- **Hidden parameters do not exist** — no code, no docs, no marker. The
  `hidden` slot in the marker vocabulary is free.

Two pieces of machinery already exist and should be reused rather than
re-invented:

- **`_describe.py`'s `row` kind** — used today for `NamedTuple` returns —
  renders exactly fixed-arity heterogeneous array semantics:
  `{"type": "array", "prefixItems": …, "minItems": n, "maxItems": n}`.
  Its spec carries `fields` as *name → type*, so the names are already
  captured.
- **`nosplit`** already means *"a value may itself contain a comma; only
  the repeated flag adds items"*. Nothing new is needed for the escape.

## The rule, in one sentence

**Values accumulate from commas and repetition into one stream; a declared
fixed arity groups that stream; a remainder is a taught error.**

That covers every shape:

    tuple[X, Y]              --size=800,600          exactly one group
                             --size=800 --size=600   same stream, same group
    tuple[T, ...]            identical to Many[T]/list[T]
    Many[tuple[X, Y]]        --p=a,b --p=c,d         → [(a,b), (c,d)]
                             --p=a,b,c,d             → [(a,b), (c,d)]
                             --p=a,b,c               → error: groups of 2, got 3
    NoSplit Many[tuple[X,Y]] --p="a,b" --p=c         → [("a,b", "c")]

A bare fixed tuple is just the case where the stream must make exactly one
group. Position-wise coercion gives each slot its own type, so the same
token means different things by position: `mytype = Many[tuple[str, int]]`
with `--mytype=1,1` yields `[("1", 1)]` — a `str` in slot 0, an `int` in
slot 1. That is the one-line example for the docs; a list cannot express it.

### Why this is not guessing

Chunking looks like inference and is not. **Guessing would be inferring the
arity**; here the arity is declared by the annotation, so the grouping is
fully determined, and the remainder case is a *refusal* rather than a
fallback. Nothing is inferred, nothing is silently rounded.

## How the design got here — four corrections worth keeping

The first draft of this design was wrong in four places, each found by a
question rather than by review. Recorded because the wrong versions are the
tempting ones and will be re-proposed otherwise.

1. **"Repetition is refused on a fixed tuple; comma only."** Wrong.
   Repetition is the `nosplit` escape hatch; refusing it leaves
   comma-bearing strings inexpressible.
2. **"`nosplit` on a tuple is a contradiction → taught error."** Backwards.
   `nosplit` on a tuple is precisely the mechanism for the case that
   objection was worried about.
3. **"A tuple's parts come from exactly one source (comma by default,
   repetition under `nosplit`)."** Superseded. It was a special case
   invented to resolve a conflict that chunking dissolves; with one stream,
   comma and repetition both accumulate exactly as they do for lists, at
   every depth.
4. **"`Many[tuple[str, str]]` with comma-bearing values is inexpressible —
   two levels plus commas needs three separators, and only `dict[K, V]` has
   one to spare (`=`)."** Wrong, and the most interesting of the four:
   **the declared arity is the third separator.** With `nosplit`, repetition
   feeds one flat stream and the arity groups it —
   `--p="a,b" --p=c --p="d,e" --p=f` → `[("a,b","c"), ("d,e","f")]`. The
   hole was an artifact of correction 3, not a property of the grammar.

Each wrong version added a special case; the right one deletes all of them.
That is the usual tell.

### Rejected, and why

- **An escape grammar** (`--pair=a\,b,c`). Refused: it breaks the lexical
  rule that dash-tokens are self-contained and values are *read*, not
  parsed. That principle is what let the two-pass parser die unbuilt; an
  escaping layer for one shape is a bad trade for it.
- **Refusing `Many[tuple[...]]`** at registration. Too blunt — the shape is
  fine whenever values carry no commas, which is most of the time.
- **Silently chunking a remainder** (dropping or padding). Refused: that
  *would* be guessing. A remainder is a taught error.

## NamedTuple — my lean, UNRULED

A bare `tuple[int, int]` gives an error nothing to say but a number, and
counting errors always read mechanically:

    --size takes 2 values, comma-separated (got '800')
    --size: 2nd value expects an integer (got 'tall')

The same parameter as `class Size(NamedTuple): width: int; height: int`
gives them names, and they stop being about counting:

    --size takes width,height (got '800')
    --size: height expects an integer (got 'tall')

The names are already in the `row` spec, and `row` already renders the
right JSON Schema. So the proposal is that **`NamedTuple` is the blessed
spelling for a fixed-arity value and plain `tuple[X, Y]` is the terse
fallback** — shown first in the typing table the way `Literal` is shown
before a bare `str`, because it buys both validation and a message that
names the culprit.

This is the part that turns the feature from a coercion tweak into a new
input shape, so it is also the part that costs the most. **Willem has not
ruled on it.**

## Hidden parameters — my lean, UNRULED

No prior art in footman, so the design should be the task-level rule
transposed rather than something new. `hidden=True` on a task means: out of
`--list`/`--tree`/group help, *still* completed (composing.md: *"Hiding and
completing are different questions"*), reported in `--json` **marked rather
than missing**, and revealed by `--all`.

Transposed to a parameter, via a free marker slot:

    def deploy(target: str, legacy: Annotated[str, hidden] = ""): ...

- **out of `--help`** — the listing a human reads
- **still bindable** — `--legacy=x` works exactly as before
- **still completes** — a long machine-facing flag is the one you most want
  spelled for you; this is the task rule's own argument
- **`--json --list` marks it**, so an agent still sees it exists
- **`--all` reveals it**, as it does for hidden tasks

Use cases: a deprecated flag kept working but unadvertised, a debug switch,
a flag a wrapper script passes.

The alternative reading — *"not a CLI surface at all, bound only from
env/stdin/default"* — is a different feature and would need its own name;
`hidden` should mean what it already means one level up.

## Opens

- **`NamedTuple` in scope?** (above) — the feature's value hinges on it.
- **`hidden` semantics** (above) — the task-rule transposition, or the
  narrower "not a CLI surface" reading.
- **Does a hidden parameter still show in the generated task docs?** Hidden
  *tasks* do appear there, badged, *"because the docs are where you look up
  something the listings won't offer"*. Symmetry says yes, badged.
- **`--describe` for a fixed tuple**: reuse `row` verbatim, or a distinct
  input-side kind? Reusing it means one vocabulary for "fixed-length
  heterogeneous array" across input and output, which is the argument.
- Whether `tuple[T, ...]` should be *supported* or *taught away* in favour
  of `Many[T]` (one spelling per concept). Supporting it costs a `tuple()`
  cast at the boundary and keeps the annotation honest; teaching it away is
  more consistent. Leaning support, because refusing a shape the return
  side accepts is the asymmetry this note opened with.
