# Roadmap

This file began as a critical self-audit of footman at **v0.4.0** — every
claim checked against the source, file and line. Ten releases later, almost
all of it has shipped. The file now does two jobs: the road ahead, and — for
posterity — the original audit preserved item by item, each with the release
that closed it. The full stories live in the
[changelog](https://willemkokke.github.io/footman/changelog/).

**Where footman stands (2026-07-20, v0.15.0, Beta on PyPI).** The typed core
— coercion, chain grammar, manifest, scheduler, cascade — held up; everything
since has been built on it without structural change. The runner now has a
real help story, a testing story, a composition story, completion installed
and functionally tested on five real shells, a tools bridge with typed stubs,
one `--json` envelope for machines, docstring-driven parameter docs, markdown
export of the task surface, a progress bar that earns its confidence from
duration history, and one colour palette across the whole CLI. Coverage is
enforced at ≥ 92%, CI runs 3 OS × Python 3.11–3.14 including free-threaded,
and a tag cannot publish unless CI and the version checks agree.

## The road to 1.0

What actually remains, in order:

- **Help strings that carry the whole truth.** The CLI reference table is now
  generated from the grammar, so the help strings are the single source for
  `--help`, completion menus, and the docs at once. A short pass to make them
  worthy of that job (for example, `--jobs` doesn't mention the floor of 2).
- **The tools surface, properly** — the 1.0 headline, and the last
  known wart. Stubs generated from each tool's own metadata (click
  exposes `opts`, `secondary_opts`, defaults, and help as data;
  argparse and `--help` drivers behind the same interface), a
  reference page per tool, and a `tools audit` task that diffs the
  committed stubs against the installed tools so drift fails a check
  instead of lying. It also fixes `off`: the negation is per-flag data
  only the tool knows — `mkdocs build --no-clean` is rejected, the real
  flag is `--dirty`, and 5 of mkdocs' 8 negatable flags disagree with
  the `--no-<name>` assumption. Waits for the work migration, which
  will say which tools matter most.
- **The stability promise, written down**: decorator surface, CLI grammar,
  `--json` schema additive-only, manifest format additive-only. Then a bake
  cycle with no breaking changes.
- **The 1.0 flip**: pre-1.0 warnings out, promise in — one coordinated
  change, with a TestPyPI dry-run before the real tag. (The
  `Development Status` classifier already moved Alpha → Beta in 0.12.0.)

## New since the audit

Ideas that came out of building the last eight releases, not in the original
plan:

- **A generated `[tool.footman]` reference.** The same never-drift treatment
  the global-options table got in `fm footman docs globals`: render the
  config keys and defaults from the source, snippet-include them in the docs.
- **Dedup the branch-vs-PR CI runs.** A PR from an in-repo branch triggers
  the suite twice — once for the push, once for the PR. A concurrency group
  or a branch filter should make it one.

## After 1.0 — the backlog

Not gating anything, carried forward minus the entries that shipped
(task-returned JSON payloads landed in 0.10.0, the TTY progress UI grew into
0.12.0's history-backed bar, PowerShell/nushell completion landed in 0.8.0):

- **Watch mode** — `fm --watch lint`: re-run on file change, debounced.
- **JSONL event streaming** — `--json` is a summary; agents and CI dashboards
  want per-event lines as tasks start and finish.
- **Fingerprint-based skipping** — "inputs unchanged, skip the task"
  (doit/turborepo territory; big, and the DAG is already in place).
- **Per-task timeout and retry** — `@task(timeout=120, retries=2)`.
- **`fm --plugins`** — list installed `footman.tasks` entry points with
  dist, version, and enabled state.
- **`fm new`** — scaffold a tasks.py that demonstrates the good idioms.
- **Handoffs for other package managers** (poetry, pdm) — if there's a
  want. uv shipped first because `uv.lock` makes the fire-rule
  unambiguous; each manager needs an equally sharp rule of its own.
- From the typing table's "post-1.0" rows: hidden parameters, and fixed-arity
  `tuple[X, Y]` in comma form (`--size 800,600`).

The "never" list from the audit is still never, for the same reasons:
prompts/confirmation (a chained, parallel, CI-first runner is the most
hostile environment interactivity has ever met), counting flags (`-vvv`
belongs to the runner, not task params), and short aliases for task
parameters (collision-prone across cascade merges, and they steal
negative-number positionals). Saying never here is what keeps the grammar
deterministic — the thing that makes separator-free chaining possible.

---

## The v0.4.0 audit, for posterity

Everything below is the original audit, condensed to one line per item, with
the release that closed it. Section numbers match the original.

### §1 Bugs — all thirteen, fixed in 0.5.0

| # | Bug | Landed |
| - | --- | ------ |
| 1 | `fm --help build` executed `build` | 0.5.0 — help anywhere before `--` is read-only |
| 2 | Cyclic `pre`/`post` deps → silent exit 0 | 0.5.0 — taught error naming the cycle |
| 3 | `bool` inside collections always `True` | 0.5.0 — real bool token type |
| 4 | `list[bool]` collapsed to a single flag | 0.5.0 |
| 5 | Malformed config TOML silently ignored | 0.5.0 — discovered config warns; `--config` errors |
| 6 | Crashing strict `suggest()` disabled validation | 0.5.0 — fails the run |
| 7 | Ctrl-C → raw traceback | 0.5.0 — cancelled, `interrupted`, exit 130 |
| 8 | Windows `run("...")` mangled by POSIX `shlex` | 0.5.0 — string goes to `CreateProcess` whole |
| 9 | Non-UTF-8 subprocess output crashed | 0.5.0 — `errors="replace"` |
| 10 | Duplicate task name misreported as import crash | 0.5.0 — named user error |
| 11 | `"²".isdigit()` → `int()` traceback | 0.5.0 — taught type error |
| 12 | `--dry-run` recorded no `StepResult` | 0.5.0 — records, honours `quiet` |
| 13 | `py.typed` missing | 0.5.0 — shipped, checked by the release gate |

### §2 Half-baked and dead surface

| Item | Resolution |
| ---- | ---------- |
| `--refresh-manifest` no-op | removed, 0.7.0 |
| `--install-completion` printed "not wired up yet" | wired: bash/zsh/fish 0.7.0, pwsh/nushell + shell detection 0.8.0 |
| README pessimistic about `-v`/`--no-color` | README rewritten as a front door, 0.8.0 |
| Per-task `--help` didn't exist | shipped, 0.5.0 |
| `manifest.is_stale` + `sources` never consulted | removed, 0.7.0 (real freshness arrived as stale-while-revalidate in 0.9.0) |
| `executor.run_chain` had no callers | kept and wired: it became the binding-test harness the suites drive |
| `tools` load-bearing but not exported | public (`__all__`, lazy), 0.7.0 |
| `Group` unrunnable, `Context` unconstructable, `reset()` public | `Runner`/`use_context()` 0.6.0; `reset()` out of the root namespace 0.7.0 |
| `tools.*` seven wrappers vs duty's dozens | the bridge (any executable, no declaration), 0.8.0; typed stubs 0.9.0 |

### §3 Release engineering

| Item | Landed |
| ---- | ------ |
| Any `v*` tag published unverified | 0.5.0 — release runs full CI on the tagged commit |
| Version in two places, checked by nothing | 0.5.0 — tag = pyproject = `__version__` = changelog, enforced |
| Coverage reported, never enforced | 0.5.0 — `fail_under = 92` in CI |
| Docs built strictly only after merge | 0.5.0 — strict build on every PR |
| Missing URLs, dead changelog links, sdist excludes | 0.5.0–0.8.0 housekeeping |
| Alpha classifier + warnings, one coordinated flip | Beta in 0.12.0; the written promise is the road to 1.0 above |

### §4 Test-suite gaps

The hostile-world column filled in: signals, Windows backslash paths, and
non-UTF-8 bytes with the 0.5.0 fixes they guard; the manifest, cascade, and
coercion suites deepened across 0.5.0–0.9.0 (0.9.0's correctness pass drove
coercion, scheduler, and tools through their edges); and 0.8.0 added the
functional column nobody asked for in the audit — every completion hook
driven against its real shell in CI, which is how the bash 3.2 and
PowerShell empty-argument bugs were caught for keeps.

### §5 The testing story

Shipped whole in **0.6.0**: `footman.testing` (`Runner.invoke`,
`recording()`, `use_context()`), three auto-loaded pytest fixtures (`fm`,
`fm_project`, `fm_record`) at zero runtime dependencies, footman's own suite
dogfooding them, and the *Testing your tasks* docs page. The `--json`
envelope (`{"schema": 1, ...}`) landed earlier, in 0.5.0, exactly so
breaking was still free.

### §6 The composition story

Shipped whole in **0.7.0**: `@task(when=…, reason=…)` disable-but-list,
`include(source, into=…, only=…, exclude=…, override=…)`, the
`footman.tasks` entry point with opt-in `[tool.footman] plugins`,
`registry.capture()` as the public seam, and the *Composing tasks* page.
`@task(requires=…)` followed in 0.9.0 as the import-free dependency gate,
reusing the same availability machinery. The "hiding is an `if` statement"
stance held — no kwarg was ever added.

### §7 Typing parity

| Gap | Landed |
| --- | ------ |
| `bool` in collections | 0.5.0 |
| `exists` / `isfile` / `isdir` | 0.6.0 |
| `between(lo, hi)` / bare `range` | 0.6.0 |
| `env("VAR")` fallback | 0.6.0 — CLI > env > default, same coercion path |
| `check(fn)` validator | 0.6.0 — post-coercion, per element |
| Silent `str` degrade of unknown annotations | 0.6.0 — warns |
| Hidden params, `tuple[X, Y]` | still post-1.0 (backlog above) |
| Prompts, counting flags, short aliases | still never |

### §8 Completion and CLI polish

| Item | Landed |
| ---- | ------ |
| `--install-completion` bash/zsh/fish | 0.7.0 |
| pwsh/nushell installers, shell detection | 0.8.0 |
| Chain-aware completion | 0.7.0 — the resolver walks segments like the splitter |
| Latency headline honesty | 0.10.0 — ~25 ms measured by a committed benchmark, quoted everywhere |
| Wire-or-delete the dead flags | 0.7.0 |
| Public-surface hygiene | 0.6.0–0.7.0 |
| Grow `tools.*` | 0.8.0 bridge, 0.9.0 in-process + stubs |

Beyond the audit's asks: functional tests against all five real shells
(0.8.0), completions that teach and stay fresh (0.9.0), global-flag
completion, `--setup-completion`, `--uninstall-completion`, per-shell docs
pages, and descriptions in every shell that renders them (0.10.0).

### §9 Docs

| Item | Landed |
| ---- | ------ |
| README as a drifting near-superset of the site | 0.8.0 — a front door with pointers |
| `testing.md` | 0.6.0 |
| `composing.md` | 0.7.0 |
| CI page, troubleshooting catalogue | 0.8.0 |
| Benchmark honesty (import cost, completion latency) | 0.7.0 and 0.10.0 — committed scripts behind both |
| Voice pass over the older pages | 0.10.0 docs cycle — restructure, tabs, one voice |
| The cookbook | landed 2026-07-20, post-0.14.0 — seventeen recipes, agents included |

The audit's other docs worry — the hand-maintained global-options table that
"*will* drift" — proved right on schedule: it drifted three ways and is now
generated from the grammar on every docs build (unreleased, post-0.12.0).

### §10 The release train

Went to plan: 0.5.0 (bugs + release engineering + envelope), 0.6.0
(testing + typing), 0.7.0 (composition + completion) shipped as the table
said, in two days rather than four cycles. Reality then added stops the plan didn't
know about: 0.8.0 (the bridge, all five shells, real-shell CI), 0.9.0 (the
correctness pass, in-process tools, stubs), 0.10.0 (the one-envelope `--json`
contract, `doc()`, agents + llms.txt), 0.11.0 (docstring parameter docs, the
stdout/stderr contract, markdown export), 0.12.0 (the progress bar with
duration history, `-j/--jobs`, `FOOTMAN_CACHE_DIR`, one colour palette,
Beta).

### §11 The original backlog

| Item | Status |
| ---- | ------ |
| Task-customizable `--json` payloads | shipped, 0.10.0 — `returned`, symmetric with what footman coerces in |
| A TTY progress UI for the DAG | shipped, 0.12.0 — and it learned to estimate from duration history |
| PowerShell/nushell completion | shipped, 0.8.0 |
| Watch mode, JSONL streaming, fingerprint skipping, timeout/retry, `fm --plugins`, `fm new` | open — carried in the backlog above |

---

*The original audit was generated from a full source read at v0.4.0 (commit
9328109) and is preserved in the git history of this file. This revision
reflects v0.12.0.*
