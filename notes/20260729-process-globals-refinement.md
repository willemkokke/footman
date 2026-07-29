# Process globals: a refinement pass

Planned 2026-07-29. Reasoning for the env change lives in
[20260729-env-handoff.md](20260729-env-handoff.md) — read that first; this
note is the work.

Four findings from one thread that started with "can a tool call skip being a
step". Three are worth doing, one is worth recording and leaving alone. The
theme: the virtualisation is right, but two of its models are more complex
than the problem, and one guard cries wolf.

## 1. `ctx.env` becomes the task's environment (the big one)

**Today.** A module-global `_snapshot` (a copy of `os.environ` pinned at the
run boundary) plus a per-task additive overlay, merged at three sites in three
spellings: `_merged()` uses `{**_snapshot, **overlay}`, the Popen router uses
`{**_snapshot, **ctx.env}`, `run()` uses `{**os.environ, **ctx.env, **env}` —
the last agreeing with the others only *because* `os.environ` is itself
virtualised, an invisible coupling. Plus an additive-only rule, an exception
to it (you may delete keys you set yourself), and a taught error for the rest.

**After.** `ctx.env` is initialised from the process environment and simply
*is* the task's environment. `os.environ` is virtualised onto it. `run(env=)`
**replaces**, exactly as `subprocess` does.

- `_merged()` → `ctx.env`; Popen injection → `ctx.env`; `run_env` →
  `{**ctx.env, **(env or {})}`.
- Deletion is ordinary `del`. No tombstone, no rule, no exception, no taught
  error.
- `_toolhelp.read_env()` disappears (it is "snapshot minus three", hand-rolled).
- The `isolated=` flag designed earlier is **not built** — it existed only to
  work around the overlay's inability to subtract.
- Both standard idioms work with no footman vocabulary:
  `run(env={**os.environ, "CI": "1"})` to add; `e = dict(os.environ);
  del e["X"]; run(env=e)` to remove.

**Why replace and not merge:** merging defeats copy-modify-pass — a deleted
key returns from `ctx.env`, which is the original bug relocated.

**Migration.** Four `env=` call sites in the whole tree; one is an overlay
(`tasks.py`, `COVERAGE_FILE`) and becomes `{**os.environ, …}`. `Context(env=)`
changes meaning from overlay to whole environment: a `default_factory`
snapshot covers construction, the explicit sites (`context.py:324`, tests)
need deliberate migration. `run(env={})` becomes "no `PATH`" — conventional
danger, identical to `subprocess`, so the reflex already exists.

## 2. The `getcwd` note stops crying wolf

**Today** it fires whenever a managed parallel task reads `os.getcwd()`, with
no check on whether the answer is actually misleading:

```python
if guarded:
    _note("getcwd", "…reads the process cwd — in a parallel run it can be
                     anyone's…")
```

Run `fm` from the directory your tasks file lives in — the common case — and
`ctx.cwd` *is* the process cwd, the read is correct, and you are told it might
not be. In-process tools trip it constantly (a subprocess never can: footman
passes `cwd=` at spawn).

**After:** note only when `ctx.cwd` differs from the real process directory.

The sibling guard already learned exactly this in PR #180 — `chdir` to where
the process already is "changes nothing for anyone" and stopped being refused
(pytest's defensive restore). `getcwd` never got the equivalent.

**Cost to watch:** `chdir` can afford `realpath` because it is rare; `getcwd`
may be polled in a loop. Compare the already-resolved `ctx.cwd` against a
cached process cwd rather than resolving per call.

## 3. `cwd` + `cwd_unmanaged` collapse into one field

Two fields encode one concept, so `None if ctx.cwd_unmanaged else ctx.cwd`
recurs and every reader must check both. The three real states —
*unresolved*, *managed(path)*, *unmanaged* — fit one field with a sentinel:
`None` / `Path` / `UNMANAGED`. Cosmetic, cheap, do it while touching the area.

## 4. `sys.argv` — one bug fixed, one question deferred

Two separable things were tangled here.

**Fixed (done 2026-07-29).** `_ArgvProxy` overrode 18 list operations and
missed the in-place mutators — `__iadd__`, `__imul__`, `sort`, `reverse`,
`__reversed__`, `__mul__`. Unoverridden they fall through to `list`, which
edits the proxy's *base* storage, so a legacy `main()` doing
`sys.argv += ["-v"]` suffered both failures at once: the append **vanished**
from its own subsequent reads (those consult the override) *and* leaked into
every call without one. Silent in both directions. Two tests pin it — written
to fail first, and they did: the caller saw `['tool', '--x']` after appending,
and `reverse()` left the view untouched while reordering the base.

**Deferred: is the tools bridge's lock fallback enough on its own?**
`_ArgvProxy` is 95 lines for one case (a zero-argument `main()` that reads
`sys.argv`, parallelised), and carries holes that cannot be closed from
Python: a C extension reading the list storage sees the base, and
`sys.argv = [...]` replaces the proxy outright.

The bridge's alternative is a lock plus save/restore. It is *complete* —
nothing to intercept, so no method can be missed — but it is globally
visible: while held, `sys.argv` really is the patched list for every thread,
so it isolates nothing. The proxy is the better design; the lock is the more
complete implementation.

**Not redundant, and not reachable from a task.** The routers install only in
`run_plan`, so inside a task `_pg.active()` is true and the proxy always
wins. The lock is the *no-run* path — a script or REPL importing
`footman.tools`, or a test calling a tool directly — which is precisely the
standalone-library seam. Revisit whether the lock alone suffices **when that
split happens**, not before.

## Order, and what it unblocks

1. **(2) and (3)** first — small, independent, no migration.
2. **(1)** next, as its own change: the model, then the four call sites, then
   `Context(env=)`, then `read_env()`'s retirement.
3. **Then the conversion this thread started for**: the nine raw `subprocess`
   probes in `_toolhelp`, `_provision`, `_colorprobe`, `_toolfetch`,
   `_drivers` move to `run(step=False, timeout=…)` and gain `ctx.cwd`/
   `ctx.env`, colour handling, Windows quoting and the encoding policy they
   currently hand-roll.

**Still open, blocking step 3 only:** four of those sites pass
`creationflags=NO_CONSOLE_WINDOW` and `run()` cannot express it. The
interesting option is that `run()` should set it for *every* captured Windows
run — `_toolhelp`'s comment argues that case — but it is a Windows behaviour
change that wants verifying on Windows.

## Verification

`fm check` throughout. The env change specifically wants: a task deleting a
base key and its children not seeing it; `run(env=…)` handing over exactly
what was passed; the Popen router injecting the same value `run()` would; and
a parallel run where two tasks mutate their environments without seeing each
other's. The `getcwd` refinement wants a test that a task whose `ctx.cwd`
matches the process cwd gets **no** note, and one whose differs still does.
