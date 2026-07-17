# Changelog

All notable changes to footman are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/). While footman is pre-1.0, minor
versions may include breaking changes.

## [Unreleased]

### Changed

- **In-process tools import only when they actually execute.** Resolving a
  tool's `[console_scripts]` entry point is now pure metadata; the `.load()`
  that imports the tool's module is deferred into the callable footman runs.
  So a `--dry-run`, a `recording()` test, or a branch you never take costs
  zero tool imports — the property that made duty's lazy design nice, now
  without the build-vs-run split (a call is still always a call). One
  behaviour change: a console-scripts entry that exists but fails to import
  now surfaces as a task failure with the real error, instead of silently
  falling back to a subprocess.

### Added

- **`off` — disable a flag a tool turns on by default.** `False`/`None`
  mean *omit* (so a task parameter's default flows through), which left no
  way to spell a negation. `strict=off` → `--no-strict` fills the gap and
  completes the boolean story (`True` → `--flag`, `off` → `--no-flag`);
  it's the same as naming the negation directly (`no_strict=True`) but
  reads as intent and lets a variable drive it
  (`directory_urls=pretty or off`). Typed in the stubs, so it autocompletes
  and a garbage value is still a type error.
- Filled a real gap in the `ruff.check` stub — `exit_zero`,
  `exit_non_zero_on_fix`, `quiet`, `silent`, `verbose`, `isolated`,
  `cache_dir` now autocomplete, so you're guided to the right flag instead
  of guessing a name like `exit=` that `**flags: Any` silently accepts and
  a `False` value quietly omits. Docs now spell out that escape hatch: an
  unknown flag either errors at the tool (truthy) or is dropped (`False`/
  `None`), and a literal `"--flag"` positional always sidesteps it.
- **Tool autocompletion via stubs — zero runtime cost.** `tools.pyi` gives
  IDEs and type checkers typed verbs and common flags for the curated
  tools (`tools.ruff.check(` completes `fix=`, `select=`, …; `fix="yes"`
  is a type error), while the runtime bridge stays a few mechanical lines
  the stub never touches. Every stubbed verb ends in `**flags: Any` and
  unknown verbs fall through to `Tool`, so the stub can suggest but never
  forbid — drift degrades a hint, not a run. `None` is typed as the omit
  sentinel everywhere, matching the translation rules.

- **The tools bridge runs Python tools in-process.** `Tool(...,
  in_process=True)` (or `in_process=True` per call) resolves the tool's own
  `[console_scripts]` entry point and calls it with `sys.argv` patched —
  the no-transcription contract, minus the interpreter spawn. `mkdocs`,
  `zensical`, and `coverage` default to it. Beyond speed this is a
  correctness fix on macOS, where SIP strips `DYLD_*` from child processes:
  a tool needing Homebrew's native libraries (mkdocs + cairo) only works
  in-process. Preferences fall back to a subprocess when no entry point
  exists; per-call demands error with a taught message. And parallelism
  survives: capture routes through the per-task stdout router
  (thread-confined — also fixing a pre-existing race where the global
  redirect could cross-contaminate concurrent in-process captures), and
  argument-accepting entries (click commands, `main(argv=None)` — nearly
  all of them) are called directly. Only a legacy zero-arg `main()` gets
  the `sys.argv`-patching fallback, and only those serialise.

## [0.8.0] — 2026-07-17

### Added

- **PowerShell completion installer.** `fm --install-completion pwsh` (alias:
  `powershell`) writes a `Register-ArgumentCompleter` hook and dot-sources it
  from the profile PowerShell itself reports (`$PROFILE`), for PowerShell 7+
  and Windows PowerShell alike. Idempotent, branded, and covered by a
  functional test that drives PowerShell's own completion engine on every CI
  platform.
- **nushell completion installer.** `fm --install-completion nushell` (alias:
  `nu`) writes an external-completer hook sourced from the config nushell
  itself reports (`$nu.config-path`). The hook *wraps* any existing external
  completer (carapace, …) — it answers for `fm` and passes every other
  command through. Verified against a real nushell. Every shell footman
  promised is now installed with one command.
- **`tools.*` became a bridge, not a transcription.** Every executable on
  PATH is a tool with no declaration (`tools.terraform("plan")`), attribute
  access chains subcommands (`tools.docker.compose.up(detach=True)`), and
  keyword arguments translate mechanically (`fix=True` → `--fix`, lists
  repeat, single letters go short, trailing `_` escapes keywords). This is
  a deliberate answer to the drift in hand-transcribed wrappers — duty's
  `ruff.check(show_source=True)` emits a flag modern ruff rejects; a bridge
  has nothing to go stale. `tool.installed_version()` (cached, resolved
  outside the task context) covers the rare version-dependent branch.
  Curated spellings for ruff, uv, git, docker, bun, mkdocs, zensical,
  coverage, cspell, prek, markdownlint (-cli2), basedpyright; pytest keeps
  its in-process path. A tools *plugin* mechanism was considered and
  rejected: tools are plain objects, so publishing them is publishing
  Python — an import already beats an entry point.
- **A live progress line for parallel runs.** On a TTY, the scheduler keeps
  one status line (`/ 2/5 (1 failed)  running: lint, test`) between the
  finished tasks' output blocks. Event-driven (no timer thread), always
  cleared before a block lands so output stays non-interleaved, red only
  when something failed, plain under `NO_COLOR`/`--no-color`, and absent
  entirely under `--quiet`, `--json`, or a pipe. The last item on the
  README's original roadmap besides `tools.*` growth.
- **Bare `--install-completion` detects your shell.** No argument needed:
  footman walks the parent-process tree (the way typer's `shellingham`
  dependency does — without the dependency, and correctly skipping over
  `uv run`), with the `PSModulePath` tell on Windows and `$SHELL` as the
  last resort. Undetectable → a taught error naming the five options.
  Verified through a real shell with `$SHELL` deliberately lying.

### Docs

- **The README is a front door now** — what footman is, why it exists, one
  taste, and pointers into the site — instead of a 460-line hand-maintained
  copy of the documentation that drifted on every change.
- Two new pages: **CI & automation** (the `--json` envelope contract, exit
  codes, keep-going/sequential in CI, agents) and **Troubleshooting** — a
  catalogue of every taught error, generated against real output, with the
  standing invitation that a raw traceback is a footman bug.

### CI

- **Every completion hook is now functionally tested against its real
  shell.** New tests drive bash (`COMP_WORDS`/`COMPREPLY`), zsh (the hook's
  exact expansion idiom), and fish (its own `complete -C` engine) alongside
  the existing pwsh and nushell tests — and a dedicated `shells` CI job
  installs zsh, fish, and a pinned nushell so none of them can skip
  silently. The bash 3.2 slice bug taught us: a hook that hasn't met its
  shell isn't tested.

### Fixed

- The pwsh installer now writes its hook into **every** PowerShell profile
  present — PowerShell 7 and Windows PowerShell keep *different* `$PROFILE`
  files, so on a machine with both, completion previously landed in only
  one of them (and not necessarily the one the user asked for). The hook
  runs on both shells unchanged (`Register-ArgumentCompleter` exists since
  PS 5.0), so whichever PowerShell opens, TAB works.
- Completion no longer re-offers an option the segment already has —
  `fm lint --fix <TAB>` suggests what can still bind, not `--fix` again.
  Repeatable (`list`/`dict`) options rightly stay on offer, and a fresh
  segment starts with a clean slate.

## [0.7.0] — 2026-07-17

### Removed

- `--refresh-manifest` — it was parsed and never read; the manifest already
  rebuilds on every execution-path run, so the flag had no job to do.
- `manifest.is_stale` and the manifest's `sources` block — scaffolding for a
  staleness check no live path ever consulted.
- `reset()` is no longer re-exported from the package root (it remains in
  `footman.registry` for test suites); it was a test-suite helper living on
  the public namespace.

### Changed

- `footman.tools` is now a real public export (`__all__`, lazy) — it was
  load-bearing in the docs and footman's own tasks file while officially not
  existing.
- The `import footman` vs `import typer` cost claim is now backed by a
  committed script (`scripts/bench_import.py`), and the comparison page's
  repro commands include the required `--group comparison`.

### Added

- **Shell completion installers.** `fm --install-completion bash|zsh|fish`
  writes the hook and (bash/zsh) one guarded `source` line into your rc
  file; fish needs no rc edit at all. Idempotent, branded (`acme
  --install-completion zsh` installs for `acme`), and the generated hook
  stays on the cached stdlib-only fast path. The bash hook survives macOS's
  bash 3.2 (whose quoted array slices collapse to a single word — found the
  hard way, tested for keeps).
- **Chain-aware completion.** The resolver now walks segments the way the
  splitter does — exact positional arity, then a trailing `Many`/variadic
  consumer, then the next word starts a new segment — so
  `fm format lint --fi<TAB>` completes *lint's* options, a satisfied task
  offers the next task names, `+` resets, and after `--` nothing is offered
  (it's the passthrough's). Latency is unchanged: same one-file-read walk.
- **Composable task surfaces.** Three mechanisms, one contract (resolve at
  import time, re-check availability live): `@task(when=…, reason=…)`
  disables-but-lists a task that can't run here (pytest-skip semantics —
  shown in `--list`/`--help`, refuses to run with the reason, a `pre`/`post`
  dependency on it is a hard failure); `include(source, into=…, only=…,
  exclude=…, override=…)` grafts another module's tasks into your tree
  (loud on collisions and typos, provider imported under a registry capture
  so nothing leaks, adopted tasks run from *your* directory); and packages
  advertise a `Group` under the `footman.tasks` entry point that projects
  opt into via `[tool.footman] plugins = ["name"]` — never auto-loaded,
  user names shadow plugin groups, missing plugins are crisp errors naming
  what *is* installed. New docs page: *Composing tasks*.
