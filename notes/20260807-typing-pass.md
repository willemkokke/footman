# One typing pass — the same types on every channel

**Status: DESIGNED, not built.** Willem ruled the command-line grammar and
the scope on 2026-08-07 in conversation; the hidden-parameter half is
leaned but **unruled** and marked so. Started as "fixed-arity tuples and
hidden parameters" — the last after-1.0 backlog row — and grew when the
same question was asked of each input channel and got three different
answers. Renamed from `20260807-typing-extensions.md`; the date is when
the thread opened.

Related: [20260726-plugin-architecture.md](20260726-plugin-architecture.md)
(the lexical-grammar ruling — no escaping, no second pass),
[20260726-process-boundary.md](20260726-process-boundary.md) (the stdin
channel), [20260725-dotted-addressing.md](20260725-dotted-addressing.md)
(one spelling per concept).

## Why this is one pass, not three features

footman has **three channels** that speak types: values from the command
line, a document from stdin, and the declared shape of a return. They are
meant to be one vocabulary. They are not, and the gaps are invisible until
they bite — one warns, one is silent, one is a hard error, for the same
annotation.

Measured against 0.33.0, not remembered:

| shape | return (`--describe`) | stdin (bind) | command line |
| --- | --- | --- | --- |
| `dataclass` | describes | **binds, nested** | hard `ValueError` |
| `NamedTuple` | describes as `row` | **silently a string** | passes text through |
| `tuple[X, Y]` | describes as `row` | warns, string | warns, string |
| `tuple[T, ...]` | describes | warns, string | warns, string |
| one-arg custom type | — | — | works (`T(value)`) |

The evidence, verbatim from a scratch project:

- `{"name":"web","at":{"x":1,"y":2},"sizes":[800,600]}` binds to
  `Plan(name='web', at=Point(x=1.0, y=2.0), sizes=[800, 600])` — the stdin
  binder already constructs **nested** custom types.
- The same `Point` from the command line: `ValueError: '1,2' is not a valid
  Point`.
- A `NamedTuple` on stdin returns `'{"width": 800, "height": 600}\n'` — a
  **string**, with **no warning**, and the run reports `ok`. Checked
  specifically for a warning; there is none.
- All three tuple forms warn *"annotation … is not a usable type; values
  are passed through as text"* on both input channels.

The `NamedTuple` case is the worst kind: the type checker concludes `Size`,
the body receives `str`, and nothing says so — the same failure class as the
basic-default asymmetry fixed in 0.33.0, but silent rather than warned.

## The shape of the fix

**The types are the contract; each channel fills them its own way.**

- **stdin needs no grammar.** JSON already carries structure, so there is
  no stream, no commas, no `nosplit` on this path — only the binder
  learning `tuple` and `NamedTuple`, which is also the silent-bug fix.
- **The command line needs a grammar**, because it starts from a flat
  string. That is the chunking rule below.
- **The return side already describes all of it** and needs nothing.

So "chunking" is a *command-line* rule, not a type-system rule. Stating it
that way keeps the pass from leaking a CLI concern into the binder.

## The command-line rule, in one sentence

**Values accumulate from commas and repetition into one stream; a declared
fixed arity groups that stream; a remainder is a taught error.**

    tuple[X, Y]              --size=800,600          exactly one group
                             --size=800 --size=600   same stream, same group
    tuple[T, ...]            identical to Many[T]/list[T]
    Many[tuple[X, Y]]        --p=a,b --p=c,d         → [(a,b), (c,d)]
                             --p=a,b,c,d             → [(a,b), (c,d)]
                             --p=a,b,c               → error: groups of 2, got 3
    NoSplit Many[tuple[X,Y]] --p="a,b" --p=c         → [("a,b", "c")]

A bare fixed tuple is the case where the stream must make exactly one
group. Position-wise coercion gives each slot its own type, so the same
token means different things by position: `mytype = Many[tuple[str, int]]`
with `--mytype=1,1` yields `[("1", 1)]`. That is the one-line docs example;
a list cannot express it.

### The arity can come from a constructor

`inspect.signature(T)` returns the same `[(x, float), (y, float)]` for a
dataclass, a `NamedTuple`, and a plain class with `__init__(self, x: float,
y: float)`. They are one case, not three:

| annotation | arity + types from |
| --- | --- |
| `tuple[float, float]` | the subscript |
| `NamedTuple` | its fields |
| `@dataclass` | its fields |
| `Point(x: float, y: float)` | its `__init__` |

So `--at=1,2` builds `Point(1.0, 2.0)`, and the errors take names from the
constructor for free: `--at takes x,y`, `--at: y expects a number (got
'tall')`. It is the house thesis one level down — the typed signature is
the contract, including a constructor's.

**Not new scope; an existing promise made true.** typing.md says *"Any type
with a typed constructor works — footman calls it"*, then implements it as
`T(value)`, silently narrowing "any type" to "any one-parameter type". A
two-parameter type is a hard error today, so the documented claim is
already false and nothing says so.

