# The road to 1.0

A critical self-audit of footman at v0.4.0, and the plan that falls out of it.
Every claim below was checked against the source — file and line — not
remembered from the README. Where something is broken, it says broken.

> [!NOTE]
> **The verdict, in three sentences.** The typed core — coercion, chain
> grammar, manifest, scheduler, cascade — is genuinely solid and better tested
> than most released tools (~180 tests, 3 OS × 3 Python CI). What stands
> between here and a 1.0 worth trusting is a dozen small correctness bugs, a
> release pipeline that would happily publish a broken tag, and two missing
> stories (testing your tasks, composing tasks from elsewhere) that separate a
> good task runner from the premier one. None of it is structural; all of it
> is listed below.

## 1. Bugs

Found by reading, confirmed against the code paths. In rough order of
severity — the first two can ruin someone's afternoon.

| # | Bug | Where |
| - | --- | ----- |
| 1 | `fm --help build` **executes `build`** | `_app.py` (run has no help branch) |
| 2 | Cyclic `pre`/`post` deps → **silent exit 0**, nothing runs | `schedule.py:162-181` |
| 3 | `bool` inside collections is always `True` — `--flags x=false` → `True` | `coerce.py:31-40` |
| 4 | `list[bool]` silently collapses to a single flag | `manifest.py:93-95` |
| 5 | Malformed config TOML is **silently ignored** → defaults | `config.py:31-36` |
| 6 | A crashing strict `suggest()` completer **disables validation** | `manifest.py:116-124` → `split.py:90-91` |
| 7 | Ctrl-C during a run → raw `KeyboardInterrupt` traceback | no signal handling anywhere |
| 8 | Windows: `run("...")` uses POSIX `shlex.split` — backslash paths mangle | `context.py:199` |
| 9 | Non-UTF-8 subprocess output → unhandled `UnicodeDecodeError` | `context.py:165` |
| 10 | Duplicate task name reported as "failed to import … ValueError" | `_app.py:249-254` |
| 11 | `"²".isdigit()` is true, `int("²")` raises → traceback | `coerce.py:160-176` |
| 12 | `--dry-run` never records a `StepResult` (prints and returns first) | `context.py:185-187` |
| 13 | **`py.typed` is missing** — the `Typing :: Typed` classifier currently lies | packaging |

Reading the table: #1 is the worst kind of bug — the universal "don't do
anything" flag does the thing. #2 means a typo'd dependency graph *looks like
success* in CI. #3/#4 are silent wrong answers, which is exactly what eager
validation exists to prevent. #6 deserves a sentence: `strict=True` promises
validation, a raised completer is swallowed to `[]`, and an empty choice list
skips the check entirely — the feature quietly turns itself off. #13 is a
one-file fix with outsized shame potential: a typed task runner whose types
no downstream checker can see.

Each fix lands with a regression test in the matching test file. The fixes
for #1 and #2 also decide product surface: `--help <task>` should render real
per-task help from the manifest (a missing feature, not just a bug), and a
dependency cycle should be a taught error naming the cycle.

## 2. Half-baked and dead surface

Things that parse, print, or exist without doing their job. Before 1.0 each
either gets wired or removed — an accepted-but-dead flag is a tiny lie.

- `--refresh-manifest` — parsed, never read. Pure no-op.
- `--install-completion` — prints "not wired up yet" (`_app.py:209-215`).
  This is also the biggest missing headline feature; see §8.
- `-v`/`--verbose` and `--no-color` — README says "not yet wired," but they
  *are* partially wired (replay-on-success, color suppression). The README is
  wrong in the pessimistic direction, which is novel, but still wrong.
- Per-task `--help` — doesn't exist at all (and worse, see bug #1).
- `manifest.is_stale` + the `sources` block — computed, stored, never
  consulted by any live path. Either the hot path learns to use it or it goes.
