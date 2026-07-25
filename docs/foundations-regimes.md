# The four globals, two regimes

No self-check here — this page is the recap that ties the section
together. If the previous pages held, this one should feel inevitable.

## The four globals

A process has four things its threads cannot help but share, each covered
by one page and one footman answer:

| Global | The problem | footman's answer |
|--------|-------------|------------------|
| working directory | one per process; chdir moves it for everyone | `ctx.cwd` as data; children get it at spawn; `footman.cwd()` in bodies |
| environment | one map; flows down only at spawn | reads = snapshot + task overlay; writes scope to the task |
| spawning | the child's world is fixed at spawn | `run()`/injection fill `cwd=`/`env=` per child, race-free |
| the terminal | one stdin, consumed not mutated | `ask()` at the boundary; `interactive=True` to own it |

## The two regimes

**The parallel regime** is the default: process globals are nobody's, every
task carries its own data, children are handed their world at spawn, and
the guards turn each classic mistake into a lesson — `os.chdir` errors,
environment writes scope with a note, a bare `input()` names the honest
spellings. Nothing blocks, so nothing deadlocks.

**The declared regime** is for the tasks that genuinely need the real
thing, named in one word each:

- `serial=True` — owns the process globals; at most one at a time,
  *overlapping* the parallel pool; real `chdir` legal inside.
- `exclusive=True` — owns the machine; nothing else in flight.
- `interactive=True` — owns the terminal; the pool keeps running around
  it, captured, and the status line steps aside for exactly that window.

All three are granted at task boundaries by one arbiter, which is why they
can be scheduled instead of contended for — the deadlocks page is why that
distinction earns its keep.

## The sentence to defend

**In a parallel run, the only non-parallel execution is the kind you
declared.** Everything else runs at full width; the worst degradation
anywhere else is speed, never parallelism — and every remaining wait says
its name.

From here, the [Working directory & environment](working-dir.md) guide
page shows the product surface, and the [Cookbook](cookbook.md) has the
worked recipes.
