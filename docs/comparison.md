# Comparison

How footman stacks up against the Python task runners I measured it against —
the same seven-task surface (`lint`, `format`, `typecheck`, `test`, `check`,
`dist build`, `dist clean`) written five ways. The runnable head-to-head lives in
the repo's [`comparison/`](https://github.com/willemkokke/footman/tree/main/comparison)
directory; reproduce the numbers with
`uv run --group comparison python comparison/bench_compare.py`. Switching from
one of these runners? The practical guides live on [Migrating](migrating.md).

Measured on duty 1.9.0, invoke 3.0.3, poethepoet 0.48.0, typer 0.27.0, CPython
3.13, M-series Mac.

!!! note "Verified, not vibes"

    Every number and checkmark here was checked against the tools themselves,
    on the same seven tasks. If any of it is wrong or has become unfair to a
    tool, [open an issue](https://github.com/willemkokke/footman/issues) —
    it will be fixed.

## First, some love for duty

Before any table makes footman look clever: I've been running my projects on
[duty](https://pawamoy.github.io/duty/) for nearly two years, and it's been a
pleasure the whole way. footman exists *because* of duty — the `ctx.run` capture
model, the lazy tool wrappers, the decorator ergonomics are all ideas I'm
happily standing on. This is a "here's what I wanted to tweak," not a takedown.
And duty still wins outright in one place: its `tools` standard library is far
more extensive and detailed than footman's handful — dozens of tools, carefully
typed. footman has some catching up to do there.

## Completion latency

Cold-process wall time per `<TAB>`, mean of 15 fresh processes. The **Δ import**
column is the one that matters: completion time with a 0.25 s project-import cost
minus completion time without it. Re-import your tasks on every keystroke and
you see the whole ~0.25 s; answer from a cache and you see roughly nothing.

| runner  | completion (per TAB) | Δ import | re-imports every TAB?    |
| ------- | -------------------: | -------: | ------------------------ |
| footman |            **23 ms** |    ~0 ms | no — cached manifest     |
| poe     |                45 ms |    ~0 ms | no — reads TOML          |
| duty    |               346 ms |   286 ms | yes                      |
| invoke  |               360 ms |   289 ms | yes                      |

duty and invoke reload your whole project on every TAB — their completion
scripts call the tool, which imports your tasks before it can answer. footman
reads a cached JSON manifest instead and never imports a thing, so it lands about
15× faster. It pays the same import cost as everyone else, just on the execution
path: `fm --list` is ~313 ms, right there with the pack. Completion is the one
moment that has to feel instant, so that's the moment I optimised. poe is quick
here too, for the honest reason that its tasks are TOML strings with no Python to
load — which is also the rest of this page.

## The same `check`, composed five ways

Completion is the moment that has to feel instant; `check` is the command you
actually run fifty times a day. So: four check steps, each an identical
in-process 0.5 s sleep (the honest stand-in for an I/O-bound tool run — a
real lint step spawns a subprocess and waits, which parallelises exactly like
a sleep), composed the way each tool wants you to. Fairness cuts both ways —
a tool that supports parallelism gets to use it. Reproduce with
`uv run --group comparison python comparison/bench_check.py`.

| runner  | composition                    | wall (mean) |
| ------- | ------------------------------ | ----------: |
| footman | parallel (pre-deps, *default*) |  **563 ms** |
| poe     | parallel (`parallel` task)     |      625 ms |
| typer   | serial (no orchestration)      |     2092 ms |
| duty    | serial (pre-duties)            |     2120 ms |
| invoke  | serial (pre-tasks)             |     2146 ms |

The floors are 0.5 s parallel and 2.0 s serial, so everyone's *overhead* is
a rounding error — the 4× gap is architecture, not dispatch speed. duty and
invoke run prerequisites serially and have no parallel switch to flip; the
same four steps simply cost the sum instead of the max. poe genuinely ticks
this box (a dedicated `parallel` task type — credit where due); the
difference is spelling. In poe you declare a parallel composite per case; in
footman `pre=[fmt, lint, typecheck, test]` is parallel *by default* and goes
serial only when you ask (`-s`). And typer hands you nothing here — four
calls in a row, unless you hand-roll a thread pool, at which point you've
written the scheduler yourself.

## Is "just write a typer app" too heavy?

Genuine question, because typer is lovely and a completely reasonable choice — if
you're building a user-facing CLI rather than a task runner, honestly, reach for
typer. It's also footman's closest relative here: typed signatures, real flags,
`Enum`/`Literal` validation, nested apps. The only thing I measured was startup,
because typer has a reputation for being heavy:

| import           | cost over a bare interpreter |
| ---------------- | ---------------------------: |
| `import footman` |                     **+4 ms** |
| `import typer`   |                    **+24 ms** |

typer's import really is ~6× heavier — it ships its own parser plus `rich` and
`shellingham`. (Reproduce with
`uv run --group comparison python scripts/bench_import.py`.) On a single launch you'd never notice (footman ~38 ms, typer
~40 ms; footman just spends its milliseconds on parsing instead of importing).
The difference only shows up when a typer app does completion, because that
re-runs the app — paying the typer import *and* your project import on every TAB,
where footman is answering from cache. Not a knock on typer; just a different job.

## Feature matrix

The list is footman's own feature set, so the left column is green by
construction — the honest content is in the other columns, and in the one ❌
footman concedes: duty's tools library.

| capability                                  | footman | typer   | duty          | invoke        | poe      |
| ------------------------------------------- | :-----: | :-----: | ------------- | ------------- | -------- |
| Typed Python-function tasks                 |   ✅    |   ✅    | ✅            | ✅            | ❌       |
| No `ctx`/`c` boilerplate param              |   ✅    |   ✅    | ❌            | ❌            | —        |
| Real `--flags`                              |   ✅    |   ✅    | ✅            | ✅            | ✅       |
| `Literal`/`Enum` → validated choices        |   ✅    |   ✅    | ❌            | ❌            | ❌       |
| Union / one-or-many / `dict[K,V]` params    |   ✅    | partial | ❌            | ❌            | ❌       |
| Native nested groups                        |   ✅    | ✅      | ❌            | manual        | ❌       |
| Zero-boilerplate discovery (module = group) |   ✅    |   ❌    | ❌            | ❌            | ❌       |
| Separator-free chaining                     |   ✅    |   ❌    | reserved-word | reserved-word | seq task |
| Parallel-by-default DAG (`pre`/`post`)      |   ✅    |   ❌    | serial        | serial        | ✅       |
| `run()` capture / replay-on-failure         |   ✅    |   ❌    | ✅            | partial       | ❌       |
| Extensive typed `tools` standard library    |   ❌    |   ❌    | ✅            | ❌            | ❌       |
| Monorepo `tasks.py` cascade                 |   ✅    |   ❌    | ❌            | ❌            | ❌       |
| Custom-branded CLI as a library             |   ✅    |   ✅    | ❌            | ❌            | ❌       |
| Completion without re-importing             |   ✅    |   ❌    | ❌            | ❌            | ✅\*     |
| Zero runtime dependencies                   |   ✅    |   ❌    | ❌            | ❌            | ❌       |

\* poe skips the re-import only because its tasks aren't Python functions.
