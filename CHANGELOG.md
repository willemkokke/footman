# Changelog

All notable changes to footman are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/). While footman is pre-1.0, minor
versions may include breaking changes.

## [Unreleased]

### Added

- **Tri-state failure policy and true fail-fast.** Keep-going is now three-state:
  an explicit command-line choice wins, otherwise a task can declare its own
  (`@task(keep_going=True/False)`), otherwise the built-in fail-fast — so
  "unspecified" means *the code decides*, not a silent default. The new
  `--fail-fast` global forces fail-fast when a task declares keep-going, the
  mirror of `--keep-going`. And fail-fast now actually *is* fast: on the first
  failure it stops launching new work **and terminates the subprocess trees
  still running** — each child *and its own children*, so a tool's workers
  (pytest-xdist, `make -j`, a script's background jobs) die with it instead of
  orphaning — escalating SIGTERM to SIGKILL for anything that ignores the first
  signal. A task cut off this way reports as *cancelled*, kept distinct from a
  genuine failure; the run's exit code follows the real failure. `Ctrl-C` reaps
  in-flight trees the same way. `@task(atomic=True)` opts a task's subprocesses
  out of the kill — they run to completion, so a mid-write can't be truncated —
  and an `interactive` task's child stays attached to the terminal it owns.
  In-process runs are never killed (there's no child to signal).
- **Parameter forwarding.** A parameter marked `Annotated[T, forward]` (or the
  shorthand `Forward[T]`, like `Many[T]`) passes its value to every task this
  one dispatches — its `pre`/`post` prerequisites and a runnable group's
  surfaces — that declares a parameter of the same name; the rest run on their
  own defaults. So `@task(pre=[format, lint]) def check(fix: Forward[bool])`
  reaches `--fix` into the tasks that support it and lets the ones that don't
  just run, and the value chains through a callee that re-declares the marker.
  Precedence is CLI > forwarded > default, and a forwarded value overrides a
  default without rescuing a required parameter (a prerequisite stays runnable
  on its own). Two dispatchers sending different values to a shared
  prerequisite is a taught error, not a silent last-wins. `NoSplit[T]`,
  `Exists`, `IsFile`, and `IsDir` join `Many`/`Forward` as terse aliases for
  the bare markers.
- **Runnable groups.** A group gains a default action with `@group.default` —
  a typed function whose signature is the group's own options — so `fm lint`
  runs it while `fm lint markdown` still runs one surface. An empty-body default
  fans out the group's own tasks (`fm lint --fix` fixes what's fixable and lints
  the rest); a custom body is the escape hatch. A positional parameter on a
  default is a load-time error, because a bare word after a group names a child.
  The group tab-completes and self-documents like a first-class command.
- **Discovery hooks (`@finalize`).** A function decorated `@footman.finalize`
  runs once on the fully-merged task tree, after the whole `tasks.py` cascade is
  assembled but before dispatch — footman's `pytest_collection_modifyitems`. Use
  it to edit the tree in bulk: add a `pre` to every task whose name matches a
  pattern, switch a set of tasks off by policy, and so on. Because it runs at
  discovery the edits are part of the plan — an added `pre` runs and shows in
  `--dry-run`, a disabled task drops from listings — not a runtime surprise. The
  hook is handed a `Tasks` view of the tree; iterate it or index it by
  command-line name for a `TaskView` that reads (`pre`, `post`, `disabled`) and
  edits (`add_pre`, `add_post`, `disable`) each task through a defined interface,
  never footman's private attributes. Hooks run in cascade order — root's first,
  the folder nearest your cwd last, each seeing the previous edits.

## [0.17.0] — 2026-07-22

### Added

- **`check()` validators can read the other inputs.** A `check` callable that
  declares a second parameter receives the parameters to its left at their
  effective values (a provided value, else the default), read-only — so a version
  can be validated against the current release of the package named in an earlier
  argument, or an end-date against a start-date, without hardcoding a bound that
  drifts out of sync with the signature.
- **Interactive input, typed and CI-safe.** A parameter marked
  `Annotated[T, ask()]` prompts for its value when the CLI and env don't
  supply it, coercing the answer through the same pipeline as a flag — a
  `Literal` is a typed choice, a bad value re-asks — with precedence
  CLI > env > default > prompt. Off a terminal, under `--no-input`, or in
  `--json` it errors naming the flag rather than hanging. `prompt()`,
  `confirm()`, and `select()` are public primitives for asking mid-run, but
  **guarded**: called inside an ordinary task they raise a taught error,
  because the prompt would be swallowed by the capture buffer or race a
  parallel sibling — a task that genuinely owns the terminal declares
  `@task(interactive=True)` (it runs sequentially, uncaptured, with sole
  stdio). New globals: `--yes` (auto-answer confirms) and `--no-input`
  (never prompt).
- **Dynamic completions are recomputed fresh at <kbd>Tab</kbd>, not served
  stale.** A `suggest(fn)` completer queries live state (git branches, release
  candidates, deploy targets), so footman now runs it fresh in a bounded,
  isolated subprocess when you complete its value — rather than serving the
  snapshot baked into the manifest, which is exactly wrong for a build-critical
  answer. A slow or failing completer degrades to no candidates, never the old
  values; task names, options, and `Literal` choices still answer instantly from
  the cache.
- **The first <kbd>Tab</kbd> in a fresh directory builds the manifest instead of
  answering empty.** A cold completion cache used to stay blank until your first
  real `fm` run; now the first <kbd>Tab</kbd> builds it once (bounded, and out of
  the import-free hot path) and answers accurately. A slow `tasks.py` degrades to
  empty with the build finishing in the background, so the next <kbd>Tab</kbd> is
  warm — never a hung keystroke.
- **<kbd>Tab</kbd> completes file paths for path-valued arguments.** The
  path-valued globals (`-f`/`--tasks-file`, `-C`/`--directory`, `--config`) and
  any task option annotated `Path` now hand off to your shell's own file
  completion — `_files` in zsh, readline's filename completion in bash, and the
  fish/pwsh/nushell equivalents. A plain `str`/`int` value still completes
  nothing, so files are offered only where a path is actually wanted.
- **`fm -f <file> <TAB>` completes that file's tasks.** A one-file run reads
  its own tasks, so its completion now does too — cached under a key pairing the
  file with the cwd, separate from (and never overwriting) the plain-cwd cache.
  `-f` and `--config` are documented as orthogonal: each disables only its own
  cascade.
- **`fm footman tools provision`** — fetch the latest of every curated tool
  into one throwaway prefix, without polluting the machine. Almost every tool
  ships an installable PyPI wheel (the Rust and C++ ones included), so
  `uv tool install` into an isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` covers
  most of them; bun comes from its own GitHub release (first, since the node
  tier runs through it), the node CLIs via `bun add`, and the Go CLIs (gh,
  eclint) from a release asset matched off the release's own asset list.
  `--sync` then rewrites the stubs against the prefix; `--clean` deletes it,
  and deleting the prefix is the whole undo.
- **Ten more curated tools, with generated stubs:** `gh`, `eclint`, `mypy`,
  `ty`, `twine`, `git-changelog`, `git-cliff`, `build`, `cmake`, `ninja`.
- **A sixth help dialect — Go's stdlib `flag`** — single-dash long options
  (`-color`) under `Usage of <prog>:` with descriptions on the next line, so
  a Go tool like `eclint` reads as fully as a clap or cobra one.

### Changed

- **python, pytest, and the shells are first-class tools.** `tools.python`
  and `tools.pytest` are `Tool` instances with generated stubs, not bespoke
  functions — `Tool` gained `path=` (so `tools.python` targets
  `sys.executable`) and `entry=` (so `tools.pytest` runs the arg-accepting
  `pytest.main` and stays parallel). The shells footman completes for —
  `tools.bash`/`zsh`/`fish`/`pwsh`/`nu` — run a command string through a real
  shell (`bash -c "…"`). `sh` is removed: it never used a shell, so `run("…")`
  is the honest spelling. A per-tool short-option policy (`none`/`only`/`all`,
  default `only`) controls whether a stub keys on a short flag, so python's
  `-m`/`-c` are complete without cluttering other tools.
- **footman's first-party tasks are now two plugins, `footman.docs` and
  `footman.tools`**, each opt-in on its own — a project can mount the
  end-user-facing doc generator without the maintainer-facing stub toolkit.
  A plugin's name is its command path, so a dotted name nests one group per
  segment (`["footman.docs"]` → `fm footman docs …`), and plugins that share
  a prefix meet under one namespace group without either owning it.

### Fixed

- **The tools reference sidebar is generated from the drivers, not
  hand-maintained** — `fm footman tools pages` regenerates the docs nav
  (alphabetically, between markers) and a test fails when a tool is added
  without it, so the stale "13 tools" sidebar can't recur.
- **The `--help` parser no longer swallows a flag's trailing punctuation** —
  clap's repeatable `--verbose...` and a manual's `--merge.` ending a sentence
  had become the keywords `verbose___` / `merge_`; a dot is now read only
  inside a name.
- **Bare lowercase value placeholders are read** (gh's `--assignee login`,
  docker's `--memory bytes`) from `--help`, while a man page's prose reference
  (`the --patch option.`) is left as the switch it is.
- **Bulleted option lists are read** — markdownlint-cli2 prints its options as
  `- --fix  …`, and the leading bullet no longer hides the flag.
- **A backslash in a tool's help** (mypy's `--exclude '\.py$'`) is escaped in
  the generated docstring instead of becoming an invalid escape sequence.

## [0.16.0] — 2026-07-21

### Added

- **The command line footman shows is now separate from the one it runs.**
  `run()` renders a normalised, syntax-highlighted invocation — options in
  their readable separated form, values shell-quoted, coloured by role the
  way `--help` colours a usage line — while execution takes whatever
  spelling the tool needs. `StepResult` carries both: `.command` (what
  `recording()` asserts and the terminal shows) and `.raw` (the exact
  executed bytes, what `--verbose` prints). One translation feeds both, so
  they can never disagree about what a call means.

- **The `tools.*` stubs are generated from the installed tools.** The
  bridge never went stale, because it transcribes nothing — but its stub
  could, because a stub describes a tool at a version. Now it is read
  from the tool: one file per tool under `footman/_stubs/`, carrying each
  flag's own help text as a docstring, the values it accepts as a
  `Literal`, and the one fact a bridge can never infer — how that tool
  spells "off" (`clean=off` → `mkdocs build --dirty`). Five help dialects
  are understood: click and argparse structurally, plus clap, cobra,
  commander and git's own `--[no-]flag` notation read from `--help`.
- **`fm footman tools …`** — `list` (what is curated and installed),
  `spec` (what a tool says about itself right now), `sync` (rewrite the
  stubs) and `audit` (fail when a stub and its tool disagree). Tools that
  are not installed are skipped *and named*, so a check can't quietly
  cover three of thirteen.
- **git's stubs are read from its manual, not its terse `-h`.** `git
  commit -h` lists 19 flags; the manual lists 37, and git is exactly the
  tool where autocomplete earns its keep. footman now reads `git help
  <verb>` for each git verb — twice the options, each with its own help,
  and a clean per-form `SYNOPSIS` that gives `git clone` its required
  `repository` while multi-form verbs (`git branch` lists *and* creates)
  stay permissive. The manual is read only when regenerating stubs, so it
  never becomes a runtime dependency; the extraction folds the manual's
  typographic punctuation to ASCII and keeps one sentence per flag.
- **git's globals reach `.opts()`, and every multi-command tool's
  `.opts()` keeps the chain typed.** `tools.git.opts(git_dir="…",
  work_tree="…").commit(…)` now completes git's global options — read from
  the `git help git` manual — and places them before the verb, where git
  requires them (`git -C x commit` runs in x; `git commit -C x` reuses a
  commit). Every tool with subcommands declares a self-returning `opts()`,
  so the chain after it stays typed even for a tool footman found no
  globals for.
- **`tools.<tool>.opts(...)` binds a tool's global options before the
  subcommand** — `tools.docker.opts(host="tcp://x").compose.up(detach=True)`
  runs `docker --host=tcp://x compose up --detach`. Some options belong to
  the tool, not the verb, and must precede it (cobra tools like docker
  reject a global after the subcommand); `opts` places them correctly and
  keeps chaining, typed per tool and returning the tool so the rest of the
  chain stays checked. A generic untyped `opts()` is available on any tool.
- **The stubs know each verb's positional shape.** Read from the tool's
  own usage line (or click's declared arguments): `mkdocs build` takes only
  options, so a stray positional is now a type error; `docker run` requires
  an image positionally, so `docker.run(image="x")` is caught. The parser
  is deliberately conservative — anything ambiguous stays permissive, so it
  never forbids a call the tool would accept, and git's idiosyncratic
  multi-form `-h` grammar is trusted for nothing.
- **A reference page per tool**, in a new **Tools** section of the docs.
  mkdocstrings renders each one straight from that tool's stub, so every
  flag arrives with the tool's own help text, its accepted values as a
  `Literal`, and the `off` spelling where one applies. The index table
  states the version each stub was read from and whether the tool can run
  in footman's process — built from the checked-in stubs, so the docs
  build needs nothing on PATH.
- **A type-level test for the stubs** (`tests/typecheck_tools.py`): a file
  of tool calls that is never executed and never collected, only
  type-checked. Its negative cases are the real assertions — since
  `**flags: Any` swallows an unknown keyword, a call that is *required to
  fail* is what proves a flag is declared and typed.

### Changed

- **Valued long options are executed attached** (`select="E"` →
  `--select=E`). This is invisible in what footman shows you — the shown
  line stays separated and readable — but it fixes two silent failures:
  an optional-value option whose value was read as a positional
  (`--abbrev 4` → `--abbrev=4`), and a dash-leading value read as another
  option (`--format -%h` → `--format=-%h`). The rule covers every tool,
  including undeclared ones. `recording()` assertions on `.command` are
  unaffected; assert on `.raw` for the exact spelling.

### Fixed

- **A wrapper verb's flags no longer leak into the wrapped command.**
  `tools.uv.run("pytest", "-q", frozen=True)` emitted
  `uv run pytest -q --frozen` — and uv never saw `--frozen`, because
  everything after `run`'s arguments belongs to pytest. The bridge now
  knows which verbs wrap a command (`uv run`, `uv tool run`, `coverage
  run`, `docker run`/`exec`, `docker compose run`/`exec`) and places their
  flags first: `uv run --frozen pytest -q`. The wrapper set is read from
  each verb's usage line and checked by `fm footman tools audit`.
- **Optional-value options are no longer mistyped as switches.** A tool
  that glues its placeholder to the flag — git's `--gpg-sign[=<key-id>]`,
  `--untracked-files[=<mode>]`, ruff's `--add-noqa[=<REASON>]` — was read
  as taking no value, so the stub rejected `gpg_sign="KEY"`, which is
  valid. These now type as `_ValuedFlag`: usable bare (`gpg_sign=True`,
  sign with the default key) *or* with a value, both spelling a valid
  command.
- **`off` now speaks each tool's own dialect.** It assumed the negation
  of a default-on flag is `--no-<name>`, which is wrong often enough to
  break real commands: `mkdocs build --no-clean` is rejected outright —
  the flag is `--dirty` — and five of mkdocs' eight negatable options
  disagree with the convention. The spelling is per-flag data only the
  tool knows, so footman asks: the new `footman._toolspec` reads click's
  `secondary_opts` (with defaults, types, and help text for the stubs
  and reference pages to come), and the exceptions ride in a table `off`
  consults. `clean=off` emits `--dirty`; `strict=off` still emits
  `--no-strict`; other tools are untouched. A test diffs the table
  against the installed tools, so a tool that changes its spelling fails
  a check instead of quietly producing a command it refuses.

## [0.15.0] — 2026-07-20

### Added

- **Counted progress: `progress(done, total)` and `track(iterable)`.**
  Work that knows how far along it is — 23 of 150 migrations, bytes of
  a download — is better evidence than any duration history, so a
  reported count now drives the live bar directly and outranks the
  estimator. That makes the bar honest on a task's *first* run, where
  the estimator is still gathering samples. A reporting task
  contributes a fractional unit to the run (three done and a fourth
  halfway is 3.5/4), so a chain of reporters fills smoothly and a mixed
  chain is smooth where it can be. `track()` is the ergonomic form —
  total from `len()`, `total=` for generators, report cleared if you
  break out early. Both are no-ops outside a run.
- **`fetch(url)` — download into footman's cache.** Cached by URL under
  `footman_cache_dir()` (so `FOOTMAN_CACHE_DIR` relocates it and the
  daily collector tends it), revalidated with ETag / `If-Modified-Since`
  rather than re-downloaded, optionally verified with `sha256=`, and
  copied anywhere with `into=`. A fetch is a **step**: `--dry-run`
  prints it without touching the network, `recording()` asserts on it,
  `--json` carries it, and it lands in the step lines beside `run()`.
  Byte counts feed the new progress bar. A cached copy survives a
  failed refresh, so a warm cache still builds offline.
  **Backends**: stdlib `urllib` by default — zero dependencies,
  deterministic, and the only one that can report bytes as they arrive
  — with `curl` (in Windows' System32 since build 17063, and on every
  POSIX box), `httpx`, and `requests` available when named, plus an
  explicit `auto`. Choose per call or set `[fetch] backend` anywhere on
  the config ladder: a machine behind a corporate proxy names curl once
  in `~/.config/footman/config.toml` and every project follows.
  Deliberately never automatic — a download that silently changed
  engine when an unrelated dependency appeared would change its TLS
  trust store and proxy semantics with it; a urllib failure instead
  raises a taught error naming that exact config line.

- **`inherited()` — extend an overridden task instead of replacing it.**
  A nearer `tasks.py` overriding a task by name usually means *and
  also*, not *instead of*. Inside the overriding task, `inherited()`
  hands you the task you shadow as the plain function it is:
  `inherited()(fix=fix)`. Forwarding is deliberately manual — the two
  signatures are independent, so automatic forwarding could only drop
  arguments silently or fail at run time, where spelling the call out
  shows the mismatch as you type it — and it chains through a cascade
  of any depth. Two
  discovery surfaces come with it: `fm --where <task>` now lists the
  whole shadow chain (winner first, each shadowed definition after),
  and `fm --help <task>` shows the inherited task's usage line, so the
  forwarding call can be read straight off it (additive `shadows` key
  in the manifest, present only when something is shadowed). Calling it
  where nothing is shadowed is a taught error naming `--where`.

- **`@task(infinite=True)` — tasks that run until you stop them.** A dev
  server or follow-mode tail isn't late, it's intentional: `infinite`
  implies `progress=False`, the status line yields to a one-time dim
  hint (`serve runs until you stop it — Ctrl-C`), and listings and
  `--help` carry a `(runs until Ctrl-C)` note (additive `infinite` key
  in the manifest). Distinguishing "don't time this" from "this never
  ends" came out of reading the cookbook's dev-server recipe.
- **Brands can rename the tasks file.** `App(..., tasks_file="acme.py")`
  sets the default filename a branded CLI looks for; per-project config
  (`tasks`) still overrides it, and the filename is baked into the
  cached manifest (additive) so the background completion refresh — a
  child that cannot know the brand — reads it back and rebuilds with
  the right file.

### Fixed

- **Completion output is LF on every platform.** Windows text-mode
  stdout translated the resolver's newlines to CRLF, so a shell reading
  lines literally — git-bash's `read` — kept the carriage return and
  completed `--fix\r`, planting a stray CR at the cursor. The resolver
  now writes bytes straight to the underlying buffer, which skips the
  translation and pins UTF-8 besides. Found by driving the real
  git-bash on a Windows runner, not by reading the code.
- **git-bash on Windows is detected and installed correctly.** A bare
  `fm --install-completion` inside git-bash used to answer "pwsh",
  because PowerShell's `PSModulePath` is machine-level environment and
  is set there too — so the user got a hook their shell would never
  read. Detection now checks the `MSYSTEM` variable git-bash exports
  first, and the `source` line written into `~/.bashrc` uses the MSYS
  spelling (`/c/Users/…`); a backslashed Windows path in a bash rc is a
  string of escapes that silently sources nothing. Install and uninstall
  build that line through the same helper, so uninstall can't strand it.
  The Windows CI job now drives the real git-bash to prove detection.
- **Recordings no longer depend on what's installed beside footman.**
  fish's autosuggestion drew on the build machine's PATH, so the same
  script recorded `factor` on macOS and `f77` — the Fortran compiler —
  on the Linux runner, which read as stray characters at the prompt.
  Autosuggestions are off in the recording's scratch config now: a cast
  should show footman's completion, not the host's toolchain.
- **Casts render dim text as dim.** pyte spells the bright ANSI
  colours `brightblack`; rich spells them `bright_black` and silently
  ignores a style it cannot parse — so anything dim was drawn in the
  normal foreground. That is the whole story behind the stray "77" in
  the fish recording: it was fish's own autosuggestion (`f77`, a real
  Fortran command on the Linux build machine; `factor` on macOS) drawn
  in white instead of grey, so it read as characters typed into the
  prompt rather than a suggestion.
- **Casts no longer type the terminal's own answers into the prompt.**
  The recorder answers cursor-position queries because PSReadLine and
  reedline paint nothing without one — but fish asks *mid-session* and
  then inserts the reply at the cursor, so `fm che` recorded as
  `fm ch77e`. Cursor replies are now sent only to the shells that need
  them (pwsh, nushell); bash and zsh never cared, and fish is visibly
  happier without. Verified by re-recording all five.
- **Casts no longer flash the shell's terminal queries.** pyte doesn't
  consume DCS sequences, so fish's XTGETTCAP capability probe rendered
  its hex payload as screen text for one frame before the prompt
  painted. Those sequences are terminal protocol, not screen content,
  and are now stripped before the emulator sees them; recordings also
  skip any blank frames before the first paint, so they open on the
  prompt.
- **The 0.12.0 changelog entry had a second `Changed` section** holding
  the merged-coverage note, which shipped in 0.13.0 — it now sits under
  the release that carried it.

### Docs

- **The `serve` examples use `@task(infinite=True)`** on the home page,
  the README, and getting-started, matching what the runner now offers.
- **The cookbook.** Seventeen recipes across the whole surface — the
  parallel gate, passthrough, stacking validators, git-branch TAB
  completion via `suggest()`, build matrices, monorepo overrides,
  tasks that return data for `jq`, the coding-agent loop, testing
  recipes, and a branded CLI — closing the last open docs item from
  the original v0.4.0 audit.

## [0.14.0] — 2026-07-20

### Added

- **Install once, run anywhere: the uv handoff.** A globally-installed
  `fm` (`uv tool install footman`) now hands the invocation to `uv run`
  when the project's `uv.lock` pins footman and the running interpreter
  isn't already inside the project's environment — so plain `fm check`
  works from any uv project, at the project's pinned footman version,
  with the project's tools on PATH, no `uv run` prefix. The rule is one
  sentence: the lockfile declaring footman is what makes it fire. POSIX
  replaces the process (`execvp`); Windows spawns and waits, because
  `exec` there lies about exit codes. `--version`, completion management,
  and the TAB hot path never hand off; `uv = false` in `[tool.footman]`
  or `FOOTMAN_NO_UV=1` opts out for purists, and `-v` says when a handoff
  happened. uv only for now — poetry/pdm handoffs will be considered
  if there's a want for them.
- **A user-level config file completes the precedence ladder.**
  `~/.config/footman/config.toml` (honouring `XDG_CONFIG_HOME`; move it
  with `FOOTMAN_CONFIG`) now seeds every merge: personal defaults — a
  purist's `uv = false`, a permanent `progress = false` — that every
  project layer cascades over. The ladder, weakest to strongest:
  defaults, the user file, the root-to-cwd cascade (standalone
  `footman.toml` beating `pyproject.toml` within a folder, as is
  customary), `--config`, environment, flags. The docs gain a dedicated
  [Configuration](https://willemkokke.github.io/footman/configuration/)
  page for all of it.
- **The cache cleans up after itself.** At most once a day, a run
  spawns a detached collector that removes cache pairs whose project
  directory no longer exists (manifests now bake in the `cwd` they
  describe — additive) and pairs idle for 90 days. A fresh cache only
  plants tomorrow's stamp, so short-lived caches — a test suite's tmp
  dirs — never spawn anything; the invoking directory's own files are
  never touched; and every deletion is safe by construction, because
  the cache is derived state that rebuilds on the next run. It runs
  after the uv handoff, so a pinned project's own footman collects.
  `gc = false` disables it — from the user-level config file only,
  since per-project switches for a shared cache would lie (a `-v` run
  notes and ignores them); `FOOTMAN_NO_GC=1` is the blunt override.

### Changed

- **`--config` now replaces all discovered configuration** — the global
  file and the cascade both — instead of overlaying the cascade. With a
  user-level file in the ladder, "the named file is exactly what
  applies" is the only rule that stays one sentence; an explicit
  `--config` is total control by intent.

## [0.13.0] — 2026-07-19

### Added

- **Keyword-only parameters are options — required options without a
  default.** Python's `*` already says "must be named": a parameter after
  `*` (or `*args`) now maps to `--name`, and without a default it is a
  *required* option — the shape defaultless dicts and flags always had.
  Previously a defaultless keyword-only parameter was silently treated as
  a positional, which its own signature then refused at call time.
- **`fm footman docs shots` — terminal screenshots that cannot lie.** Runs
  a command on a real pseudo-terminal (colours, receipts, taught errors,
  exactly as a terminal renders them), collapses live rewrites to their
  final frame, and saves a macOS-style framed SVG via rich. Everything
  after `--` is the command line to capture; `--width`, `--title`, and
  `--cmd` shape the frame (the default executable is the invoking CLI, so
  branded CLIs screenshot themselves). rich is *not* a dependency: the
  task is gated with footman's own `@task(requires="rich")` and lists as
  unavailable without it — the availability machinery, dogfooded. The
  docs site now embeds these, regenerated on every build.
- **Both engines dress step lines identically.** A chain's buffered
  blocks (`fm lint format`) rendered plain `ok` lines while the same
  work inside a task-body `parallel()` (`fm check`) rendered the full
  terminal treatment — ✓ marks, bold names, dim commands, cyan times.
  Captured children now style for the terminal they replay onto, exactly
  like `parallel()` children always did; in-place rewrites and the
  announce line stay live-only, so no control bytes ever land in a
  capture buffer (or the `--json` envelope). One look, both engines,
  finishing the 0.12.0 unification.
- **Captured blocks no longer start with the `→ running` line.** The
  arrow announces what is running *now*, which is only worth a line while
  output is live — a TTY rewrites it in place, a streamed CI log may wait
  minutes under it, and both keep it. A buffered block (chains of two or
  more, `parallel()` in a task body) flushes when the task is already
  done, where "starting X" directly above "finished X" said nothing —
  those blocks now open straight with the completion line. Surfaced by
  the first `docs shots` screenshot, which faithfully photographed the
  redundancy.
- **`fm footman docs cast` — animated terminal recordings, no JavaScript.**
  Boots a real interactive shell — zsh, bash, fish, pwsh, or nushell —
  from a scratch config with footman's completion hook loaded via
  `--setup-completion`, types a keystroke script (`"fm che"`, `<TAB>`,
  `<ENTER>`, `<WAIT>`…), and replays the capture through a terminal
  emulator into one self-contained SVG animated by CSS keyframes with
  the session's own timing — an `<img>` plays it. **Every completion
  page now opens with its shell's own recording**: zsh's `_describe`
  menu (and a real `fm check` run to its receipts), fish's pager,
  PSReadLine's MenuComplete grid with tooltips, nushell's completion
  menu, bash's candidate list — re-recorded from live shells on every
  docs build. The session answers terminal interrogations (capability,
  cursor-position, and colour queries) like a plain xterm, because
  modern shells refuse to paint a prompt into silence, and it makes the
  pty its child's controlling terminal, because fish, nushell, and
  PSReadLine refuse interactive mode without one. Needs rich + pyte
  (the `shots` group), gated with `@task(requires=…)` like its sibling;
  the scratch HOME hands the invoker's completion cache through
  `FOOTMAN_CACHE_DIR`, so TAB answers exactly as it would at your
  prompt.
- **`fm footman docs globals` — the runner's global options as a markdown
  table.** Rendered straight from the CLI grammar: the same rows, in the
  same order, with the same words `--help` prints, with `{prog}` speaking
  a branded CLI's own name. `footman.markdown.globals_table(prog=…)` is
  the function behind it. This site's CLI reference now regenerates its
  table on every docs build, so it can never drift from the runner again
  (it had, three ways, which is how this feature earned its place).

### Changed

- **The published coverage report is the merged matrix picture.** The
  docs site's embedded report used to be re-measured on one
  ubuntu-only run, understating the number CI actually gates on. The
  merge job now renders the combined HTML — every OS, every Python,
  the real-shell jobs, and the docs build itself, which runs the whole
  taskdocs pipeline (five shell casts included) under coverage and
  merges in like any other job — and both docs builds embed that
  artifact instead of measuring their own slice.

## [0.12.0] — 2026-07-19

### Added

- **A progress bar that earns its confidence.** On a TTY, every run keeps
  one live status line on stderr: green runs teach footman how long each
  exact invocation shape takes (last 50 wall totals per chain + values +
  passthrough + serial/parallel, per directory), and once five recent runs
  agree closely enough, the line becomes a real bar — filling against the
  history's 90th percentile, clamped at 98% so it never claims done early,
  labelled with elapsed vs. typical time. Sparse or erratic history renders
  an honest bouncing pulse with elapsed time instead. Both parallel engines
  feed the same line, so a chain and a `parallel()` inside a task body
  finally present identically, running names appearing the moment each unit
  starts. Without a TTY, a confident estimate prints once as `eta ~5.8s` on
  stderr — CI still records, still learns. Off switches at every level:
  `--no-progress` for a run, `progress = false` in `[tool.footman]` for
  good, and `@task(progress=False)` for tasks whose duration has no rhyme
  (runs containing one never record and only pulse). Failed runs are never
  recorded; a missing, corrupt, or read-only history never fails a run.
- **`FOOTMAN_CACHE_DIR`** relocates every footman cache — completion
  manifests and timing history alike — in one variable; the XDG rules stay
  unchanged beneath it, and the completion hot path honours it with no
  re-install.
- **`-j/--jobs N` and `jobs = N` in `[tool.footman]` cap the parallel
  width** — in both engines: the scheduler's pool and `parallel()` inside
  task bodies. Unset, the default is now cores - 1 (never below 2) instead
  of effectively unbounded — the machine stays responsive while fan-outs
  stay real. The width is part of the timing key, so `-j2` runs build
  their own duration history.
- **Receipts are task-shaped: `✓ check  (5.2s)`.** The end-of-run summary
  speaks the same grid as the step lines — mark, name, time — with the
  name in bold cyan (same family as the steps, one rank up) and durations
  humanised. A single task's receipt *is* the total, so the separate
  `took` line only appears for chains of two or more, dimmed, where the
  wall total genuinely adds information. `--timings` keeps millisecond
  precision on the receipts. The `--json` envelope carries the total as
  an additive top-level `total_ms`.
- **One palette across the whole CLI.** `--help`, `--list`, `--tree`, the
  `--dry-run` plan, and error messages now speak the same visual language
  as the step lines and receipts: names and headers bold, groups bold
  cyan, mechanics and optional syntax dim, required placeholders cyan,
  the `fm:` error prefix red. Usage lines and synthesised examples are
  painted from one token grammar (prog/group/task/required/optional), so
  every command line footman prints is lit the same way. Colour is gated
  per stream on its own TTY — piped output, `--json`, `--where`, and
  `NO_COLOR`/`--no-color`/`TERM=dumb` runs stay byte-identical to before.

### Changed

- **Development Status: Alpha → Beta.** The PyPI classifier now says what
  the last few releases have shown: the surface is settling, the test bed
  is broad, and coverage is enforced. Pre-1.0 minors may still include
  breaking changes, as the header above says.

### Fixed

- **`-s/--sequential` now reaches inside task bodies.** It serialised the
  scheduler's tasks but `parallel()` inside a body still fanned out — so
  `fm -s check` ran just as parallel as ever. The user's sequential request
  now rides the task context (`ctx.sequential`) and `parallel()` honours
  it: `-s` means no concurrency anywhere. Serial runs already kept their
  own timing history (the flag is part of the chain key), so their
  estimates stay honest too.
- **A single-task invocation now streams live, with colour.** The default
  scheduler treated even one task as a parallel plan, so `fm check` — the
  most common shape there is — buffered everything into one uncoloured
  block flushed at the end, and `run()`'s TTY mode (green ✓ / red ✗, the
  in-place step rewrite) never fired. One node has nothing to parallelise:
  it now takes the sequential-live path, so steps appear as they happen and
  the TTY treatment applies. Chains of two or more keep the buffered
  non-interleaving contract unchanged.

## [0.11.0] — 2026-07-19

### Added

- **Parameter docs come straight from your docstrings.** Google
  (`Args:`), NumPy (`Parameters` + underline), and Sphinx (`:param x:`)
  styles are auto-detected per docstring; entries fill each parameter's
  help in `fm --help <task>`, in completion menus that show descriptions,
  and in the `--json --list` catalog — everywhere a `doc("…")` marker
  reaches, and the marker still wins for the same parameter. The body
  between the summary and the section becomes the task's **long help**,
  rendered by `--help` and carried as an additive `long` key. A docstring
  entry that names no real parameter warns, the same loudness a broken
  annotation gets.
- **`footman.docstrings` — the parser behind it, public and standalone.**
  Stdlib-only with no footman imports (lift the file into any project):
  `parse(text)` returns a frozen `Docstring` with `summary`, `long`, and
  `params`, tolerant of tabs, CRLF, uneven indentation, and unusual
  section orders.
- **The docs site follows your system's colour scheme by default**, with a
  three-state auto → light → dark toggle.
- **`fm footman docs page` / `site` — your tasks, documented.** A
  first-party plugin (mount with `[tool.footman] plugins = ["footman"]` —
  the two-line demo of the plugin system) renders a project's task tree as
  markdown: one page (scoped to the tree, a group, or a task, headings
  nestable for snippet includes, pipeable to pandoc) or a linked site
  (one file per task, an `index.md` per group) for zensical/mkdocs navs.
  Two flavors: portable CommonMark, or `material` with anchors and example
  admonitions. Content is phrased by the same code as `--help` — names,
  params, docstring help, defaults, synthesized examples — so pages can't
  drift from the CLI. Usage lines carry the CLI you invoked — a branded
  `acme` documents itself with no flag (`ctx.prog`, new on the task
  context, carries the invoking brand); `--prog` overrides. The renderer
  is public (`footman.markdown`), the manifest gains an additive `default`
  key, and footman's own docs dogfood both modes: the Task reference
  section and the embedded sample on the "Your tasks, documented" page are
  regenerated on every docs build.

### Changed

- **Step lines are columns now: mark · task name · command · time.** Every
  `run()` line carries the task it belongs to, padded so siblings align;
  on a colour terminal the name is bold, the command dimmed, and the
  `(time)` cyan, aligned to the widest command — the width rides the
  timing history, so a warm run aligns from its very first line (a cold
  one learns as it streams). Anonymous
  `parallel()` thunks show `…` — pass a named function or a
  `functools.partial` (its callee's name is used) for a real label.
  Durations everywhere now humanise past seconds: `4.1s`, `42s`, `1m10s`,
  `4h35m` — step lines included, which used to print raw seconds forever.
- **The run summary and live progress line moved to stderr.** One rule now
  governs the streams: *stdout is the answer, stderr is the commentary*.
  Task output — and footman's own answers (listings, help, `--json`
  envelopes) — stays on stdout; the `ok`/`FAIL` summary, `--timings`, and
  the live status line join warnings and errors on stderr. So
  `fm task > file` captures exactly what the task produced, and piping
  stdout keeps the live line visible on the terminal. Behavioral: anything
  that parsed the summary from stdout should read stderr (or use `--json`);
  wrappers that treat stderr bytes as failure can pass `-q`.

## [0.10.0] — 2026-07-19

### Added

- **`doc("…")` — per-parameter help, in the established `Annotated` marker
  idiom.** One line of the author's words per parameter, and it pays three
  times: it leads the option's line in `fm --help <task>`, it becomes the
  option's completion description in shells that render one (zsh, fish,
  nushell, PowerShell tooltips — options used to complete bare), and it
  rides in the `--json --list` catalog as an additive `doc` key. Inert at
  run time, like every marker.
