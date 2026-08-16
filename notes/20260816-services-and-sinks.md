# Services and sinks: two node kinds hiding behind two flags

Status: EXPLORATORY — 2026-08-16, opened by Willem while the signal fixes
(#455–#458) were still landing. Everything below was measured against
`origin/main` at `913a59b`, in throwaway worktrees, with the reproductions
kept in the appendix. **Nothing here is built.** Five questions at the bottom
are Willem's to call, and two of them gate the rest.

## The idea (Willem)

> I'm a little worried about infinite tasks and tasks with variadic arguments
> being mixed. Should an infinite task combined with other tasks work? Does it
> permanently take up a job slot? […] Should variadic tasks always be at the
> end? […] Either way let's experiment and see if we can get to an exhaustive
> coherent behaviour.

and then, once the exhaustive behaviour turned out to be a pile of exceptions:

> If we didn't make new rules to fit the current situation, but can change
> things […] would we be able to improve on this design?

## The thesis

`infinite=True` and `*args` are both **flags standing in for a node kind that
does not exist**. Everything catalogued below is some subsystem discovering it
needs a distinction the core does not make, and inventing a local one.

The tell is that the *peripheral* subsystems already model it correctly and the
core does not:

- completion knows a variadic segment is terminal — `fm test <TAB>` offers
  nothing, while `fm lint <TAB>` offers every sibling task
- `_split.py:16` says so in prose: *"variadic / `--` passthrough segments are
  terminal"*
- `_futures.py:654` knows an infinite task can never be awaited
- `_describe.py:422` knows it "runs until Ctrl-C"

Only `_split.py`'s consumer loop and `_schedule.py`'s node loop treat them as
ordinary. That is where every finding lives.

---

## Part 1 — services

### What was measured

The trigger was the README chain the launch audit found
(`launch/audit-2026-08-16.md`, "Fixed already — text"). Chasing it turned up
the following, all on `913a59b`.

**An infinite task permanently holds a job slot.** With a bounded stand-in
(`infinite=True`, returns after 3s) so the run completes and receipts print:

| run | wall |
|---|---|
| `-j=2 slow1 + slow2 + slow3` (three 1s tasks) | 2.0s |
| `-j=2 held + slow1 + slow2 + slow3` | 3.0s — the three ran **one at a time** |
| `-j=2 slow1 + slow2 + slow3 + held` | 4.0s — position decides when it starts |
| `-j=1 held + slow1 + slow2` | 5.0s — with a real server, slow1/slow2 never run |

With a real service that is for the life of the process: the default `--jobs`
permanently loses a worker, and `-j=1` means nothing after it ever runs.

**Stopping a service is filed as a failure, and takes the run's receipts with
it.**

```console
$ fm lint + stays          # `stays` is run(["sleep", "600"]), Ctrl-C at 2.3s
lint ran
FAIL stays  sleep 600  (2.3s)
fm: interrupted                    # <- no `ok lint` row. lint's receipt is gone.
```

footman prints *"stays runs until you stop it — Ctrl-C"*, you do the one thing
it told you to, and it reports `FAIL` — and throws away the receipt for the
work that genuinely succeeded. `docs.serve` chained with anything means never
learning whether the anything passed.

It is also inconsistent by node count: `fm stays` alone prints **no receipt at
all**, `fm lint + stays` prints `FAIL`.

**A crash and a stop are not distinguishable at the node.** Both print `FAIL
<task>`; only the run-level exit code (1 vs 130) and the presence of a
`RunFailed:` line separate them. A server that died on "address already in use"
and a server you ran for an hour and then stopped produce the same node
receipt.

**`pre=[infinite]` hangs forever, and footman recommends it by name.**
`_futures.py:654` refuses a body call with *"Run it as its own segment, or
declare it as `pre=[watch]`."* Measured: `@task(pre=[watch])` never returns
(killed at 8s, exit 137). Nothing refuses it at declaration or plan time.

**One infinite node costs the whole run its status line.**
`_schedule.py:745` computes `endless` over every node and passes
`progress and not endless`, so five finite tasks lose progress because one
sibling is a server.

**A pure-Python infinite body plus any sibling is unkillable.** This one is
narrow but it is the diagnosis that unlocked the design. Using the SIGQUIT
stack dump from #457:

```text
Thread [fm:watch]   lab/tasks.py:45 in watch        <- the body, on a POOL WORKER
                    _schedule.py:1034 in run_node
                    concurrent/futures/thread.py:119 in _worker
Current thread      _schedule.py:1109 in _run_parallel   <- main thread, futures.wait
```

One node takes `_run_sequential` (`_schedule.py:944`) and the body runs **on
the main thread**, so the signal lands in it and unwinds. Two or more nodes and
the body is on a non-daemon pool worker; the main thread takes the signal,
prints `fm: interrupted`, and `_python_exit` then joins a thread that will
never return.

| | SIGINT | SIGTERM |
|---|---|---|
| `fm sub` / `fm pure` (alone) | 130, 0.3s | 143, 0.1s |
| `fm lint + sub` (subprocess body) | **130, 0.3s** | **143, 0.1s** |
| `fm lint + pure` (Python loop) | SIGKILL, 10s | SIGKILL, 10s |
| `fm -j=1 sub + lint` | **130, 0.2s** | **143, 0.1s** |
| `fm -j=1 pure + lint` | SIGKILL, 10s | SIGKILL, 10s |

No orphans in any case — #453's child kill holds, and #457's SIGTERM handler
works. The subprocess shape, which is what `docs.serve` actually is
(`run("zensical serve")`), is clean. **The pure-Python loop is the only broken
body**, and it is broken because CPython cannot interrupt a thread.

`--keep-going` plus a service is a guaranteed hang: keep-going means nothing
ends the run, and the service is what would have to end it.

### A service is not a task that runs forever

It is a task whose **completion condition is external**. That is a different
contract, and inverting it is the whole design:

- a **job** succeeds by terminating with status 0
- a **service** succeeds by coming up and staying up until its scope ends —
  *exiting on its own is the failure*

Once that is the definition, the special cases stop being special:

| a rule we would otherwise have to write | under the service model |
|---|---|
| "stopping an infinite task counts as success" | it is the contract |
| "an infinite task does not consume a `--jobs` slot" | it is not queued work; it is held for a scope, like the console lane |
| "`pre=[infinite]` must refuse" | it **works** — the edge is readiness, not completion |
| "a crash and a stop should read differently" | one breaks the contract, one fulfils it |
| "fail-fast must reach an infinite sibling" | ending the scope stops the service |
| "receipts must stream when a run has an infinite task" | "up" is an event, so there is something to report |
| "the pure-Python loop must not strand the pool" | held, not joined — a daemon thread is now correct rather than a hack |

Two things become *expressible* that are not today: **teardown** (the scope's
end is the stop) and **readiness handoff** (the service tells you its port).

And the chain cases stop being traps. `fm docs.serve + e2e` starts the server,
runs the tests against it, tears it down. `fm lint test + docs.serve` runs the
checks and leaves the server up. Today neither works, in either order — which
is exactly the README line that started this.

### The spelling: read the shape, do not add a flag

```python
@task
def serve(port: int = 8000) -> Iterator[str]:
    with run.background(f"zensical serve --port={port}"):
        yield f"http://localhost:{port}"     # up — everything in scope runs here
    # torn down here
```

A generator body **is** the service. Why this and not a `service=True` flag:

- it is a **typed** difference — `Iterator[T]` vs `T` — which the manifest
  already reads. "footman groups a shape it can type" is the existing rule;
  this obeys it rather than adding to it.
- `@step` already documents generator bodies. The idiom exists in the codebase.
- it converts an open audit item into a feature. Today `@task def gen(): yield`
  prints `ok gen (0.0s)` and **never runs the body** — measured. Refusing that
  closes a hole; using it closes the hole and buys services.
- the yielded value is the readiness handoff, and it is `Forward[T]`-shaped.

### The rule that makes it work: no infinite bodies

CPython cannot interrupt a thread. There is no cooperative cancellation point
inside somebody's `while True` and there never will be — that is what the stack
dump shows, and it is a property of the shape, not a bug to fix. Any design
that lets a task body block forever inherits it.

So a service body **returns**. It yields in the middle; both halves are finite;
the waiting is not in user code at all. footman is never blocked inside a body
with no way out, which is why stopping, teardown, readiness and fail-fast all
become expressible at once.

This does **not** push everything into subprocesses. In-process long-lived work
still works — the author just owns their own cooperation, which is the only
arrangement CPython permits:

```python
@task
def consume() -> Iterator[None]:
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield
    stop.set()
```

`infinite=True` survives as the deliberately **lossy** one-liner: a body that
just blocks, run on a daemon thread footman never joins and abandons at exit,
children killed by the registry on the way out. No teardown, no readiness, no
clean stop — and documented as exactly that, so the generator form is visibly
the better path rather than a second way to do the same thing. It keeps
`run("zensical serve")` a single line for the trivial case. (Whether it survives
at all is open question 4.)

### Scope is a ladder, not a choice

The first draft of this design offered two scopes and asked which. That was
wrong; it is three rungs, and the third one is what Willem's language-server
question turned up:

- **subtree** — up for one dependent's duration, torn down after
- **run** — up for the invocation
- **project** — survives invocations, keyed by (project, tool version,
  config hash)

The ladder is a shape footman already uses, for `shared` and for the config
cascade, and resolving it on the *request* rather than the declaration is the
precedent `shared` sets.

The project rung is the run-scoped once-cell lifted across process boundaries.
`_futures.py` already does *"one execution per task and arguments, whoever
asks"* within a run; a project-scoped service is that cell persisted, so
`pre=[pyright]` resolves to a running daemon or spawns one, with the same
memoisation and a wider lifetime.

**The rung has a typing consequence.** A run-scoped service can yield anything,
including a live object, because the consumer is in the same process. A
project-scoped one cannot — the next `fm` is a different process, so its yield
must be a serialisable descriptor (socket path, port, pid) that the client
reconnects from.

That is the same wall `20260727-incremental-caching.md` hit: *"a persistent
cache hit generally cannot serve a body-call, because the caller wants the
return object and most returns are not serialisable."* Same constraint, found
twice, from two directions — decent evidence the ladder is carving at a real
joint. And footman can enforce it, because it can see the annotation.

### The project rung: resident analysers

Willem's question: could footman host language servers so lints and type checks
return instantly?

Measured on this repo:

| step | wall | on re-run |
|---|---|---|
| `ruff check .` | 26 ms | 26 ms |
| `ruff format --check .` | 26 ms | 26 ms |
| `pytest --collect-only` | 0.46 s | — |
| **`basedpyright`** | **3.07 s** | **3.07 s** — no caching whatsoever |
| `pytest -q -n auto` (full suite) | 15.0 s | — |

Two conclusions, and the second one is unwelcome:

1. The lints are **already** instant. There is nothing to win there. The prize
   is entirely basedpyright, which is 3.07s and identical on re-run.
2. `fm check` runs its steps in parallel, so its wall time is the test suite at
   ~15s with basedpyright hidden underneath. **A resident type server makes
   `fm typecheck` about 30× faster and `fm check` not faster at all.**

The gate's 15s is execution, not import (collection is 0.46s), so no daemon
touches it — only affected-test selection does, and that is
`20260727-incremental-caching.md`, which already concluded "resume first, if
either."

So the honest claim is "`fm typecheck` gets instant", not "the gate does". That
is still a real loop — typecheck on save, and a monorepo cascade where a
per-package check is type-bound rather than test-bound.

**footman already has the plumbing.** `_refresh.py` is a detached child that
outlives the invocation and writes to a cache — exactly the spawn shape.
`_paths.py` has brand-scoped dirs for a socket and pidfile. `_gc.py` is the
age-sweep that reaps an idle one. LSP framing is Content-Length-delimited JSON
over a pipe: stdlib, no dependency violated, a couple hundred lines.

**The hazard is the one this project has spent a month designing against.** A
resident checker reporting green on stale state is a forged receipt —
`design.md:520`, *"No fiction. No API mints a receipt without work behind it."*
footman is not the editor, so nothing tells the daemon a file changed; the
editor owns those notifications and footman never sees them. Freshness has to
be **proven** per invocation, not assumed: stat the source set (~10ms, stdlib),
diff mtime+size against the daemon's snapshot, push `didChange` for the deltas,
pull diagnostics. When freshness cannot be proven — version mismatch, config
hash changed, an unreadable file — fall back to the cold CLI rather than guess.

A second trust problem sits underneath: `basedpyright` and
`basedpyright-langserver` need not agree. LSP is open-file-driven and resolves
config differently. If `fm typecheck` says green and CI says red, that is far
worse than three seconds. The daemon path can only ever be an accelerator —
`--cold` always available, CI never using it, and something that periodically
runs both and refuses on divergence.

And a cost to state rather than hide: an editor already running
`basedpyright-langserver` means footman's is a *second* instance, hundreds of
megabytes. LSP servers are single-client over stdio, so attaching to the
editor's is not possible.

**Do the cheap version first.** Hash the source set, cache the result, skip the
run entirely when nothing changed: 3.07s → ~10ms for the no-change case, no
daemon, no LSP, no staleness risk beyond the hash. It does nothing for the
one-file-changed case, which is the actual development loop — but it builds
precisely the freshness machinery the daemon needs, it is independently useful,
and it shares the key derivation the incremental-caching note already worked
out. Treat the resident server as the second half of that feature, not a
separate idea.

---

## Part 2 — sinks

### What was measured

A variadic consumes to end of line, including tokens that name tasks. Silently,
exit 0.

| line | result |
|---|---|
| `fm lint test build` | `lint ran`, `test ran with ('build',)`, **build never ran**, exit 0 |
| `fm many a b lint` (`paths: list[str]`) | `many ran with ['a','b','lint']`, exit 0 |
| `fm ci a b lint` (group default `*args`) | same, exit 0 |
| `fm test a b + files c d` | both run correctly |

Two variadics is **not** a separate case — the first eats the second's *name*,
so it collapses into row 1, and Python forbids two `*args` in one signature.
With `+` it already works. Nothing to design.

Completion already enforces the rule the runtime does not: `fm lint <TAB>`
offers seven sibling tasks, `fm test <TAB>` offers nothing, `fm test a <TAB>`
offers nothing. And because it offers nothing, `+` is **undiscoverable by the
mechanism footman is built around**.

**`--` silently discards on any task without `*args`:**

```console
fm lint -- build        -> lint ran, exit 0.  "build" evaporated
fm plain bob -- extra   -> plain name='bob', exit 0.  "extra" evaporated
fm many a -- --x        -> many paths=['a'], exit 0.  "--x" evaporated
```

That third line is the sharpest. `_split.py:1250` treats the two trailing
consumers as one concept, `rest` — a `multiple` positional or a `variadic`,
same branch, both greedy to end of line. `_executor.py:838` merges passthrough
only for `VAR_POSITIONAL`. **The splitter says they are the same shape; the
executor says they are not.**

**What `--` actually buys**, given a variadic already eats bare words:

| line | `pytest_args` |
|---|---|
| `fm test --port=1` | `()` — bound test's own `--port`, silently |
| `fm test -- --port=1` | `('--port=1',)` |
| `fm test -k=foo` | `('-k=foo',)` — **single-dash already flows, no `--` needed** |
| `fm test -- lint` | `('lint',)` — the only unambiguous way to pass a task's name |
| `fm test -- --tb=short + lint` | `('--tb=short', '+', 'lint')` — the literal `+` |

Three jobs: double-dash tokens, the literal `+`, and task names. Everything
else reaches a variadic without it. It correctly refuses to fill a required
fixed positional (`fm exec -- ls -la` → *missing required positional(s):
`<cmd>`*).

### The redesign

Split `rest` into the two things it is:

- a **sink** (`*args`) — opaque, terminal, receives `--`
- a **value list** (`list[str]` positional) — typed, validated, greedy

and then: **a greedy consumer ends at `+`, `--`, or a token that names a task.**

The argument that settles it: footman **already does this for fixed
positionals**. `_consume_positional` (`_split.py:1436`) refuses a token when
`_is_address(tree, tok)`. The greedy consumer is the only construct in the
grammar that does not follow the rule. This is not a new rule — it is deleting
an exception.

The payoff is the thesis. Under this rule TAB offers sibling task names after a
sink, because they would be legal — the grammar and the completion agree, and a
user discovers chaining by pressing TAB instead of by reading docs.

`--` then has one job — *"stop reading this as grammar"* — instead of three
narrow ones. And being load-bearing, it should get a `+` exit:
`fm test -- build + lint`. (Open question 5.)

`--` on a task with no sink should **refuse**, not evaporate. There the words go
nowhere at all; that is a hole, not an ambiguity.

---

## Rejected

- **Refuse the swallowed token (exit 64).** `pytest build` is a legitimate line
  and `build` is a legitimate directory. Refusing breaks real usage.
- **Advisory note only, generalising `_default_notes` from group children to the
  whole tree.** This was the first proposal and it is the wrong shape: there is
  no complete escape hatch (`--` costs the rest of the line, and `./build`
  changes the value) and no way to silence it. `_default_notes` carries the same
  weakness today for non-path values.
- **Keep `infinite=True` and patch the exceptions.** That is how it got here —
  seven local rules, each individually defensible.
- **Ban in-process long-lived work.** Unnecessary. The author can own the
  cooperation; footman only needs the body to return.
- **Attach to the editor's language server.** LSP servers are single-client over
  stdio. Not possible; footman's is a second instance.
- **A resident pytest.** The suite's 15s is execution, not import (collect is
  0.46s). A warm process wins nothing. Test *selection* is the lever, and it is
  the incremental-caching note's problem.