- `registry.capture()` — the public seam for importing task-defining modules
  without touching the live registry.

## [0.6.0] — 2026-07-17

### Added

- **A first-party testing story.** `footman.testing` ships `Runner.invoke`
  (drive a full command line in-process: exit code, stdout/stderr, structured
  `TaskResult`s, isolated completion cache), `recording()` (capture the
  commands a block *would* run, silently, without executing), and re-exports
  the new public `use_context()`. Three pytest fixtures — `fm`,
  `fm_project`, `fm_record` — auto-load via a `pytest11` entry point; pytest
  is still not a dependency (only pytest itself imports the module).
  footman's own suite dogfoods them. New docs page: *Testing your tasks*.
- **Validation markers**, all in the `Annotated` idiom: `exists` / `isfile` /
  `isdir` path requirements and `between(lo, hi)` numeric bounds (a bare
  `range` works for ints), both validated eagerly with taught errors;
  `env("VAR")` fallbacks (CLI > env > default, the env value flowing through
  the same coercion/bounds/checks as a CLI token); and `check(fn)` custom
  validators, run post-coercion, per element for collections. `env()` on a
  parameter without a default (or on a dict) is a taught build-time error.
- **Opaque annotations warn.** A parameter whose annotation resolves to
  nothing footman can coerce (an unresolved name, a value) now emits a
  `UserWarning` instead of silently treating every value as text.