- **An AI agents page and a generated `llms.txt`.** docs/agents.md ships a
  paste-ready CLAUDE.md/AGENTS.md snippet (the discovery loop, grammar,
  envelope, exit codes) plus edit-time and stop-gate hook recipes for
  Claude Code and Cursor. The docs build now generates `llms.txt` and
  `llms-full.txt` from the nav — an agent-readable index and full text of
  the site — and the Pages workflow builds through `fm docs build --check`,
  the same task devs run.

- **Tasks can return JSON.** A task's return value now lands in its `--json`
  entry under `returned`: return a dict (or list, string, bool, …) and a
  machine consumer gets it verbatim; return `None` and the key is absent. An
  `int` return keeps its existing meaning — the exit code, never data. The
  types footman coerces *in* (`Path`, `Enum`, `datetime`, `UUID`, `Decimal`,
  dataclasses, sets) serialise symmetrically on the way *out*; any other type
  is dropped loudly — a `returned_error` note in the entry, a warning on
  stderr, and the run's exit code untouched. The envelope stays `schema: 1`
  (additive only). `Runner.invoke(...).results[n].returned` already exposed
  the same value for tests.
- **`--json` now means: stdout is exactly one JSON document, whatever
  happened.** New envelopes cover every surface that used to fall back to
  text: a refusal (typo'd task, bad flag, broken tasks file, `--config`
  error, Ctrl-C) emits `{"schema": 1, "error": {"code", "message"}, "results":
  []}` alongside the stderr message; `--list`/`--tree`/bare `fm` emit the full
  task tree with parameter specs (`{"schema": 1, "tree": …}`) — the machine
  catalog agents were missing; `--dry-run` emits the parsed plan
  (`{"schema": 1, "globals": …, "plan": …}`); `--version` emits
  `{"schema": 1, "name": …, "version": …}`. The one exception is `--help`,
  which stays human — its machine twin is `fm --json --list`.