## Open — Willem's call

1. **How is a service's scope resolved?** A ladder on the request, as `shared`
   does? `fm serve + a + b` says the run; `pre=[serve]` says the dependent's
   subtree. Same task, two scopes, decided by how it was reached. The `shared`
   precedent probably carries it, but it is the load-bearing assumption.
2. **Exit code on a clean stop.** `fm docs.serve`, Ctrl-C, the service did its
   job: 0 or 130? Leaning 130 when anything was still pending and 0 when the
   service was all that remained — principled, but it makes the exit code depend
   on the plan.
3. **Does `--jobs` bound services?** If they do not count, `--jobs` means
   "concurrent jobs" and services are unbounded. Probably right, but
   `fm a.serve + b.serve + c.serve` with no limit is a thing someone will do.
4. **Does `infinite=True` survive** as the lossy one-liner, or is a blocking
   body simply refused and the generator form made the only spelling?
5. **Note or refuse for the swallowed token, and does `--` get a `+` exit?**
   These are coupled: whether "note" is defensible depends on whether `--` is a
   complete escape hatch, and today it is not.

Questions 1 and 4 gate the service build. Question 5 gates the sink build. The
two builds do not touch each other and can land in either order — the sink half
is smaller, is mostly deleting an exception, and has a visible payoff in TAB.

## Appendix — reproductions

Every measurement above came from throwaway task files driven against a
worktree at `913a59b`. The shapes worth keeping:

- **slot occupancy** needs a *bounded* stand-in — `@task(infinite=True)` whose
  body sleeps 3s and returns — or the run never completes and no receipts print.
- **signal behaviour** needs the child in its own process group
  (`start_new_session=True`) and `os.killpg`, plus a `communicate(timeout=…)`
  fallback to SIGKILL. A `timeout -s KILL` wrapper kills `fm` only, not its
  group, so any surviving grandchild is the harness's doing and not footman's.
- **the subprocess/pure-loop distinction is essential.** Measuring with
  `while True: sleep()` reports a hang that the real `docs.serve` shape
  (`run("zensical serve")`) does not have. The first draft of this analysis got
  that wrong.
- **`--complete` takes a word list, not a line**: `fm --complete -- fm test ""`.
  A cold cache answers empty; warm it with any real invocation first.