**Non-breaking, because arity decides.** A one-parameter constructor keeps
today's exact behaviour — the whole token, uncoerced — which is the
documented `Version("1.2.3")` case. Only multi-parameter constructors
change, and those are currently a dead end.

### Why chunking is not guessing

**Guessing would be inferring the arity.** Here the arity is declared by
the annotation, so the grouping is fully determined and a remainder is a
*refusal*, never a fallback. Nothing is inferred, nothing is rounded.

## Constraints the pass must not break

Verified, so the binder work does not quietly regress them:

- **stdin is shared, not exclusive.** The stream is read once, fully, at the
  boundary, and every parameter that asks is served from the same parsed
  document. Two parameters reading different keys both work
  (`name='web' port=8080`); two parameters both claiming the whole document
  each get it; a whole-document parameter and a field parameter coexist.
  No exhaustion, no ordering dependency, no first-come-first-served.
- **CLI beats stdin.** `--port=9999` over a piped `"port":"8080"` yields
  `9999`, per **CLI > stdin > env > default > prompt**. That is what lets
  one signature serve both spellings — pipe in CI, override one field by
  hand — with no sentinel argument and no second code path.

## Hidden parameters — LEANED, UNRULED

No prior art: no code, no docs, and the `hidden` marker slot is free. The
design should transpose the task-level rule rather than invent one.
`hidden=True` on a task means: out of the listings, *still* completed
(*"Hiding and completing are different questions"*), reported in `--json`
**marked rather than missing**, and revealed by `--all`.

    def deploy(target: str, legacy: Annotated[str, hidden] = ""): ...

- out of `--help` — the listing a human reads
- still bindable; still completes — a long machine-facing flag is the one
  you most want spelled for you, which is the task rule's own argument
- `--json --list` marks it, so an agent still sees it exists
- `--all` reveals it

Uses: a deprecated flag kept working but unadvertised, a debug switch, a
flag a wrapper script passes. The alternative reading — *"not a CLI surface
at all, bound only from env/stdin/default"* — is a different feature and
wants a different name; `hidden` should mean what it already means one
level up.

## How the design got here — four corrections worth keeping

Each was found by a question, not by review. Recorded because the wrong
versions are the tempting ones and will be re-proposed otherwise.

1. **"Repetition is refused on a fixed tuple; comma only."** Wrong —
   repetition is the `nosplit` escape hatch, and refusing it leaves
   comma-bearing strings inexpressible.
2. **"`nosplit` on a tuple is a contradiction → taught error."** Backwards:
   it is precisely the mechanism for the case that objection worried about.
3. **"A tuple's parts come from exactly one source."** A special case
   invented to resolve a conflict that chunking dissolves. With one stream,
   comma and repetition both accumulate as they do for lists, at every
   depth.
4. **"`Many[tuple[str, str]]` with comma-bearing values is inexpressible —
   two levels plus commas needs three separators."** Wrong, and the most
   interesting: **the declared arity is the third separator.** With
   `nosplit`, repetition feeds one flat stream and the arity groups it —
   `--p="a,b" --p=c --p="d,e" --p=f` → `[("a,b","c"), ("d,e","f")]`. The
   hole was an artifact of correction 3.

Each wrong version added a special case; the right one deletes all of them.
That is the usual tell.

### Rejected, and why

- **An escape grammar** (`--pair=a\,b,c`). Breaks the lexical rule that
  dash-tokens are self-contained and values are *read*, not parsed — the
  principle that let the two-pass parser die unbuilt.
- **Refusing `Many[tuple[...]]`** at registration. Too blunt; the shape is
  fine whenever values carry no commas, which is most of the time.
- **Silently chunking a remainder.** That *would* be guessing.
- **Fixing the silent `NamedTuple` binding as its own PR.** Ruled against:
  one pass, one consistent standard, so the channels move together.

## Opens

- **`NamedTuple` as the blessed named form?** Its errors name a position
  (`--size: height expects an integer`) where a plain tuple can only count
  (`2nd value`). Lean: show it first in the typing table, the way `Literal`
  precedes a bare `str`.
- **`hidden` semantics** — the task-rule transposition above, or the
  narrower "not a CLI surface" reading.
- **Hidden parameters in the generated task docs?** Hidden *tasks* appear
  there, badged, *"because the docs are where you look up something the
  listings won't offer"*. Symmetry says yes, badged.
- **Optional constructor parameters** (`Point(x, y, z=0)`) — a group cannot
  be "2 or 3" without guessing. Lean: the group is the *required* count and
  optionals are not fillable from the command line.
- **Non-scalar positions** (`Line(a: Point, b: Point)`) — stop at one level;
  recursion needs a nesting rule, which is the ambiguity this design
  avoids. `*args` constructors do not group either.
- **`--describe` for a fixed tuple**: reuse `row` verbatim, or a distinct
  input-side kind? Reuse means one vocabulary for "fixed-length
  heterogeneous array" across input and output.
- Whether `tuple[T, ...]` is *supported* or *taught away* in favour of
  `Many[T]`. Leaning support: refusing a shape the return side accepts is
  the asymmetry this note opened with.