- **`--uninstall-completion [shell]` reverses the installer exactly**: the
  script file goes, the rc/profile line goes (UTF-16 profiles stay UTF-16,
  one BOM), and both directions are idempotent. When the shell itself has
  vanished from PATH, the script is still removed and the leftover rc line
  is printed for hand-removal.
- **A completion page per shell.** bash, zsh, fish, PowerShell, and nushell
  each get their own docs page: what installs where, the session-only form,
  what the completion menu shows, and — new — how to customise its colours
  and appearance with copy-paste snippets (`zstyle list-colors`,
  `fish_pager_color_*`, PSReadLine `-Colors`, nushell's `completion_menu`
  style block), each verified against the real shell.
- **`--setup-completion <shell>` prints the completion hook to stdout**, for
  enabling completion in the current shell only — no rc file touched:
  `eval "$(fm --setup-completion zsh)"` (bash/zsh), `fm --setup-completion fish
  | source`, or `| Out-String | Invoke-Expression` for PowerShell. A bare
  `--setup-completion` detects the shell, with the note on stderr so stdout
  stays clean for `eval`.
- **`fm`'s own global options now complete.** Typing a flag before the first
  task — `fm --<TAB>`, `fm --inst<TAB>`, `fm -<TAB>` — offers the globals
  (`--help`, `--list`, `--install-completion`, `-C`, …); a bare `fm <TAB>`
  still lists tasks only. Resolver-side, so no re-install is needed.
