# Startup cost: where the no-op launch went

Written 2026-08-16, chasing the gap between `comparison.md`'s "~38 ms" and a
measured ~67 ms. All numbers from one quiet machine (M-series, load < 4),
min-of-25 fresh processes unless said otherwise.

## The number did not regress — it aged

`~38 ms` was written 2026-07-16 at **v0.4.0**, and it was right: walking the
released wheels, 0.4.0 measures **38.9 ms**. It has not been re-measured in
the 37 releases since.

| version | `fm noop` | what landed |
|--------:|----------:|-------------|
| 0.4.0   | 38.9 ms   | the number was written here |
| 0.11.0  | 41.4 ms   | |
| 0.12.0  | 54.7 ms   | the progress bar (`statistics`, `hashlib`) |
| 0.20.0  | 61.3 ms   | |
| 0.30.0  | 72.5 ms   | |
| 0.38.0  | 66.9 ms   | the perf-polish release, -6 ms |
| 0.41.0  | 66.7 ms   | |

## The shape of a launch

| stage | cost |
|-------|-----:|
| bare interpreter | 13.9 ms |
| `import footman` | +1.5 ms |
| answering `--version` | **+29.5 ms** |
| `--list` over `--version` | +2.6 ms |
| running a no-op | +8.2 ms |

`import footman` costing 1.5 ms means the lazy `__init__` works. Almost
everything lives in the single `from footman.app import App` that `main()`
does before it can dispatch, so `--version`, `--list` and every run pay the
same bill.

Imports are ~60% of the total. The rest is the interpreter, and ~8 ms of
actual work.

## What landed

Three commits, gate green each time, concurrency suites also run serially.

- **`concurrent.futures` off the run path.** Two sites already deferred it,
  but "the moment of use" was every executed task: `_Cell.__init__` claimed a
  `Future`. It drags `logging` through `traceback`, and `logging` drags
  `_colorize`. The five methods used are a `threading.Event` and two slots.
- **A one-node plan runs on the calling thread**, no `ThreadPoolExecutor`.
  The *regime* is deliberately unchanged — routing single nodes to
  `_run_sequential` would have been shorter and wrong, because it builds a
  sequential context and `os.chdir` would then be legal in a one-task chain
  and refused in a two-task one.
- **`_install_multiprocessing` only patches if multiprocessing is imported.**
- **`statistics` moved past the guard in `estimate()`.**
- **`uv` resolved at the point of use**, not before six early returns.
- **`shutil` deferred in four modules** — 1.9 ms of archive codecs for one
  `get_terminal_size` and two `which` calls.
- **`docstrings` deferred into `_task_node`.**

| | before | after |
|---|---:|---:|
| `fm noop` median | 70.5 ms | ~61 ms |
| `fm --version` | 47.2 ms | 44.0 ms |
| `fm --list` | 49.6 ms | 47.3 ms |
| import self-time | 61.0 ms | ~35 ms |
| modules imported | 193 | 167 |

## The biggest thing left, and why it did not land

**Per-run cost is O(tasks) and could be O(1).** `sync_manifest` calls
`build_manifest` on every run — resolving every task's signature and parsing
every docstring — and only *then* compares hashes to decide whether to write.
The build is the cost; the write is already guarded.

Measured against a build that is skipped outright:

| tasks | rebuilds | skipped | saving |
|------:|---------:|--------:|-------:|
| 1     | 59.1 ms  | 56.2 ms | 2.9 ms |
| 50    | 63.4 ms  | 56.4 ms | 7.0 ms |
| 150   | 68.7 ms  | 61.5 ms | 7.2 ms |
| 400   | 87.5 ms  | 60.0 ms | **27.5 ms** |

Skipping it makes the run flat in task count. ~70 us per task, every run.

Not attempted here: the guard has to be *correct*, and the tree depends on
more than one tasks file's mtime — the cascade, config, plugins and the
`-f`/`-C` probes all feed it. Serving a stale manifest would break the
headline feature. This wants designing, not a quick mtime check two days
before a launch.

## Dead ends, closed with measurements

Recorded so nobody spends a day on them again.

- **`dataclasses` is not the problem.** It looks like the biggest stdlib item
  at 5.41 ms cumulative, dragging `inspect` (3.24) and `re` (1.42). But
  `_manifest` imports `inspect` directly for `inspect.signature`, and `re` is
  used by `_describe`, `_script` and `docstrings` — all loaded anyway. True
  marginal cost of `dataclasses` itself: **~0.28 ms**. A large refactor for
  nothing.
- **File I/O is not the problem.** A no-op opens 14 files, and cProfile
  blames 13 ms on `_io.open` — almost all of it profiler overhead. Unprofiled,
  each read is ~20 us. The config cascade is read twice and the timing history
  three times; deduping both saves under 0.1 ms combined. (Most of the
  "reads" of the user config are *failed* opens, which are nearly free.)
- **The editable install is not the problem.** Wheel install vs editable,
  same code and interpreter: +0.9 ms, inside the noise.
- **Bytecode size is not the problem.** footman's modules average 14 us/KB,
  but the spread is 5–52 us/KB. Modules far above the line are doing work on
  the way in (compiled regex tables, dataclass construction), not paying for
  their size. That ratio is the useful thing to look at next time.

## Still on the table

- footman's own 22 modules: ~11.9 ms.
- `tempfile` → `shutil` (~1.9 ms). `_globals.install()` warms
  `tempfile.gettempdir()` before arming the routers on purpose, so a task's
  first `mkdtemp` doesn't trip the getcwd note. Worth keeping.
- `tomllib` 0.85 ms, `hashlib` 0.66 ms.
- 13.9 ms of it is the interpreter, which none of this touches.

## Consequence for the docs

`comparison.md:106` still quotes "footman ~38 ms, typer ~40 ms" for a single
launch. Measured on 3.13 today: footman **79 ms**, typer **56 ms** — and the
page ships the command that shows it. The completion claim it is really
selling holds and is better than stated: **0 ms** of project-import cost per
TAB against duty's and invoke's ~260 ms.