### Docs

- Fixed the dynamic-completion examples: the documented `suggest[str, fn]`
  syntax never existed — the real form is `Annotated[str, suggest(fn)]`.

## [0.5.0] — 2026-07-17

### Added

- **A real help story.** `fm --help` documents the runner itself (usage
  grammar plus the full global-options table, generated from the same table
  the parser reads). `fm --help <group>` shows a group's tasks, and
  `fm --help <task>` renders per-task usage, docstring, and typed
  positional/option tables from the manifest. `-h`/`--help` anywhere before
  `--` turns the whole line into a read-only help request — `fm deploy --help`
  can never execute `deploy`.
- **`bool` is now a real token type.** `dict[str, bool]` values and
  `list[bool]` elements parse `true/false/1/0/yes/no/on/off` (eagerly
  validated with a taught error) instead of collapsing to a flag or silently
  reading every value as `True`.
- **Dependency-cycle detection.** A cyclic `pre`/`post` graph is a taught
  error naming the cycle; previously it ran nothing and exited 0.
- **`py.typed` marker** — downstream type checkers now see footman's inline
  types (the `Typing :: Typed` classifier was already claiming they could).
- **Ctrl-C is handled**: pending tasks are cancelled, the run reports
  `interrupted`, and the exit code is 130 — no more raw traceback.

### Changed

- **Comma-splitting is now the default for collections.** A `list` / `dict`
  parameter splits a single token on commas (`--tag a,b,c` → `["a", "b", "c"]`)
  out of the box, in addition to the repeatable form (`--tag a --tag b`). The
  old opt-*in* `csv` marker is replaced by an opt-*out* `nosplit` marker, for
  the parameters whose values may themselves contain a comma.