- **Python 3.14 is tested in CI**, including the free-threaded (no-GIL) build —
  footman runs tasks in real parallel threads, and the suite passes with the
  GIL disabled.

### Changed

- **nushell completions now carry descriptions.** The external-completer hook
  returns `{value, description}` records, so task and group names show their
  one-line docstring in nushell's menu instead of being stripped to bare names.
  Re-run `fm --install-completion nushell` to pick it up.
- **zsh completions now use the native `_describe` builtin.** The rich-
  description hook right-aligns descriptions into a column and honours your
  completion styling (`list-colors`, `descriptions` `format`) — the same look
  `_git` and `_npm` produce — instead of the hand-formatted `name -- desc`.
  Re-run `fm --install-completion zsh` to pick it up.

### Fixed

- **The completion-latency headline is now the number users actually get.**
  The docs quoted ~19/20/23 ms in different places; the honest figure for the
  installed hook path (`fm --complete` via the console script) is **~25 ms**,
  now measured directly by `scripts/bench_completion.py` and quoted
  consistently everywhere. The ~15× multiplier vs re-importing runners is
  unchanged.
- **`fm --help <typo>` now refuses with a suggestion** (exit 2, `unknown task
  or group 'nope' — did you mean …?`) instead of silently printing the global
  help with exit 0. With a real target on the line (`fm --help deploy prod`),
  extra words are still tolerated as argument values.