- `executor.run_chain` — no callers. Legacy shim.
- `tools` — used in the README, the docs, and footman's own `tasks.py`, yet
  absent from `__all__` and the lazy `__getattr__`. Load-bearing and
  undocumented at the same time.
- `Group` is exported but there's no public way to *run* one; `Context` is
  exported with no documented construction path; `reset()` is a test-suite
  helper living on the package.
- `tools.*` coverage is seven wrappers to duty's dozens — the one ❌ footman's
  own comparison table concedes, still true.

## 3. Release engineering

The pipeline is modern where it counts — trusted publishing, attestations,
uv-native build, a genuinely zero-dependency wheel. And then:

- **Any `v*` tag publishes to PyPI with zero verification.** `release.yml`
  never checks that CI passed on the tagged commit. A broken tag ships.
- **The version lives in two places** (`pyproject.toml:7`,
  `__init__.py:36`), synced by hand, checked by nothing. Nothing asserts tag
  == pyproject == `__version__` == changelog heading. This is the single most
  likely way a release goes wrong.
- **Coverage is reported, never enforced.** The README claims ~95%; CI runs
  `pytest -q` with no `--cov`, and `[tool.coverage.report]` has no
  `fail_under`. The claim is probably true and definitely unguarded.
- **Docs only build strictly *after* merge** (`docs.yml` on push to main). A
  PR that breaks the strict build sails through and fails on main.
- Small stuff: no `Documentation`/`Changelog` URLs in pyproject; the
  changelog links tags `v0.0.1`/`v0.0.2` that don't exist; no sdist excludes
  for `site/`/`docs/htmlcov/` on a dirty checkout; `Development Status :: 3 -
  Alpha` and three separate alpha warnings all need one coordinated flip at
  1.0, together with a written stability promise (decorator surface, CLI
  grammar, `--json` schema, manifest additive-only).

## 4. Test-suite gaps

The suite is broad — every module touched, the grammar and scheduler
genuinely well covered, and the 3 OS × 3 Python matrix is more than most 1.0s
run. What's missing is the hostile-world column:

- **Signals.** Not one test (or line of handling) for Ctrl-C. For a tool
  whose job is running other tools, interrupt behavior *is* product surface.
- **Windows realities.** CI runs Windows, but nothing exercises backslash
  paths through `run("...")` — which is how bug #8 survived.
- **Bytes that aren't UTF-8.** No test feeds a subprocess that emits latin-1.
- **A manifest that is valid JSON but the wrong shape.** Corrupt-JSON → `None`
  is covered; garbage-but-parseable reaching the completion hot path is not.
- **A genuine `SyntaxError` in tasks.py** (the runtime-raise path is tested;
  the parse-failure path is not).
- **Deep cascades.** The monorepo tests stop at two levels; override
  precedence at level four is asserted nowhere.