- **`--json` output is now enveloped**: `{"schema": 1, "results": [...]}`
  instead of a bare list, so post-1.0 additions never break consumers. This is
  the blessed machine surface; future changes will be additive.
- **Errors name their culprit.** A failing tasks-file import names the file; a
  duplicate task name is reported as the user error it is (not "failed to
  import"); a malformed discovered config TOML warns and is skipped; a
  malformed `--config` file is a hard error; a *strict* `suggest()` completer
  that raises now fails the run (it used to silently disable the validation it
  promised).
- Dry-run now records `StepResult`s (and honours `quiet`), so tests can assert
  which commands *would* run without executing anything.

### Fixed

- `fm --help <task>` used to **execute the task**.
- `run("...")` string commands are no longer `shlex`-split on Windows —
  backslash paths survive; the string goes to `CreateProcess` whole.
- Non-UTF-8 subprocess output no longer crashes `run()` (decoded with
  `errors="replace"`).
- Digit-lookalike tokens (`"²"`) are taught type errors instead of an
  `int()` traceback.
- An exception escaping a worker thread in a parallel run (including a
  `KeyboardInterrupt` raised inside a task) now propagates instead of being
  silently dropped and reading as success.

### Docs

- Docstrings converted from reStructuredText to Markdown (renders natively via
  mkdocstrings).

### CI

- Releases are gated: `release.yml` now runs the full CI suite on the tagged
  commit and refuses to publish unless the tag, `pyproject.toml`,
  `__version__`, and the changelog all agree on the version (and the wheel
  ships `py.typed`).
- Coverage is enforced (`fail_under = 92`), and the strict docs build runs on
  every PR instead of only after merge.

## [0.4.0] — 2026-07-16

### Added

- **Custom-branded CLIs.** A public `App(name, prog, version)` carries your
  project's names and version and threads them through every user-facing string
  (the `--version` banner, the `prog:` error prefix, the completion hint) — so
  you can ship an internal tool under its own name while it stays footman
  underneath. footman's own `fm`/`footman` are now just the default-branded
  `App()`.
- **API reference** on the docs site, generated from docstrings via
  [mkdocstrings](https://mkdocstrings.github.io/).
- **Coverage report** embedded directly in the docs via an inline `<iframe>`,
  regenerated on every deploy.

## [0.3.0] — 2026-07-16

### Added

- **Monorepo task cascade.** Every `tasks.py` from the repo root (the nearest
  `.git`) down to the current directory is merged into one command set: new
  names append, collisions are overridden nearest-wins, and groups merge. Each
  task runs from the folder that defined it.
- **Config discovery.** `[tool.footman]` in `pyproject.toml` and a standalone
  `footman.toml`, walked up to the repo root (nearest wins), plus a
  `--config PATH` override.
- **Per-directory completion cache**, so each folder of a monorepo caches its
  own merged cascade.
- **Documentation site** (Zensical) published to GitHub Pages.

## [0.2.0] — 2026-07-16

### Added

- **Richer type system:** union parameters (validated and coerced by
  specificity), `Many[T]` one-or-many values, opt-in `csv` comma-splitting,
  `dict[K, V]` (including `dict[str, list[...]]`), and custom types via their
  typed constructors.
- **Execution layer:** `run()` (subprocess or in-process callable, capture with
  replay-on-failure, dry-run, `--json` steps), the typed `tools.*` wrappers, and
  opt-in `Context` injection.
- **Parallel-by-default DAG scheduler:** independent tasks run concurrently;
  `pre`/`post` dependencies, the `parallel()` helper, `-s/--sequential`, and
  grouped non-interleaved output.

## [0.1.0] — 2026-07-16

### Added

- Initial release: typed function signatures become CLIs (flags, options,
  positionals, choices), modules become nested command groups, a separator-free
  chain grammar, and instant shell completion answered from a cached JSON
  manifest without importing your code.

## 0.0.2 — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI (MIT license, project
  URLs). Not tagged in git.

## 0.0.1 — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI. Not tagged in git.

[Unreleased]: https://github.com/willemkokke/footman/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/willemkokke/footman/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/willemkokke/footman/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/willemkokke/footman/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/willemkokke/footman/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/willemkokke/footman/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/willemkokke/footman/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/willemkokke/footman/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/willemkokke/footman/releases/tag/v0.1.0