- **A misplaced global option is taught by position, not treated as unknown.**
  `fm check --json` now says ``--json is a global option — it goes before the
  first task name`` instead of `unknown option`; same for short aliases
  (`fm lint -k` names `--keep-going`). A task parameter that shares a
  global's name still wins by position, as before.
- **Bare `fm` now ends with the same `--help <task>` pointer the help screen
  shows** — the no-argument path is exactly where a newcomer lands.

## [0.9.0] — 2026-07-18

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
- **Exit codes now follow the documented contract.** A binding refusal — a bad
  coercion, an out-of-bounds value, an unknown option — exits **2**, not a flat
  `1`; a `run()` command that fails propagates the command's own exit code; and
  a failing `parallel()` thunk propagates too. `fm` mirrors what it ran.
- **`--no-color` / `NO_COLOR` / `TERM=dumb` drop the live progress line
  entirely**, matching piped output, instead of rewriting it without escapes.
- **In-process tools honour cwd and env.** They run from the folder that defined
  the task and see its environment overlay — the run-from-defining-folder
  contract the subprocess path already obeyed — and `run(..., capture=False)`
  streams output live instead of buffering it.

### Added

- **`@task(requires=...)` — gate a task on optional dependencies,
  import-free.** Names Python modules a task needs, checked with
  `importlib.util.find_spec` (which locates without importing), so a shared
  library can carry release tasks with heavy third-party deps: keep the
  `import` in the body (paid only when the task runs), and a missing package
  lists the task as `(unavailable: <reason>)` and refuses to run cleanly,
  instead of a raw `ModuleNotFoundError`. Reuses the `when=` availability
  machinery — shown in `--list`/`--help`, re-checked live, a `pre`/`post`
  on it fails hard. New docs: *A shared library with heavy or optional
  dependencies* in Composing tasks.
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
- **Completions that teach.** In zsh and fish, task and group descriptions
  render next to the candidates; `--help` ends with a synthesised `Example:`
  invocation built straight from the signature; and a "did you mean?" hint fires
  at every not-found site (unknown task, option, choice, or `--where` target).
  Bare `fm` now lists the tasks instead of erroring.