- **Unicode task names, very long argument lists, `coerce.py` as a unit**
  (it's only tested through its callers).

None of these are exotic. They're the inputs real projects produce on a bad
Tuesday.

## 5. The testing story — tasks are code, so test them like code

Today a user who wants to test their `tasks.py` gets one lucky break and one
wall. The break: `@task` returns the original function untouched
(`registry.py:90-96`), so `lint(fix=True)` in a plain pytest already works.
The wall: any `run()` or `tools.*` call inside that task **really executes**
— the run context is private (`context._current`), and there is no public
dry-run or recording mode. typer ships `typer.testing.CliRunner`; footman
ships nothing.

The design (validated against the internals — footman's own test suite
already does all of this privately, four times, in slightly different ways):

**Three altitudes of testing:**

1. **Plain calls** — already works. Document it; ship nothing.
2. **Recording** — assert *which commands would run* without running them:

    ```python
    from footman.testing import recording
    from tasks import lint

    def test_lint_fix_passes_the_flag():
        with recording() as steps:
            lint(fix=True)
        assert steps[0].command == "ruff check . --fix"
    ```

    Built from two small public pieces: `use_context(ctx)` (a context manager
    over the currently-private contextvar) and a dry-run branch that records
    `StepResult`s and honors `quiet` — i.e. `dry_run + quiet` *is* silent
    capture. No new mode, no monkeypatching `subprocess`.

3. **CLI-level** — drive argv → exit code → output → results, in-process:

    ```python
    result = runner.invoke("--dry-run release 1.2.0 --push")
    assert result.ok
    assert result.results[0].task == "release"
    ```

    `Runner.invoke(args, *, tasks=Path|Group|None, cwd=None) -> Result(
    exit_code, stdout, stderr, results)`. Cache isolation via
    `XDG_CACHE_HOME` (already honored), results exposed by a four-line
    `collect=` keyword on `_app.run`. Deliberately *not* named `CliRunner` —
    footman has no click lineage to imply.

**A pytest plugin, in-tree.** `[project.entry-points.pytest11]` costs zero
runtime dependencies — only pytest ever imports the module. Three fixtures,
each a thin shim over `footman.testing`: `fm` (a Runner for the current
project), `fm_project(source)` (scaffold an isolated tmp project from a
tasks-file string — footman's own `project` fixture, productized), and
`fm_record` (a recording context for the whole test). Then footman's own
suite migrates onto them — the framework dogfooding its testing story is the
best test *of* the testing story.

**Golden surfaces.** `--json` becomes the blessed machine surface for 1.0:
documented schema, additive-only promise. Worth wrapping as
`{"schema": 1, "results": [...]}` in 0.5.0 while breaking is still free.
`--dry-run` output stays human-oriented, no cross-version promise.

Plus a `docs/testing.md` page walking the three altitudes, fixtures, golden
tests, and how to test a branded `App`.

## 6. The composition story — assembling the task surface dynamically

Nothing exists here today: no conditional registration, no way to adopt a
task from another package, no plugin discovery. The design stance that makes
all three fall out of one idea: **a task tree is a value** (`Group`), and
tasks files *assemble* trees. Everything resolves at import/manifest-build
time, conditions re-check live at execution, and completion keeps serving the
cached manifest — the same contract `suggest()` already set.

### Hiding vs disabling

Two different intents, two mechanisms:

- **Hidden** — not in the tree, the listing, or completion. This is plain
  Python, because tasks.py is executed code, and it already works:

    ```python
    if sys.platform == "darwin":
        @task
        def notarize(app: Path): ...
    ```

    No kwarg will be added for what an `if` statement does better.

- **Disabled but listed** — pytest-skip semantics, for "this task exists but
  can't run here":

    ```python
    @task(when=lambda: shutil.which("docker"), reason="requires docker on PATH")
    def up(detach: bool = True):
        "Start the dev containers."
    ```

    The listing shows `up … (unavailable: requires docker on PATH)`; the name
    still completes (manifest stays stable); running it re-evaluates the
    predicate *live* — never trusting the cached answer — and refuses with
    the reason, exit 2. A `pre`/`post` dependency on a disabled task is a
    hard failure, not a silent skip: silently dropping `lint` from `check` on
    the wrong machine is how CI learns to lie.

### Adopting tasks from another package

```python
from footman import include, group

include("shared_tasks")                        # graft all of it at root
include("shared_tasks", only=["lint", "fmt"])  # cherry-pick
docs = group("docs")
include("mkdocs_helpers.tasks", into=docs)     # namespace under `fm docs …`
```

`include()` imports the provider inside a registry capture (so its decorators
can't pollute the current tree), grafts the captured tree where you say, and
is **loud on collisions** by default (`override=True` to intend the
shadowing). Included tasks run from the *includer's* directory — a shared
lint task lints *this* project. A bare `from otherpkg.tasks import build`
remains a documented footgun (import-order- and cache-sensitive);
`task(name="fmt")(shared.fmt)` is the blessed single-task re-export and
already works today.

### Packages advertising tasks

A package publishes a module-level `Group` under the `footman.tasks` entry
point:

```toml
# the plugin's pyproject.toml
[project.entry-points."footman.tasks"]
mkdocs = "footman_mkdocs:tasks"
```

And a project **opts in** — plugins are never auto-loaded, because
`pip install` silently growing your command surface is a supply-chain
surprise nobody asked for:

```toml
[tool.footman]
plugins = ["mkdocs"]        # mounts as `fm mkdocs build`, `fm mkdocs deploy`
```

or, composing with `include()` for filtering and re-mounting:

```python
include(plugin("mkdocs"), only=["build"])
```

A configured-but-missing plugin is a crisp exit-2 error naming the installed
entry points. The `importlib.metadata` scan is stdlib (zero-dep holds), paid
only on the execution path and only when `plugins` is configured; the
completion hot path never changes. User names shadow plugin groups silently —
consistent with the cascade. One rule of thumb ties it together: *config
mounts a tool; tasks.py adopts a task.*

## 7. Typing parity with typer

Better news than expected: enums, datetime/date, uuid, `Decimal`, and any
str-constructible class already coerce; unions, `Many[T]`, `dict[K, V]`, and
the `Annotated` markers are ahead of typer in places. The honest gap list,
with verdicts:

| Gap | Verdict |
| --- | ------- |
| `bool` in collections (bugs #3/#4) | **fix now** — silent wrong answers |
| Path validation (`exists` / `isfile` / `isdir`) | **must-have** |
| Numeric bounds (`between(1, 32)`, bare `range` sugar) | **must-have** |
| Env-var fallback (`env("DEPLOY_ENV")`) | **must-have** — the CI story |
| Per-param validator (`check(fn)`) | **must-have** — the escape hatch |
| Silent `str` degrade of unknown annotations | **must-have** (a warning) |
| Hidden params | post-1.0 |
| `tuple[X, Y]` fixed arity | post-1.0, comma-form only (`--size 800,600`) |
| Prompts / confirmation | **never** |
| Counting flags (`-vvv`) | **never** |
| Short aliases for task params | **never** |

The must-haves all follow the existing `suggest`/`nosplit` idiom — `Annotated`
markers, recognized in one loop in `coerce.peel`, additive manifest keys, no
schema bump:

```python
@task
def deploy(
    config: Annotated[Path, isfile],
    jobs: Annotated[int, between(1, 32)] = 4,
    target: Annotated[str, env("DEPLOY_ENV")] = "staging",
    version: Annotated[str, check(semver)] = "0.0.0",
): ...
```

```text
$ fm deploy missing.toml
fm: deploy: <config> must be an existing file (got 'missing.toml')
$ fm deploy app.toml --jobs 99
fm: deploy: --jobs must be between 1 and 32 (got 99)
```

Path and bounds checks run eagerly in the splitter (taught errors, like
choices today); `env()` and `check()` run at binding, and an env-supplied
value flows through the same coercion, bounds, and checks as a CLI token.
`env()` on a parameter without a default is a build-time taught error — an
env fallback *makes* a parameter optional, so it needs somewhere to fall.

The nevers deserve their one line each. Prompts: a chained, parallel,
CI-first runner is the most hostile environment interactivity has ever met —
a task body can call `input()` if it truly must. Counting flags: verbosity
belongs to the runner (`-v` exists), not to task params, and task params have
no short-flag grammar to hang repetition on. Short aliases: collision-prone
across cascade merges, and they steal negative-number positionals. Saying
"never" to these is what keeps the grammar deterministic — the thing that
makes separator-free chaining possible at all.

## 8. Completion and CLI polish

- **`--install-completion` for bash/zsh/fish** (pwsh/nushell after) — the
  README promises it, the resolver exists, and it's the single biggest gap
  between footman's completion story and the one users actually experience.
  Relatedly: the headline "~19 ms" is the *standalone resolver* number — the
  path users get today (`fm --complete`) is 23–24 ms. Either ship the
  standalone path with the installers, or say 23. Verified-not-vibes cuts
  both ways.
- **Per-task `--help`** — falls out of fixing bug #1 properly.
- **Chain-aware completion** — the walk currently stops at the first task.
- Wire-or-delete: `--refresh-manifest`, `manifest.is_stale`,
  `executor.run_chain`.
- Public-surface hygiene: `tools` into `__all__`; `reset()` out of the
  public namespace; decide the `Group`/`Context` construction story.
- Grow `tools.*` — duty's one conceded win in the comparison table.

## 9. Docs

- **The README is a 457-line hand-maintained near-superset of the docs site**
  — the global-options table is byte-identical to `reference.md`. It *will*
  drift. Either the README becomes a short pitch with pointers, or the shared
  tables become snippet-includes (`pymdownx.snippets` already proves the
  pattern with the changelog).
- **New pages:** `testing.md` (§5), `composing.md` (§6), a cookbook of
  recipes, a CI-integration page (the `--json`-for-agents story is currently
  scattered), extending `tools.*`, and a troubleshooting page that catalogs
  the taught errors — footman markets its error messages; show them off.
- **Honesty fixes:** the 19 ms headline (above); the `import footman` +4 ms
  vs `import typer` +24 ms claim has no committed script behind it — write
  the benchmark or soften the claim; `docs/comparison.md` says to reproduce
  with `uv run python comparison/bench_compare.py`, which fails without
  `--group comparison`.
- **Voice pass:** `comparison.md` is the calibration target — first person,
  generous before clever, tables followed by the prose that reads them,
  tradeoffs conceded out loud. `typing.md`, `monorepos.md`, `tools.md`, and
  `orchestration.md` are currently in neutral documentation voice and read
  like a different author.

## 10. The release train

Four releases, each one theme, nothing riding on a mega-drop:

| Release | Contents | The gate |
| ------- | -------- | -------- |
| **0.5.0** | All §1 bug fixes (with regression tests) + §3 release engineering + the `--json` envelope | A tag can no longer publish unverified; versions can no longer disagree |
| **0.6.0** | The testing story (§5) + typing must-haves (§7) | footman's own suite runs on `footman.testing`; the typer gap table is all ✅ or "never" |
| **0.7.0** | The composition story (§6) + completion installers (§8) | `include()`, `when=`, `plugins=` documented and dogfooded; TAB works out of the box in bash/zsh/fish |
| **1.0.0** | Docs (§9) + stability promise + alpha-flag flip | Everything above has baked for a full cycle; a TestPyPI dry-run precedes the real tag |

## 11. After 1.0 — the premier-task-runner backlog

Not gating anything, in rough order of how much I want them:

- **Watch mode** — `fm --watch lint`: re-run on file change, debounced.
- **JSONL event streaming** — `--json` today is a summary; agents and CI
  dashboards want per-event lines as tasks start/finish.
- **Task-customizable `--json` payloads** — let a task contribute structured
  data to its own entry in the envelope (a returned dict, or something like
  `ctx.json["artifact"] = path`). Deliberately not designed yet: needs
  thinking about schema stability (the envelope promises additive-only),
  reserved keys, and how it composes with steps. Parked so it isn't lost.
- **Fingerprint-based skipping** — "inputs unchanged, skip the task"
  (doit/turborepo territory; big, and the DAG is already in place).
- **Per-task timeout and retry** — `@task(timeout=120, retries=2)`.
- **A TTY progress UI for the DAG** — parallel runs deserve better than
  buffered silence, and the non-interleaving contract already exists.
- `fm --plugins` — list installed `footman.tasks` entry points with
  dist/version and enabled state.
- `fm new` — scaffold a tasks.py that demonstrates the good idioms.
- PowerShell/nushell completion, once bash/zsh/fish are real.

---

*Generated from a full source audit at v0.4.0 (commit 9328109). File:line
references are to that revision.*