- **Completions that stay fresh.** A stale-while-revalidate background refresh
  rebuilds a directory's cached manifest once it ages past `[tool.footman]
  completion.max_age` (default 10 min; `off`/`0` disables) — the <kbd>Tab</kbd>
  returns the cached answer instantly and never blocks on the rebuild.
- **`--opt=value` completes in every shell**, and value-bearing globals (`-C`,
  `--config`, `--tasks-file`, …) no longer send the completion walk descending
  as if their value were a task.
- **`capture`, `Runner`, `Result`, and `recording`** import straight from
  `footman` (previously only from `footman.testing`).

### Fixed

- **PowerShell completion after a space.** Windows PowerShell 5.1 and pwsh
  7.0–7.2 silently drop an empty-string argument to a native command, so
  pressing <kbd>Tab</kbd> after a space re-completed the previous word instead
  of the fresh position. The hook now flags the empty position with
  `--empty-partial` and the resolver supplies the `""` itself. **Re-run
  `fm --install-completion pwsh`** to pick up the new hook.
- **`--help` never touches the filesystem.** `fm --install-completion fish
  --help` used to write rc files before printing anything; and `fm --help` with
  no tasks file now shows the global help (so a stuck newcomer sees `-f`/`-C`),
  not a bare one-liner.
- **`-C/--directory` restores the working directory** afterwards, so an
  in-process caller (a test runner) is no longer left in the changed folder.
- **`-f/--tasks-file` no longer poisons** the directory's cached completion — a
  one-off `-f` run leaves <kbd>Tab</kbd> describing the real cascade.
- **Plugins and the cascade are sturdier.** A plugin that fails to import is
  taught at exit 2 instead of dumping a traceback on every invocation;
  `availability()` never crashes on a `requires=` whose parent package raises; a
  cascade file that registers tasks and then raises no longer leaves ghost tasks
  behind; each `tasks.py` gets its own copy of a sibling `import helpers`; and
  provider trees are isolated per project so one project's tasks can't leak into
  another.
- **Completion install is more robust.** bash `COMPREPLY` is glob-safe
  (`printf %q`), rc-file edits sniff BOM/encoding so a UTF-16 Windows PowerShell
  profile no longer crashes the install, and installs target the rc files shells
  actually read (`$ZDOTDIR` for zsh; the login profile alongside `.bashrc` for
  macOS bash).
- **Loud errors where footman used to stay silent** — a missing or typo'd
  `--config` file, a `**kwargs` task, `=value` on a flag-shaped global, and a
  `--` handed to an option as its value.
- A broad correctness pass across type coercion (strict env and variadic values,
  unions that carry both choices and types, dict value-type markers), the
  scheduler (each explicit chain segment runs; `parallel()` steps surface in
  `--json`), and the tools surface (`tools.run`/`tools.sys` resolve to Tools;
  `installed_version()` decodes UTF-8).

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

[Unreleased]: https://github.com/willemkokke/footman/compare/v0.17.0...HEAD
[0.17.0]: https://github.com/willemkokke/footman/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/willemkokke/footman/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/willemkokke/footman/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/willemkokke/footman/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/willemkokke/footman/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/willemkokke/footman/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/willemkokke/footman/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/willemkokke/footman/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/willemkokke/footman/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/willemkokke/footman/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/willemkokke/footman/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/willemkokke/footman/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/willemkokke/footman/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/willemkokke/footman/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/willemkokke/footman/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/willemkokke/footman/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/willemkokke/footman/releases/tag/v0.1.0
