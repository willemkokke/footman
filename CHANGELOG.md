# Changelog

All notable changes to footman are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/). While footman is pre-1.0, minor
versions may include breaking changes.

## [Unreleased]

### Added

- **Completion continues a comma-separated list.** Mid-list in a
  comma-splitting value, <kbd>Tab</kbd> completes the segment after the last
  comma with the typed items kept in place: `--paths=src/a.py,<TAB>`
  completes the second path (a new resolver exit code, 101, tells the bash,
  zsh, fish, and pwsh hooks to strip through the comma before the file walk;
  nushell's completer protocol can't rewrite part of a token, so it keeps
  completing the first item only). A list with a value set — `Literal`
  choices or `suggest()` — completes each item after a comma too, minus the
  values already in the list, and `suggest()` still recomputes fresh with
  just the tail as the partial. `nosplit` parameters keep their commas
  literal, in completion as at the prompt. Reinstalled hooks pick this up;
  an existing hook keeps today's behaviour everywhere else.

### Fixed

- **A second playground `fm test` sees your edits.** In-process pytest left
  the editor's files in `sys.modules`, so rerunning collected the first
  run's modules until the page was reloaded; the driver now evicts them
  after every run (and skips bytecode caching, whose mtime granularity
  could resurrect a stale rewritten test inside one clock tick).

## [0.26.0] — 2026-07-28

### Added

- **`with parallel()` — a fan-out written as ordinary calls.** Past two or
  three, threading arguments through thunks reads worse than the calls
  themselves, and thunks never gave the values back. Inside the block a task
  call is queued rather than run, so it has no value there (using one is
  taught, never a silent `None`); everything runs when the block ends, under
  the rules a call has anywhere else — its own row, sharing, hooks, `-s`/`-j`
  — and `p.results` hands back what each returned, in the order written. `p`
  is still the list of exit codes `parallel()` has always returned.
  `p.also(fn, *args)` brings a straggler that is not a task — a lambda, a
  plain function — into the same fan-out.
- **A task can be defined while a run is in flight.** `@task` inside a body
  is ordinary Python, and what it makes is a real task — own row, sharing,
  hooks, a place in a `parallel()` block. It lives for the run that made it
  and is swept when the run ends, so a task no listing can show never
  outlives its run; `task(fn)(args)` is the general way to run a plain
  callable as a task. A duplicate name in a tasks file stays a taught error;
  one made mid-run is numbered (`rmtree`, `rmtree-2`), so a `lambda` in a
  loop and a helper used twice are each their own work.

- **The docs have a Playground.** footman runs in the browser: the editor
  is a `tasks.py`, the prompt is `fm`, and the run goes through the same
  in-process `Runner` the testing page teaches. Python arrives via Pyodide
  on first run — nothing installs, and nothing you type leaves the page. A
  browser has no processes and one thread, so `run()` children are
  simulated — labelled `[simulated]`, exit 0 — runs are sequential, and
  failure demos are plain Python (`fail("…", code=3)`); the page says all
  of this plainly. **`fm test` runs the real pytest, in-process** through
  the tools bridge, against a second editor tab whose tests fail on
  purpose — read the diff, fix the code, run it green. The prompt
  completes on Tab — tasks, flags, choice values — through the same
  manifest walk a shell hook consults, rebuilt from whatever the editor
  says. Every runnable example in the docs carries a small "run it there"
  link that opens it in the editor together with what it builds on.
- **Every docs example is executed by the test suite.** A page reads as a
  session: its python blocks run in order in one shared namespace, so the
  first block carries the imports and later blocks build on what came
  before. A block that is deliberately an illustration says so with a
  marker in the source; everything else must import, register, and resolve
  every name — task bodies included — or the gate fails.
- **A tasks file can carry its own dependencies.** A file with a
  [PEP 723](https://peps.python.org/pep-0723/) header (`# /// script`)
  declares what it needs inline; uv builds that environment and the run
  continues inside it, so one portable file works in any folder with no
  project at all. `-f=deploy.py` applies the same rule to any named file.
  uv reads the block natively, so `requires-python` and `[tool.uv]` tables
  (sources, indexes) work as written. The file must list the runner it
  imports — the one refusal, because that environment provably could not
  run it. A project whose lockfile pins footman still owns its runs: the
  header is ignored there, mentioned only under `-v`, so a portable file
  is equally at home checked into a repo. Everything else stays soft: no
  uv, several cascading files, or an unreadable block just run as before,
  and an import that then fails says where the environment went.
- **`footman.main(__file__)` makes a tasks file its own command.** Paired
  with a `#!/usr/bin/env -S uv run --script` shebang, `./deploy.py build`
  runs the file's own tasks from any directory — same options, same
  `--help` — with no runner installed. An explicit `-f` still wins.
- **`pip install footman[uv]`** bundles uv beside the `fm` script, so a
  globally-installed runner carries the tool both handoffs need. Nothing
  imports it; it is found on disk, this runner's environment before PATH.
- **`App(dist=...)`** tells a branded CLI which distribution ships it —
  what a lockfile pins, and what a script header must declare. Unset,
  both handoffs stay out of a branded runner's way.
- **The option history knows which platforms it has looked at.** An
  observation comes from one platform and says nothing about the others, so
  each release records who read it and — beside the surface, never inside it
  — the options they looked for and did not find. Exceptions only: nothing
  recorded means nothing contradicts the option, so a stub says "Not
  available on Windows." exactly where somebody checked. A later sighting on
  that platform clears the claim; a platform that did not run says nothing
  either way. `since` and `until` are withheld where the observations cannot
  support them — a platform's own floor is not a since.
- **`fm tools.gather` and `fm tools.assemble` — observe here, fold there.**
  A Linux box cannot say what a tool's `--help` prints on Windows, so
  gathering writes a portable, self-describing document of what one machine
  saw, and assembling folds any number of them into the store in one
  process. `fm tools.refresh` is both at once, unchanged for a single
  machine. Gathering a tool a platform has never read starts at that tool's
  *base*, so new coverage begins at the version people actually run.
- **`fm tools.prepare-release`** rolls a release the way the runbook does:
  the two version files that must agree, the `--version` example the drift
  test guards, and `[Unreleased]` into a dated section with its compare
  links. Refuses when there is nothing to release.
- **The weekly refresh observes on ubuntu, macOS and Windows** and assembles
  on one runner. Its PR opens on any tree change and auto-merges once the
  gate is green; auto-release is built and gated on `vars.AUTO_RELEASE`,
  default off.

- **The tool walks gather in parallel, on footman's own runtime.** Each
  (tool, release) observation is a task of its own, fanned out with
  `parallel()` in bounded waves — the task boundary gives every observation
  its own environment, listings fetch concurrently, and the chains assemble
  from whatever order the pool finishes in. A release that will not install
  is a reported *hole* rather than the end of a tool's walk: `refresh` plans
  every unobserved release down to the floor, so it fills its own holes on a
  later run. Nine mkdocs releases primed in 27 seconds.
- **`fm --json tools.refresh` answers the release question in one field.**
  The `refresh` row's `returned` carries `release` — were any events
  appended — beside `read`, `events`, `holes`, `unreachable` and
  `wrote_changelog`, which is everything the weekly job reads.
- **The weekly refresh workflow.** Mondays 06:00 UTC (and on demand):
  provision the latest tools, refresh the histories, and open a PR only when
  something changed a command-line surface — carrying the CHANGELOG entry
  the events wrote. An index that would not answer fails the run loudly.
  The PR is pushed with a fine-grained PAT (`REFRESH_TOKEN`) so CI runs on
  it; without the secret the job refuses with directions.
- **`_toolhistory.insert` — a release can arrive at any position.** `extend`
  appends below the floor and `promote` replaces the head; this is the third
  case the format was designed for, a release belonging *between* two the
  chain already holds. Exactly one entry is recomputed — the inserted
  release's successor — however long the chain. What it buys is that
  gathering need not be ordered: installing a release and reading its
  `--help` depends on no other release, so a walk can fetch in any order,
  in parallel or across runs, and assemble as results arrive. A release that
  will not install stops being fatal to a tool's whole walk; the gap is
  filled later, and until then costs precision rather than correctness.

- **Writing plugins — the provider's guide.** One page for the whole
  authoring story: the worked provider, the `footman.tasks` entry point
  (lifecycle-only modules included), pull semantics, the
  `[tool.footman."<entry-point-name>"]` configuration convention, the two
  optional-dependency patterns, the determinism rule, and testing through
  the real pull path.
- **A `GlobalOption` completes dynamically.** A `suggest()`-marked global
  recomputes its choices fresh at <kbd>Tab</kbd>, exactly as a task
  parameter's completer does — the completion subprocess now addresses an
  option by name alongside a parameter by path.
- **Task help shows the declared globals.** A task with `@task(uses=[OPT])`
  ends its `--help` with `reads --env-file (from footman.env_files)` — the
  dependency named where the option will be typed.
- **The wiring advisories.** An option nothing is wired to read — its owner
  contributes no lifecycle hook and no task declares it — draws a warning at
  discovery; a task that declared a global in `uses=` but finished without
  reading it is an advisory under `--verbose`.

### Changed

- **`--tree` aligns its descriptions.** One description column whatever
  depth a name sits at, wrapped with a hanging indent, and no `—`
  separator — `--list`, `--tree` and group help now draw their two-band
  layout through one shared renderer, so a rule about one is true of all
  three (group help gains description wrapping in the bargain).
- **Body calls are units on the live status line.** An inline `build()` —
  fresh, or satisfied by sharing — counts and shows as running exactly
  like a scheduler node or a `parallel()` child. A task handed to
  `parallel()` is counted once, by the body-call machinery, never twice.

- **A GitHub token is used when one is offered.** `GH_TOKEN` or
  `GITHUB_TOKEN` in the environment raises the API budget from 60 calls an
  hour to 5,000 — which matters less for footman's own volume than for a
  shared CI runner, where the unauthenticated allowance is spent by whoever
  else is on that IP. Sent to `api.github.com` only, never to an asset
  download: urllib carries headers across redirects, and the CDN behind a
  release asset has no business seeing a credential. Absent, everything still
  works on the smaller budget.
- **Every curated tool's history reaches ten releases**, so the stubs can say
  when an option arrived rather than only what exists today — 102 new `Added
  in` claims across the set. eclint reaches four, because four is all that
  has been published; prek and python already reached further and keep it.

### Changed

- **build 1.5.1** adds `--env-dir`, `--report` and `--sdist-extract-dir`. It also rewords 3 descriptions.
- **djlint 1.43.0** adds `--stdin-filename`.
- **markdownlint 0.23.2** rewords 1 description. It also restates its own description.
- **ty 0.0.64** adds `--exclude-scripts`.

### Fixed

- **A body call's output is no longer dropped in an uncaptured run.** A task
  reached by `build()` runs with its own buffer so its output stays one
  block, but that block was only ever handed to a *capturing* parent — under
  `--json`, a document run, a `parallel()` child. In an ordinary terminal run
  the parent streams, so there was nothing to hand it to and the callee's
  output went nowhere. It now goes to the terminal, the same handoff
  `parallel()` makes for its own children.
- **A gather that observed almost nothing no longer reports success.** The
  summary line was the same one a complete run printed, and the exit status
  was 0 with 330 of 363 releases unread — a document that looks foldable, and
  folding it records a platform where the tools do not exist. The counts are
  now stated together, so a truncated read shows what was missed beside what
  was found, and a run whose holes outnumber its observations exits 75. Not
  any hole: one release whose asset has gone is ordinary, and failing on it
  would teach a reader to ignore the exit code.
- **A full disk stops the walk instead of being recorded as absence.** A hole
  says *this release* could not be had; a disk with no room says nothing
  about any release, and every observation after it fails identically. An
  install failure with under 512 MB free now ends the run with the same
  "look again" code an unreachable index uses.
- **A hand-written stub is named as one.** The six shells carry the default
  provision kind, so a skipped list called them `uv tier` — a tier nobody
  fetches them from.

- **`footman.docstrings` and `footman.markdown` resolve for a type-checker.**
  Both are lazily served by `__getattr__` and both were named in `__all__`,
  but neither was declared to a type-checker — so the package advertised two
  names no editor could complete, no definition could be jumped to, and a
  consumer running a strict check saw footman complain about itself. Declared
  in the `TYPE_CHECKING` block, which never executes: a bare `import footman`
  still imports nothing.

- **The npm tier no longer needs node on the machine.** It installs through
  bun, but what bun installs is a launcher beginning `#!/usr/bin/env node` —
  and bun only stands in for node when bun itself runs a script, while the
  extractor spawns the launcher as a subprocess where the shebang is resolved
  by the operating system. A machine without node read every npm-tier release
  as `No such file or directory`: twelve cspell releases and eleven
  markdownlint ones, on a Linux box, and on every CI runner it would have been
  the same two tools lost on every leg, indefinitely. The walk now writes a
  `node` that forwards to `bun --bun`, inside the scratch directory so it goes
  when the walk does, and only where bun is present and node is not.

- **A tool's man probe can no longer open a browser.** `git help <verb>`
  honours `help.format`, which Windows defaults to html — so extracting the
  git driver opened the HTML docs in a browser tab per verb, twenty-one at
  a time, from what should be a captured read. The probe pins
  `help.format=man`: a POSIX box reads the same text it always did, and a
  box with no man viewer fails quietly into the existing empty-text
  fallback.

- **The detached refresh and collector children stay invisible on Windows
  11.** With Windows Terminal as the default terminal, a `DETACHED_PROCESS`
  child is handed a *visible* terminal window — one popped over the shell
  for every stale-manifest <kbd>Tab</kbd> and every due collection, and a
  console grandchild allocated another window of its own. They spawn with
  `CREATE_NO_WINDOW` now: a hidden console instead of none, which their
  children inherit.
- **A platform folding into an older release no longer contradicts what is
  stored.** Every release below the newest is kept as a *step*, not a
  surface, and the merge read that step as though it were one — so every
  option the arriving platform saw looked like one nobody had ever found.
  The first real Linux fold tagged 25,802 options as missing on macOS,
  across a store macOS itself had written. The merge now compares against
  the replayed surface, and restitches the two entries that describe a
  release whose surface genuinely moved.
- **A reading has to describe a tool to be recorded as one.** A launcher
  that cannot find its interpreter still prints prose and exits: a machine
  without `node` read every npm-tier release as one bare verb, no options,
  help text saying `No such file or directory`. Stored, that claims the tool
  accepts nothing — 855 options "missing on Linux" for a tool that never
  ran. Such a reading is a hole now, which is what a later run fills.
- **A stub header names every platform that read it**, rather than the first
  alphabetically — a release observed on two platforms credited one and
  quietly disowned the other's evidence.

- **One piece of work is one unit on the live line, whatever the spelling.**
  `parallel(lambda: build("web"))` counted twice — once as the thunk, once as
  the request inside it — where `parallel(build)` and a `functools.partial`
  counted once. `parallel()` now counts every child it is handed and hands
  that unit down; the first task request inside claims it rather than
  counting a second one for the same work. The claim is one-shot and never
  reaches the callee, so a thunk running two tasks counts two, and a plain
  thunk keeps the unit nobody claimed.

- **A task called from `@pre_tasks` or `@post_tasks` is refused, and says
  why.** Both moments sit outside the run, so such a call quietly became a
  plain function call — no result row, no sharing, no availability gate, and
  a task declaring `ctx` took the call's first argument into that slot.
  `pre_tasks` also runs in the child that rebuilds the completion manifest,
  where the call executed the task on a <kbd>Tab</kbd> press. The refusal
  names the moment and both ways out: edit the tree through `inv.tasks`, or
  move the call to a per-task moment, which runs inside the run and gives it
  a real task boundary. Calling a task from outside footman entirely — a
  REPL, an import of the tasks module — is untouched.

- **Re-reading a release no longer overwrites what other platforms saw.**
  The same-version path replaced the stored surface outright — erasing every
  recorded absence while the entry went on claiming those platforms had
  looked — and never recomputed the delta beneath it, so everything below
  replayed against a surface that no longer existed. It merges now, and
  restitches the two entries that reference a surface it changed.
- **Provisioning survives Windows.** The python tier linked its interpreter
  with a symlink, which Windows grants only with developer mode or
  elevation; it falls back to a launcher, since a copied `python.exe` finds
  no standard library.

- **Click extraction no longer mistakes this environment for the binary.**
  The entry point imports from the running process while the binary comes
  from `PATH`, and nothing tied the two together — so priming a click tool
  that also lives in footman's own venv recorded *our* surface under the
  *release's* label: nine empty deltas in a row for mkdocs and zensical, the
  exact two drivers importable here. `_from_click` now requires the entry
  point's distribution version to match the binary's, and falls to the help
  path — which asks the binary itself — on any disagreement. Both chains are
  re-primed: mkdocs turns out to have changed surface in 1.6.0, 1.5.3 and
  1.4.3, zensical in 0.0.50 and 0.0.48.

- **A release asset is fetched by the tag the listing recorded, not one
  derived from the version.** bun tags `bun-v1.3.13` for a binary answering
  `1.3.13`, so deriving the tag made its entire history unreachable — listing
  worked, and only installing failed, which is why nothing looked wrong.

## [0.25.0] — 2026-07-27

### Added

- **`footman.env_files` — the .env built-in.** `plugin("footman.env_files")`
  loads `.env` from the invocation's directory at the run's single-threaded
  moment — before availability gates, so `@requires_env` sees it — with
  env-wins semantics: the real environment always beats the file.
  `--env-file=PATH` names another file (a `GlobalOption`, so it completes as
  a file and exists only when the plugin is pulled); a missing named file
  refuses, a missing default is nothing to do. Parsing is python-dotenv's —
  an optional dependency, imported lazily and taught by name when absent,
  never a footman requirement — with interpolation off.
- **`GlobalOption` — a plugin's own global option.** Constructing one is
  registering it: a module-level singleton in the provider, stamped with the
  defining module, riding the contributions carriage — so `--env-file=…`
  exists exactly when its owner is pulled, and an unpulled owner's option is
  an unknown option, taught. Long-form only, `=`-attached; a `bool`
  annotation is a flag, anything else takes a value coerced and validated
  through the same pipeline as a task parameter, which is also what makes
  completion work by construction — choices, `Path` file handoff — from a
  new `globals` section in the manifest (schema 2; stale completion caches
  refresh themselves). Read `OPT.value` anywhere in-run (frozen after
  parse; outside a run the read is taught). `@task(uses=[OPT])` declares
  the dependency into the manifest and task metadata; an undeclared in-task
  read still works, with a note naming the fix. Collisions are loud: a core
  name names footman, two plugins on one name names both owners.

### Fixed

- **A version scrape never touches the network, and an empty read names its
  cause.** The tool-driver version read now runs with gh's update check
  disabled (`GH_NO_UPDATE_NOTIFIER=1` — every other tool ignores the
  variable), and distinguishes its three failure shapes — spawn failed,
  spawn timed out, output carried no version token — so the CI check that
  guards version-keyed history teaches which one happened instead of
  reporting an em-dash.

- **A prerequisite's `confirm=` is asked.** The documented rule — a task
  that asks for confirmation gets it however it is reached — held for a
  command-line segment (asked up front) and a body call (asked at the
  call), but a task pulled in via `pre=`/`post=` ran unasked. Every gate in
  the plan is now asked up front, in dependency order; one reference is one
  question, however many segments and prerequisites reach it (a repeated
  gated segment also stops asking twice). A denial becomes the task's
  result before anything runs, so it never launches and its dependents skip
  with the denial as their cause.

## [0.24.0] — 2026-07-27

### Added

- **`@post_tasks` — the run report's moment.** The closing bookend to
  `@pre_tasks`: once per invocation, on the main thread, after every task
  concluded and before the summary or the `--json` envelope prints. The
  invocation carries the whole story — `inv.results` (every row,
  chronological, as result views), `inv.skipped` (the subset that never
  ran), `inv.total_ms` — so a run-level reporter finally sees what a
  `post_task`-only reporter cannot: the nodes that never started. Under
  `--json` a hook's stdout is rerouted to stderr (the envelope owns
  stdout); hooks run in cascade order, and a raising hook is named and
  fails the invocation.
- **Skipped nodes are reported, not silently absent.** A node the run never
  started — its prerequisite failed, or the run stopped reaching for new
  work — gets a row: `state: "skipped"`, `blocked_by` naming what prevented
  it, seated directly after that cause in the chronological report. The
  summary prints it (`skip build (blocked by lint)`), the `--json` envelope
  carries it (with `blocked_by`), and the exit code never takes it as the
  headline — the cause already owns that. `state` is an open set; consumers
  should tolerate values they don't know. `blocked_by` means prevention and
  nothing else: a `shared` row carries none — nothing blocked it, it was
  answered — and instead has its own `started`, the instant the request
  concluded, so it seats in the report where it actually happened; a
  request that waited on an execution that *failed* is blamed on it.
- **`queued_ms` — launch latency, on the row and in the envelope.** A node
  with prerequisites records when it became eligible (its last
  prerequisite's finish); `started - eligible` is how long it sat ready,
  waiting for a worker — reported as `queued_ms`, never folded into
  `duration_ms`, because the task wasn't running. Roots have no latency and
  no field. A `skipped` row still records no time at all, only its cause:
  a node that never launched never waited anywhere a clock runs.
- **Tasks wear their names on the thread, and the report says where they
  ran.** While a task executes, its worker thread is named after it —
  `fm:build`, badged `[serial]`/`[exclusive]` under a lane hold — so a
  sampling profiler's timeline (py-spy, viztracer, an OTel exporter reading
  thread names) shows tasks and lane occupancy instead of
  `ThreadPoolExecutor-0_1`; the name is restored afterwards, and a body call
  nests naturally. The pools themselves are named `fm-worker` and
  `fm-parallel`. Each executed row records `thread` (the worker's stable
  name) and `thread_id` (the OS id, `threading.get_native_id`) — the
  correlation keys a profiler dump uses — and both ride into the `--json`
  envelope. A row that executed nothing (a `shared` row, a refusal) records
  neither. Thread names are Python-level: samplers that read the
  interpreter see them; a native profiler attributing by OS thread name
  will not.
- **`@wrap_task` and `@wrap_bind` — the pair as one generator.** When the
  pre and the post are two halves of one thought, a wrapper says it in one
  place: locals instead of `task.state`, `try/finally` doing the pairing.
  `wrap_task` takes exactly one yield — pre half, `result = yield`, post
  half — enters at the `pre_task` moment and is resumed with the
  `ResultView`, so every pair rule is its rule too: per request (a `shared`
  row resumes it), reverse unwinding, a raising half failing the task,
  named. `wrap_bind` takes exactly two yields, enters at the bind boundary
  (`bound = yield` receives the bound arguments), and closes even when
  binding fails — the failure arrives raised at the first yield, where a
  `try/finally` observes it. The one asymmetry, stated plainly: `wrap_task`
  never sees a task that failed to bind (its anchor moment never fires);
  observing the bind boundary is what `wrap_bind` is for. Both desugar at
  registration into the same engine as the explicit hooks — one engine, two
  spellings — and yield-count violations are taught, naming the wrapper.
- **`@pre_bind` — the moment before parameters exist.** It fires before the
  task's parameters are bound, so what it writes into `task.env` is what
  `env()` fallbacks resolve, what coercion sees, and what `check(fn)`
  validators read — the one moment a plugin can influence what the body will
  be handed. A body call binds like a segment, so its binding sees the same
  injected environment. `task.args` is not readable here (nothing is bound
  yet — read values in `pre_task`); the same handle carries through the whole
  ladder, so state set at `pre_bind` is there at `post_task`. Binding happens
  per request while execution happens per work, so `pre_bind` may fire for a
  request whose row ends up `shared` — the whole ladder does, because the
  pair is per request and only the body is shared; a bind failure still
  fires the posts — the attempt concluded — with the refusal as the result.
- **The task's managed window opens before binding.** Hook code and the user
  code binding runs — `check(fn)` validators, custom constructors — now
  answer to the same rules a body does: an `os.environ` write is captured
  into the task's own overlay instead of leaking to parallel siblings, and a
  prompt outside an interactive task is refused. footman's own prompts —
  `ask()` questions and menus, `confirm=` — read the real terminal and are
  never caught by those guards, wherever they fire.
- **`@pre_task` / `@post_task` — the per-task lifecycle pair.** Where
  `@pre_tasks` runs once over the plan, this pair runs around every
  *execution* — a chain segment, a prerequisite, a fan-out member, a body
  call all count the same. `pre_task(inv, task)` fires after binding and
  reads the bound arguments (`task.args`, defaults included, read-only);
  `post_task(inv, task, result)` fires after the body, whatever the outcome.
  The `task` handle also carries `task.state` (scratch private to the plugin
  and the execution, delivered from pre to post), `task.env` (the task's own
  environment overlay — the one sanctioned lane for per-task env), and
  `task.source_hash` (the body digest, a tripwire, `None` when unreadable).
  `result` reads everything and writes one thing: `set_returned(value)`,
  which rewrites the *reported* value — the summary and `--json` — never
  what a dependent or a body caller received. Pres run in plugin order and
  posts unwind in reverse; the pair is **per request** — only the body is
  shared, so a request satisfied by an execution the run already performed
  still gets the whole ladder, closed with its `shared` row
  (`result.state`) — and the post is the task-finished event: once a
  request's ladder opened, it fires when the request concludes,
  irrespective of which pres a plugin registered or how they fared. A
  raising pre fails the task like a failed prerequisite, a raising post
  fails an otherwise-green task, and both failures name the plugin. Nothing
  fires under `--dry-run`. A `pre_task`'s return value is **reserved** for a
  future "supply the result, skip the body" power — today it is noted and
  ignored.
- **`registry.task_source_hash()` — a digest of a task's own body.** Normalised
  through the AST rather than taken over the text, so reformatting and comments
  do not move it while a real edit does; decorator lines count, so a changed
  `pre=` shows up. Deliberately a **tripwire, not an identity**: it covers the
  function's own source and nothing it calls, which makes it right for "warn me
  if the body moved and nobody said so" and wrong as a cache key. Exposed to
  hooks as `task.source_hash`.

- **A prime no longer leaves its downloads on the machine.** uv writes to two
  places of its own accord — a wheel cache, and the store holding the
  interpreters this machine actually runs — and priming CPython's releases put
  90 interpreters in that store and left them there, around 7 GB. Both now
  point inside the prime's scratch directory, so one cleanup removes every
  byte the walk caused and the python you develop against is never a candidate
  for deletion. Each release is also discarded as soon as its surface has been
  read, which is the difference between peak disk being one release and being
  all of them: a full prime of ruff would otherwise stand up 416 environments
  at once.
- **A refresh writes its own release note.** The events it found become a
  `### Changed` bullet per tool under `[Unreleased]`, naming the options added
  and dropped by their command-line spellings and counting the descriptions
  that merely moved — a release can reword half a dozen without changing what
  the tool accepts, and listing those turns a note into a diff dump. Per tool
  rather than per release, because a reader cares that prek gained `--glob`,
  not which patch carried it. Written into the file rather than printed: the
  job already edits `tool-history/` and the stubs and lands through a PR
  either way, so the note rides in the same diff.
- **`fm tools.refresh` — read every release published since the history was
  last updated, and say whether that warrants a release.** `prime` walks
  backwards to deepen a history; this walks forwards to catch one up,
  installing **every** release in between rather than jumping to the newest, so
  a flag that arrived in 0.16.1 is attributed to 0.16.1 and not to whatever
  was latest the day the job ran. A release that changed nothing still records
  an empty delta. `fm --json tools.refresh` returns what was read, which of
  those carried events, what could not be read, and whether a release is
  warranted — and the events are the CHANGELOG line.
- **CPython's releases can be primed into the option history.** The index is
  the provisioned uv's own — uv carries it inside the binary, so `fm
  tools.prime` gained a `--prefix` and drives the tiers from there: a stale uv
  reports a stale newest python and the walk starts too low without saying so.
- **The python stub tracks the newest CPython** instead of a pinned 3.13.

### Changed

- **A body call binds like a segment.** A parameter the caller leaves out now
  consults the same sources an absent option does — stdin, then its `env()`
  variable, then the default, with a defaultless `ask()` prompting as the last
  resort — so `build()` under `DEPLOY_ENV=prod` builds what `fm build` builds
  instead of silently taking the Python default. footman sees the call before
  Python fills in defaults, so omitting a parameter and passing the default's
  value explicitly are different requests: an explicit value wins over env,
  exactly as a command-line value does. Resolution happens before the work is
  keyed, so a segment, a prerequisite and a call that resolve to the same
  values are one piece of work. The ladder is consulted through a per-task
  plan built on the first call — a task never called from Python pays
  nothing. Outside a run, a call is still the plain function call it looks
  like.
- **Explicit call values run the annotation's validators.** Choices, bounds,
  path requirements and `check(fn)` now refuse at a body call exactly what
  they refuse on the command line — `scale(20)` against `between(1, 10)` is a
  taught error naming the call — because the annotation is the contract
  wherever the value comes from. Values are validated, never coerced: a
  Python caller passes real values under the signature's types, and the type
  checker polices those.

### Removed

- **BREAKING: `@finalize` is gone, replaced by `@pre_tasks`.** Removed
  outright rather than left as a refusing alias: the lifecycle has one name
  per moment, and a retired second name for the same moment is exactly the
  duplication the rest of this design keeps removing. The migration is
  mechanical — take `inv` instead of `tasks`, and read `inv.tasks` where the
  tree view used to arrive. It runs at the same moment, in the same cascade
  order, and can now also set the environment every task will see. Internally
  the readers for a task's declared prerequisites are `registry.pre_deps` /
  `post_deps`, leaving `pre_tasks` / `post_tasks` to name the lifecycle
  moments.

### Fixed

- **A prime could append a release newer than the one it was walking back
  from.** It asked whether a release predated the chain's floor by date, but a
  base carries the date it was *observed*, not the date it was published — so
  on a first prime the floor is dated today and every release ever published
  passes. Invisible while every base happened to be the newest release, and
  wrong the moment one is not, which is any stub synced from an outdated
  binary. The walk now positions the floor in the source's own ordering, and
  a floor that ordering cannot place stops the walk and says so.
- **An index that cannot be read is no longer an empty one.** Every network
  error, timeout and malformed body was swallowed into `{}`, so a throttled
  registry and a tool that had genuinely not released were the same answer.
  That is the one distinction a release job cannot afford: "is there anything
  new" answered "no" by a rate limit would end the job with "nothing to
  release", and a renamed package would answer it that way forever while the
  job kept reporting success.
- **A release chain is ordered by version, not by publication date.** Three
  curated tools keep more than one series alive at once — cmake 3.31.x beside
  4.x, pytest's 4.6 LTS beside 5.x, CPython's five — so the most recently
  published release is not the newest one. Ordered by date, a walk back from
  CPython 3.14.6 stepped to 3.13.14 and read every 3.14 option as dropped and
  then re-added, making every interval derived from it wrong. Pre-releases are
  also excluded from chains: an alpha is not something to say a flag arrived
  in, and two tools only *looked* like they shipped series in parallel because
  one sorted as though it were final.
- **A version the comparator cannot separate no longer moves the base.** Two
  builds of one base — eclint's `0.6.0-wk.3` against its `-wk.5` — compare
  equal, and "not older" was read as "newer", so a stale checkout could
  promote the older build and push the newer one down the chain. That is the
  rewrite the guard exists to refuse; it now declines and names the tool.
- **A vendored build tail no longer keeps a tool out of its own index.** PyPI
  ships `ninja` 1.13.0 and the binary in it answers
  `1.13.0.git.kitware.jobserver-pipe-1`, which matched no release, so ninja
  could not be primed at all.
- **The CPython listing no longer depends on what the machine has installed.**
  `uv python list` replaces a version's download entry with a local path once
  it is installed, dropping the URL its publication date is read from — so
  each primed release vanished from the index the next prime reads. It now
  asks for downloads only.

- **A `ctx`-declaring task now keys its body calls on the caller's
  arguments.** The work key bound a call's arguments against the declared
  signature, where the first positional value landed in the injected `ctx`
  slot and was then discarded — so `render("web")` and `render("api")` were
  the same key, and the second call wrongly shared the first's result. Calls
  now bind against the signature a Python caller actually sees, with the
  context parameter stripped.

## [0.23.0] — 2026-07-27

### Added

- **The stubs are rendered from a record, not from a reading.** Each curated
  tool gets a file under `tool-history/` holding what it accepted, release by
  release: the newest observed release stored whole, and — once the fetchers
  land — every older one as a delta describing how to step back to it.
  `fm tools.sync` now records its reading there first and renders the stub
  from *that*, so what ships is a view of the record rather than a second
  record that can disagree with it. All 26 stubs regenerate byte-identical
  through the round-trip, which is the proof the store loses nothing.

  Deltas point **backwards** because that is the shape of the work: priming
  older releases is pure append, the current version costs no replay (it is
  the base), midfill rewrites exactly one entry, and "did this release change
  anything" is "is its delta non-empty" — the question a release job asks,
  answered without comparing surfaces. An empty delta means *observed and
  unchanged*, which is not the same as a release nobody looked at; those are
  simply absent.

  The store is tracked but **not shipped**: it lives outside `src/`, so no
  install pays for history nobody reads. Generation is a maintainer task run
  from a checkout, and users read the stubs, which already carry everything
  the log is for.

- **`fm tools.prime` reads a tool's past releases into its history.** Walks
  backwards from the release the history already holds, installing one
  version at a time into a throwaway environment and appending a delta for
  each — so nothing already written is touched, and a release the chain
  already has is skipped. A prime stopped by a rate limit is resumed by
  running it again. `--count` is how far back to reach *this* run, and the
  floor a tool actually reached is recorded as `observed_from`: an option
  present in the oldest release read is "at or before" that version, never
  "since" it.

  Releases are ordered by publication date, with the version breaking a
  same-day tie — prek shipped 0.4.7 and 0.4.8 on one day, and a tie resolved
  by index order let the walk skip one and a later run append it *below* its
  own successor. Only the PyPI tier can be listed today; every other tool is
  named and skipped rather than left looking like a tool with no history.

### Changed

- **BREAKING: calling a task from a task body is part of the run.** It used to
  be a plain function call: it ran the task again even if the run had just run
  it, on the caller's thread, with no context of its own and no result anywhere
  — the one execution path footman could not see. A call now gets a real task
  boundary (its own context and working directory, its `@requires` and
  `confirm` gates, its own entry in the report), and the run performs a task's
  work **once per task and arguments**, whoever asks. So a prerequisite you
  also call hands back what it already produced, which is how a task finally
  reads a value `pre=` cannot pass:

  ```python
  @task(pre=[build])
  def publish():
      artifact = build()      # the build that already ran, not a second one
  ```

  Whether a task was reached by declaration or by a call makes no difference to
  how often it runs. Calling a task that is running on another thread waits for
  that run; a call that could never return (a task calling itself, or two
  calling each other) is refused by name instead of hanging, where
  self-recursion used to be a stack overflow. Two calls are refused outright: a
  `serial=`/`exclusive=` task, whose lane is taken at the task boundary and
  never mid-body, and an `infinite` task. A call outside a run is still the
  plain call it looks like, so importing a tasks file and calling a function
  keeps working. An `int` return remains a segment's exit code, while a call
  gets the number as a value.

- **`@task(shared=False)` — work that is never shared.** Some work exists to
  happen again: a notification, a timestamp, a scratch clean. Every request for
  such a task runs, whether that request is a call, a chain segment, or a
  `pre=` edge — one rule, so the spelling never changes the answer. Sharing is
  a property of the request: `.opts(shared=…)` first, then the task's
  declaration, then whatever asked for it, then shared. It propagates down the
  dependency subtree, because an unshared request asks unshared for what it
  needs, so `shared=True` is the pin for a step that genuinely is reusable. An
  unshared run never rewrites what the run already remembers — the first result
  stands.

- **BREAKING: a run performs a task's work once per request, however the task
  was asked for.** Two tasks declaring `pre=[build]` shared one build already;
  now a chain segment, a body call and a prerequisite all count the same, so
  `fm check check` runs `check` once and reports the second mention as
  `shared`. Repeating a task in a chain used to run it once per mention. What
  still runs twice: different arguments (`fm build web build api`), a different
  policy (`pre=[build.opts(atomic=True)]` is a different invocation), and
  anything declared `shared=False`.

  An unshared execution is nobody else's answer: it neither reuses a result nor
  becomes one. Otherwise whether a shared request reused would depend on which
  of two nodes the scheduler happened to start first, and how much work a run
  performs would stop being predictable.

- **A request the run had already satisfied is reported as `shared`.** A body
  call that finds its work already done returns the memoised value — and used
  to leave no trace, so the second request simply vanished from the report. It
  now appears, marked `shared`: dimmed in the summary with "already run this
  run" where a duration would be, and carrying `state: "shared"` in the
  `--json` envelope. It never began, so it has no start of its own and the
  ordering rule places it directly after the execution that satisfied it; it
  is `ok`, since the work succeeded just earlier, so the run's exit code is
  untouched.

  `state` is the reported spelling of what happened — `ok` / `failed` /
  `cancelled` / `shared` — resolved in one place. `ok` and `code` stay the
  exit-code channel, so a future outcome is another value of `state` rather
  than another boolean beside it. Sharing within a run and caching across runs
  are two axes, and each keeps one word.

- **The run's report reads in the order the run happened.** A dependency
  listing has no place for a task reached by a call; a chronological one does.
  Sequential runs are unchanged, since dependency order already is
  chronological, and a prerequisite still precedes its dependent; only
  independent tasks in a parallel run move, to wherever they actually ran.
  Anything that never began sits directly after whatever prevented it, so the
  report reads as cause then consequence. `TaskResult` carries `started` and
  `blocked_by` for this. A task reached by reference rather than typed is now
  reported by its *address* (`import_` shows as `import`) — the spelling you
  could actually type.

### Removed

- **BREAKING: a bare callable in `Annotated` is no longer a completer.** It
  used to mean `suggest(fn)`, which read as a convenience and behaved as a
  guess: the branch swallowed *anything* callable that was not a class, so a
  marker of the wrong shape — a plain function, or an instance with
  `__call__` — silently became a shell-completion function. The marker
  vanished, a mystery completer appeared, and nothing said a word either
  way. `suggest(fn)` is now the only spelling, the way one spelling per
  concept goes everywhere else in footman.

  The bare form is **refused**, not ignored, because it used to work:
  `Annotated[str, my_fn]` teaches `suggest(my_fn)`. Unrecognised metadata
  that is *not* callable is still passed over in silence, which is what lets
  a parameter carry a marker footman has never heard of.

### Fixed

- **A called task passes the same gates a declared one does.** A body call used
  to slip past `@requires` availability (an unavailable task *ran* when called,
  though the same task as a prerequisite refuses) and never asked a
  `@task(confirm=)` gate. Both now hold however the task was reached; the
  confirm is asked at the call, since a call cannot be known before the run the
  way a chain segment can.

## [0.22.0] — 2026-07-27

### Changed

- **BREAKING: a value is always `=`-attached.** Every option value — long
  options, short aliases, globals, task options — attaches with `=`:
  `fm build --target=prod`, `fm -j=4 check`, `fm --color=never lint`. A
  bare `--x`/`-x` is a flag, a bare word is a task or a positional, so a
  chain reads token by token with no arity table. The space form refuses
  with the fix spelled out — `fm build --target prod` answers "did you
  mean `--target=prod`?", never "unknown task 'prod'" — and a bare
  value-bearing option teaches the shape (`--jobs expects a value,
  attached: --jobs=N`). Values that start with a dash now just work
  (`--jobs=-1`); lists repeat or comma-join as before (`--tag=a --tag=b`,
  `--tag=a,b`); dict pairs read better anchored on their first `=`
  (`--env=DEBUG=1`). The completion-installer trio keeps its optional
  value (`--install-completion` detects the shell; `=zsh` names one), and
  `--help` renders every option in the attached spelling. Both the
  splitter and the completion hot path drop their value-consumption
  states — the whole option grammar is lexical now. The `tools.*` bridge
  is untouched: it renders each child tool's argv in that tool's own
  dialect, and everything after `--` stays opaque passthrough.

### Added

- **`Secret.reveal()` — deliberate exposure, said out loud.** A task whose
  *job* is to print a credential (an `export …` line for `eval "$(fm
  env-export)"`) needs the value to leave, and a `Secret` inside a
  structured document (`Stdout[dict]`, the `--json` envelope) redacts to
  `***`. `reveal()` returns the plain `str`, so the intent reads at the
  point of use and every deliberate exposure in a codebase is one `grep`
  away — an audit surface a run-wide "don't redact" flag could never give.
  Formatting was never redacted and still isn't: string operations on a
  `Secret` yield a plain `str`, which is what makes `f"export
  TOKEN={token}"` work with no switch to disarm protection elsewhere.

### Fixed

- **A subcommand group is a nested class, and the tool reference shows it.**
  `docker compose up` was `DockerCompose` sitting *beside* `Docker` — a name
  invented by flattening — and a reference page named only the root class, so
  `docker compose up`, `uv pip install`, `gh pr create` and every flag they
  take were described nowhere at all: **48 callables and 641 options**, a fifth
  of the whole stubbed surface. Groups now generate as `Docker.Compose`,
  `Uv.Pip`, `Gh.Pr`, which the docs renderer walks on its own — one directive
  documents a tool whatever its shape, and two tools can no longer collide over
  an invented name. `flags()` returns `Self` rather than its class by name,
  since a nested class cannot refer to itself from inside its own body and
  `Self` is what the chain meant anyway.

  Two things fell out of the rename. footman's own `Tool` and `Result` are
  imported privately in generated stubs (`Tool as _Tool`), because `uv tool`
  would otherwise write `class Tool(Tool)` — a class deriving from itself; a
  verb can never produce a leading underscore, so the collision is gone rather
  than dodged. And docstring wrapping now knows its nesting depth, so a group's
  help text stays inside the line limit instead of pushing four characters past
  it.

  The index table had the same blind spot from the other side: it flattened
  nested verbs to bare names, so `compose.up` read as `up` and uv's two
  `install` verbs collapsed into one row. Verbs are listed dotted now, as they
  are called, and footman's own `flags()` accessor stops posing as a verb of
  the tool.

- **One version parser, and `installed_version()` answers about the binary it
  runs.** The bridge and the stub extractor each had their own regex, and the
  bridge's read `PATH` while the extractor resolves differently (a Homebrew
  keg for host-read tools), so on macOS `git` reported 2.55.0 to one and
  2.50.1 to the other. The parsing is now shared — only the *choice of
  binary* differs, deliberately — and the bridge asks the executable it will
  actually invoke, so `tools.python.installed_version()` reports the running
  interpreter rather than whatever `python` happens to be on `PATH`. A stub's
  recorded version was never the same question, and the docstring now says so.

- **One comparator too: a build tail can't read as newer than its own base.**
  `installed_version()` scraped every digit it found, so `eclint 0.6.0-wk.5`
  compared as `(0, 6, 0, 5)` — *after* the 0.6.0 it is a fork build of, which
  is backwards under every version grammar — and ninja's
  `1.13.0.git.kitware.jobserver-pipe-1` picked up a stray `1`. Both now
  compare as their base, `(0, 6, 0)` and `(1, 13, 0)`, which is the honest
  answer to the only question the tuple is asked: is the CLI new enough. The
  snapshot guard's own copy of that logic is gone; `tools.version_tuple` is
  the one comparator, beside `tools.read_version`, and the exact string
  survives for anything that records *which* build was read.

- **`cmd.installed_version()` works.** Windows `cmd` has no `--version`; it
  spells it `cmd /c ver`. `Tool(…, version_argv=…)` makes that declarable
  rather than a special case nobody could reach, and it rides a chain like
  every other construction fact.

- **`prompt(secret=True)` returns a `Secret`.** It hid the typing and then
  handed back a bare `str`, so a mid-task secret was fully printable in the
  next traceback or `--json` payload — while the identical-looking
  `ask(secret=True)` redacted. Hiding a value while it is typed and then
  printing it at the first error is a strange kind of secret. An unattended
  default is wrapped too: where the value came from doesn't change what it
  is.

### Docs

- The input guide gains a **Secrets** section, covering the two halves
  (`secret=True` collects, `Secret` displays), what redaction deliberately
  does *not* cover (the bytes a task writes on purpose), and the honest
  flip side — a secret f-stringed into a log message loses its redaction
  the same way, because nothing can tell the two apart.

## [0.21.0] — 2026-07-26

- Tool stubs retaken at release, per the audit: **uv, prek, djlint** had
  released newer versions than the snapshots recorded. A snapshot only
  ever moves forward.

### Added

- **`stdin` — a parameter bound from the pipe.** footman is a pipe target:
  `git diff | fm review` and `fm review < changes.patch` both bind the
  stream to a typed parameter, with no flag to remember. The annotation
  decides the interpretation — `Annotated[str, stdin]` (or `Stdin[str]`)
  reads the stream as UTF-8 text, `bytes` reads it raw, `stdin("field")`
  reads one top-level key of a JSON document, and `stdin(lines=True)` binds
  a list one line per element, each line coerced exactly like a CLI token
  (`list[int]`, `list[Path]`). Precedence is CLI > stdin > env > default >
  prompt, so an explicit option always wins and one signature serves both
  spellings. The stream is read once, fully, at the boundary and shared by
  every parameter in the run that asks — task bodies still never touch
  stdin, so bound tasks stay fully parallel with no `interactive=True`. A
  terminal means "not provided": a defaulted parameter falls back, a
  required one refuses with a taught message, and nothing ever blocks on a
  read. `--help` says what a parameter reads; `Runner.invoke` grew a
  `stdin=` argument so tests pipe without touching the real stream.

- **`Stdout[T]` — the return annotation that owns stdout.** A task declares
  that its return value is the document on stdout, in the signature:
  `def status() -> Stdout[dict]` makes `fm status | jq .` work with no flag
  at any call site — a filter the way `sort` and `jq` are filters. The
  return type decides the bytes, mirroring `stdin`: `Stdout[str]` verbatim
  plus a trailing newline, `Stdout[bytes]` raw, anything structured JSON —
  pretty-printed at a terminal, one compact line into a pipe, dataclasses
  and `Secret` redaction handled by the same encoder `--json` uses. The
  rules: `--json` wins (the document rides in `results[].returned`); only
  the addressed task emits (a declaring `pre=`/`post=` dependency or
  fan-out member is suppressed, not refused); two declaring tasks in one
  chain is a plan-time refusal; `None` means empty stdout, exit 0;
  `Stdout[int]` makes the number the document (a bare `-> int` stays the
  exit-code channel); a failed task emits nothing; everything that is not
  the document replays on stderr, so `fm status > out.json` captures
  exactly the document. `Stdout[T]` + `interactive=True` is a taught
  declaration-time error, and a body call is unaffected.

- **The document binder: JSON on stdin into typed shapes.** A parameter
  annotated with a dataclass, `dict`, or `list` and marked `stdin` binds
  the whole JSON document: nested dataclasses recurse (no dotted field
  paths — `event.tool_input.file_path` is just attribute access), `list[T]`
  and `T | None` recurse too, and scalar leaves run the same coercion a
  CLI token gets, so `Path`, `Literal`, enums and `datetime` behave as they
  do on the command line. Unknown keys are ignored, never refused — a
  producer may grow fields without breaking a consumer. Missing keys follow
  the dataclass: a field with a default is optional, a defaultless absent
  one is a taught refusal. Every refusal names the exact JSON path
  (`event.tool_input.file_path: expected text, got a number`). A
  dataclass parameter is boundary-only — not a flag, not a positional; the
  pipe is its only source, and `fm task < fixture.json` replays the real
  parse. A bare-marker `list` parameter reads a JSON array; a `dict`
  parameter reads a JSON object.

- **`[tool.footman] sort = true` — alphabetical listings.** One boolean
  orders every human-facing walk of the tree: `--list`, `--tree`, help,
  the `--json` catalog, and the generated docs pages. A `--sort` global
  does the same for a single invocation. The default stays definition
  order — a tasks file is an authored page, and its order is the
  author's (the same order the composition rules preserve). Tasks still
  list before groups at every level, and the setting is presentation
  only: what runs, and in what order, never follows it.

- **`@task(hidden=True)` — listed nowhere, callable as ever.** For the tasks
  a machine calls and a human never types: a CI entry point, a step another
  task drives. The task drops out of `--list`, `--tree`, group help, the
  did-you-mean index and completion, and nothing else changes — `fm <name>`
  runs it, a `pre=`/`post=` dependency runs it, a runnable group's
  empty-body fan-out still includes it. It is presentation, not policy.
  `--json` reports it **marked** rather than missing, because a machine is
  exactly who calls it, and the generated task docs badge it, because the
  docs are where you look up what the listings won't offer.

  `hidden` is inherited: unset means "whatever my group said", so
  `group("internal", hidden=True)` (or a `hidden=True` on the group's
  `@group.default`) hides a whole subtree in one word, and a child can say
  `hidden=False` to come back. A group with nothing listed under it prints
  no heading at all. `TaskView.hidden` reads the declaration — `None` when
  it inherits — so a `@finalize` hook can tell an override from silence.

  This is a third thing, next to the two that already existed, and the docs
  now separate them: **hidden** is listed nowhere but callable; a plain
  `if` around the decorator **omits** the task entirely (no address at all);
  `@requires…` **lists it with a reason** it can't run here.

- **A stub snapshot only ever moves forward.** Two readings are worth less
  than the file already checked in, and both are now named and left alone
  rather than treated as drift: a tool **missing from a `--prefix`** (a
  partial provision would otherwise fall through to the host's copy, quietly
  turning a failed fetch into "the tools moved"), and one whose version is
  **older than the stub records** (a machine behind the one that took the
  snapshot has nothing to add, and reading it would rewrite the stub
  backwards, dropping flags that exist upstream). Neither counts as behind —
  they are unanswered, and `sync` leaves those files untouched. Only the
  `system` tier (git, docker) is meant to come from the host. On a laptop
  that trails the snapshots, `tools.audit` went from reporting eight tools
  as drifted to two.

- **`--prefix` on `tools.sync` and `tools.audit`.** Both can now read the
  binaries from a `fm tools.provision` directory instead of the machine
  they run on — the question a scheduled check actually wants to ask
  ("have the tools released anything since our snapshot?") rather than the
  one the local PATH answers ("is this laptop's ruff the one we read?").
  `color` already took `--prefix`; the three now share one helper that
  scopes the overlay to the task through `ctx.env`, with a bare-call
  fallback for callers outside a run.

### Changed

- **BREAKING: refusals exit 64 (`EX_USAGE`), not 2.** Exit 2 used to mean
  four different things — an unknown task or flag, a value that would not
  coerce, a task saying `fail(code=2)`, and a `run()` subprocess exiting 2 —
  so a caller could not tell a broken invocation from a real verdict, and a
  harness reading 2 as "blocking error" acted on refusals as if they were
  results. Now every refusal — parse, binding, tasks-file, config,
  availability, `--where`, the completion installers — exits 64, on the
  process and inside the `--json` envelope (`error.code`, `results[].code`)
  alike. Interrupt stays 130. The low codes belong to tasks again: exit 2
  from a task now means exactly what the task said. Anyone keying on 2 for
  footman's own errors must key on 64; hook recipes that flattened every
  failure to one code now pass 64 through untouched (`docs/agents.md` shows
  the shape).

- **`--tree` draws a tree.** It used to print every task at its full dotted
  address under an indented group header, which made it the `--list` output
  with worse alignment. It now draws branches (`├─`, `└─`, `│`) and names
  each leaf once, so the shape is the point; `--list` remains the flat view
  where every row is a copy-paste-runnable address. Both now read one
  traversal (`_describe.walk`), so a rule about what is listed cannot be
  true of one and false of the other — which is what let `hidden` reach
  both by construction.

- **footman's own tasks overlay the tree — no container at all.** The
  first-party plugins are pulled at the end of footman's own `tasks.py`,
  so each node merges with what the file already defined: the docs tasks
  join the local `docs` group leaf by leaf (`docs.serve` beside
  `docs.page`), and `tools.…` lands at top level — one surface, no
  `footman.` prefix (entry-point names unchanged; your
  `plugin("footman.tools", into=…)` still mounts wherever *you* say).
  Merging is order-independent for any plugin, not just ours: a pull
  composes with a group the file already defined, and a `group()` defined
  *after* a pull now adopts the pulled group instead of replacing it —
  either way you get the union, with local tasks winning name clashes;
  order only decides listing order. The
  documenter's self-exclusion now keys on per-task provenance instead of
  a hardcoded mount name, so it excludes exactly the pulled tasks
  wherever an author mounts them — the author's own tasks on a shared
  group stay documented. The tools index also stops saying "unknown" for
  the shells' in-process capability: a hand-written stub means there is
  no entry point to call, so the answer is "no". `--list` wraps long descriptions with a hanging
  indent to the description column, so a narrow terminal no longer
  shears the two-column layout apart. And the custom-CLI page says what
  was always true: the uv handoff re-execs the *(branded)* footman you
  invoked — `acme` hands off to the project's own `acme`, never `fm`.

- **BREAKING: a stub that is behind is news, not a failure.** A stub records
  what one tool accepted at the version it was read from, and footman
  promises no particular speed at retaking that snapshot — so
  `fm tools.audit` finding a newer release upstream never meant anything was
  wrong, while its exit code and its wording ("differ from the installed
  tool … run `fm tools.sync` to update") both said otherwise. It now reports
  and exits zero: *"3 tool(s) have released a newer version than the stub
  snapshot … nothing is broken — the bridge speaks flags the stub hasn't
  heard of."* `--strict` restores a non-zero exit for automation that needs
  something to trip on, and the task returns its findings
  (`{"checked", "behind", "skipped", "resnapshotted"}`), so
  `fm --json tools.audit` hands a scheduled job the list to act on.

  One finding keeps failing unconditionally: a disagreement in footman's
  negation or wrapper tables. Those are read by the *runtime*, so a mismatch
  means a task emits the wrong command today — a defect, not a release
  someone else shipped.

- **BREAKING: `fm tools.list --missing` is now `--show missing`.** Listing
  all tools, only the installed ones, or only the missing ones is one
  question with three answers, so it is one parameter:
  `show: Literal["all", "installed", "missing"]`, defaulting to `all`. The
  boolean it replaces could only say two of the three, and adding a second
  boolean would have made `--missing --installed` a representable request
  that silently lists nothing.

### Fixed

- **A background manifest rebuild no longer loses a race with the TAB that
  spawned it (Windows).** Windows refuses to replace a file another process
  holds open, and a reader holding it open is the design: completion polls
  the cached manifest every 30 ms while the detached refresh rewrites it.
  The `os.replace` failed, the child swallowed the error (a background
  refresh must never crash or print), and the rebuild silently never landed
  — leaving the stale answer in place until some later write found a quiet
  moment. `write_manifest` now retries for about a second, and cleans up its
  temp file if it does give up rather than littering the cache directory.

- **pytest is reported as in-process by default, because it is.** `tools.py`
  builds it in-process (through `pytest.main`), but its driver never said so,
  so the two places that *label* the mode read the detected capability
  instead: `fm tools.list` showed pytest as `capable` and its stub header as
  `In-process: available` rather than `default`. A parity test now pins every
  driver's `name`/`in_process` to how `tools.py` builds that tool, so the
  label can't drift from the runtime again.

### Docs

- footman's own agent hooks are footman tasks now: `.claude/settings.json`
  runs `fm hooks.post-edit` and `fm hooks.stop` (a hidden group in
  `tasks.py`) instead of shell one-liners. The payload arrives as a typed
  dataclass from stdin — the `jq` host dependency is gone — the loop guard
  is a field read, and the exit contract is exact: 0 quiet, 2 a blocking
  verdict with receipts on stderr, 64 passing through so a broken hook
  line reaches the human, never the model.

- Corrected the tools-bridge page and its code comments where they still said
  a zero-argument `main()` serialises — the argv router ended that. pytest's
  dedicated `pytest.main` path is kept for the reason that still stands: its
  console entry is the private `_console_main`, which on a broken pipe points
  the process's real stdout at `/dev/null` — harmless in a subprocess,
  blinding in footman's, where it would take every task running beside it
  with it.

## [0.20.0] — 2026-07-25

### Added

- **`Arg[T]` — the optional trailing positional.** `def files(pattern:
  Arg[str] = "*")` makes `fm files src` fill the positional and a bare
  `fm files` run on the default. The grammar stays deterministic and
  greedy: a following bare word *is* the value — never re-interpreted as
  the next task, no name-peeking — and capped at one token. To run
  argument-less ahead of another task, say so with the explicit boundary:
  `fm files + build` — and completion offers the `+` right at the optional
  slot, so the boundary documents itself. An `Arg` needs a default, takes
  at most one token, and must trail every required positional — each rule
  a taught error.

- **Live `suggest` completers drive the prompt.** A defaultless `ask()`
  parameter carrying a *strict* completer now asks with a numbered menu of
  the completer's fresh values — and `Many[...]` makes it a multi-select
  (numbers, `all`, `none`) — instead of free text. A best-effort completer
  (`strict=False`) shows its values as a hint and leaves the answer free.
  Bad picks re-ask; answers ride the same coercion pipeline as CLI values.
- **Secrets redact.** Answers to `ask(secret=True)` arrive as
  **`footman.Secret`** — a real `str` for the body, but its repr (logs,
  tracebacks, debuggers) is `Secret('***')`, the `--json` envelope
  serialises it as `***` (containers are walked), and a secret parameter
  never publishes values into the completion manifest (no baked choices,
  its completer never runs there).
- **Prompt hardening.** Stdin closing mid-prompt (Ctrl-D, piped input
  running out) is a taught error instead of an infinite re-ask; everything
  a prompt echoes back — including your own mistyped input — is scrubbed
  of control characters, closing the terminal-injection class `select()`
  already guarded against.

- **A generated "Errors & notes" reference page.** Everything footman can
  say when it refuses, warns, or teaches — every message-bearing error and
  every teach-once note — extracted from the source itself on each docs
  build (runtime holes shown as ⟨placeholders⟩), grouped by module, listed
  under Reference. Generated, not transcribed, so it can never drift from
  what the runner actually says.

- **The argv router — the last hidden serialisation point is gone.** A
  legacy zero-argument `main()` that reads `sys.argv` used to take a
  process-wide lock while footman patched the global around it; inside a
  run, `sys.argv` is now a per-call view served by a router (the same move
  the environment router makes for `os.environ`), so those entries
  parallelise exactly like their argument-accepting siblings. Nothing in
  the in-process path serialises any more — the parallel regime's claim
  ("the only non-parallel execution is declared") now holds with no
  asterisk. Outside a run the classic patch remains as the bare-call
  fallback; a C extension reading the list storage directly, or code
  *reassigning* `sys.argv` wholesale, degrades to the old behaviour.

- **Dotted cherry-picking in `only=`/`exclude=`.** The last surface of
  one-spelling-everywhere: filters take full dotted addresses relative to
  the pulled node — `plugin("acme.shared", only=["docs.build", "fmt"])`
  grafts one nested task and one flat one, materialising the path with
  the source's own group copies. Matching is exact (the whole-group
  spelling is the glob); unions are redundant, not errors; a group pruned
  empty is dropped, never grafted as a shell; validation is per-segment
  ("no task or group at 'docs.buidl' (docs has: build, serve)"). And
  because the default is the child named `default`, default-ness survives
  only if the default survives: `only=["lint.python"]` grafts a
  default-less `lint`, `only=["lint.default"]` grafts just the default,
  `exclude=["lint.default"]` everything but it.
- **`fm --plugins`** lists the installed `footman.tasks` entry points,
  marked pulled-or-not and where each landed — "installed but nobody
  pulled it" becomes visible. Descriptions are two-tier: a pulled plugin
  shows its landed tree's own help; an unpulled one shows its
  distribution's Summary (entry-point records can't carry a description),
  read from metadata with zero imports. A container module's docstring
  becomes its root help.
- **Group defaults take positionals.** The `@group.default` no-positional
  rule existed only because `fm lint foo` used to be ambiguous; dotted
  addressing dissolved the ambiguity (the subtask is `fm lint.foo`), so
  the rule goes with it: the default's signature is now the group's whole
  CLI surface — `fm lint src/` hands `src/` to the default like any task
  argument. The grammar stays deterministic (a positional wins) but never
  silent: a value that exactly names a child gets a one-line stderr note
  with the dotted spelling, an edit-distance-1 near miss (`fm lint
  markdwon`, which used to error and would now filter on nothing) names
  the nearest subtask, and a path-shaped value (`fm lint ./markdown`) is
  the documented quiet spelling.
- **Runnable groups are listed and described.** `--list`, group help, and
  the did-you-mean index now carry the bare-group spelling as its own
  row, described by the default's docstring — or,
  when the default has none, by generated text that says what it does:
  "run every task in this group" for an empty body, "run this group's
  default action" for a custom one.
- **Completion grows the other half of the `cd` idiom — two generosities,
  both completion-only** (the runtime resolver stays strict, so scripts
  cannot rot). *Segment-wise abbreviation*: each typed segment
  prefix-matches its own tree level, `fm f.t.sy⇥` → `footman.tools.sync`,
  the way zsh expands `/u/l/b` — and because footman generates the
  candidates itself, every shell gets it. An ambiguous segment expands up
  to itself and lists that level's matches. *Leaf-name fallback*: a token
  matching no top-level name completes against last segments instead
  (`fm serve⇥` → `docs.serve`) — the rescue for "I know the task, not
  where it lives", gated on zero top-level matches so it can never pollute
  a first tab or a valid descent.
- **The transition TAB is crash-proof.** The completion hot path now
  checks the manifest's `schema` before walking it: a cache baked by a
  different footman routes into the existing cold build (which also
  refuses to serve the stale file mid-rebuild), so the first TAB after an
  upgrade serves correct candidates instead of a traceback. A drift test
  pins the hot path's literal to `manifest.SCHEMA_VERSION`.
- **A "Shell differences" docs page.** The path-style completion model is
  documented shell-neutrally; the observable per-shell differences
  (description columns, menus, the space after a unique match) now have
  one advanced page, and the five-shell functional tests drive a dotted
  descent through real bash/zsh/fish/pwsh/nushell.

- **Questions front-load: asked one at a time, run in parallel, as early as
  correct.**
  Every promptable `ask()` parameter across the whole run answers *up
  front*, right after the `confirm=` gates and before anything executes —
  you answer once and walk away, and think-time can never land inside a
  task's recorded duration. Only a question carrying a live `suggest`
  completer waits (its menu may need a prerequisite's output) and resolves
  at its task's launch, as before. A required question with no way to ask
  (`--no-input`, no terminal) now refuses the whole run before anything
  starts, naming the flag — instead of failing one task mid-run. Accepted
  answers flow through the same coercion pipeline as command-line values.

- **The cascade walk is configurable.** A user-level **`cascade`** key —
  `none` (the current directory's own files only), `repo` (the `.git`
  ceiling, the default and today's behaviour), or `filesystem` (past
  repository boundaries, up to the filesystem root) — decides how far
  discovery ranges, and task files and config follow the same walk. The
  key is user-level-only (what sits above a repo is the machine owner's
  layout, not any project's business; a project file setting it is
  stripped with a `-v` advisory), and a new **`FOOTMAN_CASCADE`**
  environment variable overrides it per invocation
  (`FOOTMAN_CASCADE=none fm test` in CI). An unknown value is a taught
  error naming the three modes, never a silent default.
- **The working directory is a policy, not an accident.** A task's cwd is
  resolved once, before the body runs, by a ladder — `.opts(cwd=)` per use,
  `@task(cwd=, rel=)` per definition, `[tool.footman] cwd` as the run-wide
  default — from four tokens or an absolute path: `taskfile` (the directory
  of the file defining the task — the default, today's implicit behaviour,
  now named), `root` (the highest cascade file's directory), `asinvoked` (a
  pinned snapshot of the launch directory), and `unmanaged` (footman stays
  out: children spawn from the live process cwd). `rel=` appends a relative
  suffix to the resolved base — a nearer `rel` replaces a farther one — and
  `ctx.cwd` is always concrete inside a run. Per call, `run()` gains
  `rel=` beside `cwd=`, so `run("npm run build", rel="web")` roots one
  command in a subdirectory of the task's cwd. Relative `cwd=`, absolute
  `rel=`, and `rel=` under `unmanaged` are taught errors. `.opts(cwd=)`
  works on direct body-calls too, and two uses of one task at different
  cwds are two DAG nodes, never silently merged.

- **`footman.fail(reason, code=1)` — a blessed way to fail a task.** A function
  (not a `raise`) that stops the current task with a reason: the reason renders
  verbatim on the failure line and in the `--json` `error` field, and
  `fail("…", code=3)` sets the exit code too. It is a function on purpose — a
  task lives in your repo under your linter, and a call trips no flake8-errmsg
  (`EM101`) or tryceratops (`TRY003`), where `raise SomeError("literal")` would;
  the same reason `sys.exit()` and `pytest.fail()` are functions. The exception
  it raises, **`footman.Failed`**, is exported for `except footman.Failed:`. The
  stdlib idioms (`return N`, `sys.exit(...)`, raising) still work unchanged.
- **Colour survives footman's no-PTY boundary.** footman spawns tools over
  pipes, not a pseudo-terminal, so a tool sees a non-terminal and turns colour
  off — even when footman itself is on a terminal. footman now forces it back:
  every subprocess gets `FORCE_COLOR`/`CLICOLOR_FORCE` when the run is colourful
  and `NO_COLOR` when it is not, and the captured bytes replay onto footman's
  own terminal (colour is position-independent, so it survives the round-trip;
  live cursor control is the thing a PTY-less run genuinely can't carry, and it
  isn't attempted). One run-wide decision drives all of it, resolved from a
  new **`--color=always|never|auto`** global (`--no-color` is the `never`
  alias), **`[tool.footman] color`**, then `NO_COLOR`/`FORCE_COLOR` in the
  environment. `always` colours even into a pipe (for `less -R`); a captured
  `--json` run stays byte-clean. The few tools that ignore the environment and
  take a flag instead — git's `-c color.ui=always` — are forced through their
  own switch, injected into the executed command only, so `recording()` and
  `--dry-run` still show the tool's own call while `--verbose` shows what ran.
  Which tools obey the environment and which need a switch is *probed*, not
  guessed: **`fm footman tools color`** runs each tool with colour forced on and
  off and reads the bytes, categorising every direction `env`/`flag`/`none`, and
  regenerates the `_colordata.py` the forcing table loads. Forcing colour *off*
  is the absence of `FORCE_COLOR`, not `FORCE_COLOR=0` — some tools (ruff) read
  the mere presence of `FORCE_COLOR` as "on", so `--no-color` and piped runs
  clear it rather than setting `"0"`.

- **`run(shell=…)` runs a command string through an explicit shell.** `run()`
  stays shell-free by default — a string is split, no shell, so `|`/`>`/`$VAR`
  are literal — but `run("a | b", shell=True)` runs the whole string through a
  resolved interpreter, so pipes, redirects, globs, and variables work.
  `shell=True` follows the project's shell policy (`[shell] default`: `posix`
  by default — bash, then plain sh, git bash on Windows and Homebrew bash on
  macOS; `native`; `pwsh`; or a concrete shell); a string names a concrete
  shell (`bash`/`zsh`/`sh`/`fish`/`nu`/`pwsh`/`cmd`) or a strategy. A missing or
  wrong-platform shell is a taught error, and a shell-free `run("a | b")` now
  teaches instead of passing the operator as a literal argument. Two flags
  harden a run: **`strict=True`** fails on the first error and a masked pipe
  stage (`set -eo pipefail` for bash/zsh; `set -e` with a note on plain sh;
  `$ErrorActionPreference='Stop'` for pwsh; a taught error where there is no
  errexit), and **`clean=True`** runs the interpreter without the user's
  startup files, so a task's shell behaves the same on every machine. On
  Windows, the shown/`--verbose` command line now quotes the way cmd and
  PowerShell can read (stdlib `subprocess.list2cmdline`). The curated shell
  tools gain a sixth, **`tools.cmd`** (`cmd /c …`), so all of
  `bash`/`zsh`/`fish`/`pwsh`/`nu`/`cmd` read consistently.

- **`run()` returns a `Result`.** `run()` — and every `tools.*` call — now
  returns a `Result` instead of a bare exit code. A `Result` *is* the exit code
  (it subclasses `int`, so `code = run(...)`, `if run(...)`, and `== 0` are all
  unchanged), and it also carries the captured output split by stream, so
  `run("git rev-parse HEAD").stdout.strip()` reads the hash without stderr
  glued on. `.stdout` and `.stderr` are separated for both subprocess and
  in-process tools, and `.ok`, `.command`, `.raw`, and `.duration` round it
  out. The typed tool stubs return `Result` too, so `.stdout` is checked at the
  call site.
- **Single-dash long flags for Go-style tools.** `Tool(single_dash=True)`
  spells every long flag with one dash — `tools.eclint(fix=True)` →
  `eclint -fix` — for tools whose Go `flag` package rejects the `--fix` form.
- **`djlint`** joins the curated tools — the HTML/Django/Jinja template
  linter-formatter, with a typed `tools.djlint(...)` stub.
- **`@group.default` takes `@task`'s policy options.** A group default can now
  be parameterised — `@lint.default(pre=[bootstrap], keep_going=True,
  confirm="…", atomic=True)` and the rest — the same orchestration surface a
  task has, minus `name` (the group already names it). `interactive=True` on an
  **empty-body** default is a load-time error: an empty body fans the group's
  tasks out in parallel, so there is no single body to own the terminal.

### Changed

- **BREAKING: composition is two typed verbs — `plugin()` pulls entry
  points, `include()` pulls modules — and the `plugins=` config key is
  gone.** A pull line in your tasks file replaces the config mount:
  `plugin("footman.docs", into="footman")`. The longest installed
  `footman.tasks` entry-point name (for `plugin()`) or importable module
  prefix (for `include()`) is the *identity*; the rest of the string walks
  the provider's tree, so `plugin("acme.devkit.lint")` reads like
  `from acme_devkit import lint`. A pulled node lands under its **own
  name** — identity never becomes an address; `into=` (a dotted address,
  created on demand) is the consumer's placement, and a whole container
  splats its children (the devkit one-liner). Filters are relative to the
  pulled node; a subpath can land a single task
  (`plugin("acme.linters.default", into="lint")` adopts a provider's
  default, which then fans out the group it *landed in* — default-ness is
  parent-relative). Collisions are provenance-based: your own definitions
  silently win, whatever the file order; two pulls clashing at one leaf
  raise loudly, citing both identities, unless `override=True`;
  group-vs-group composes recursively all the way down. A leftover
  `plugins=` key is a taught refusal pointing at the pull line, never a
  silent ignore.
- **BREAKING: the group default is the child task named `default`.**
  `@group.default` now registers its function as the child `default` — a
  fixed, well-known name — so the default finally has an address
  (`@lint.default` ↔ `fm lint.default`), appears in listings and
  completion, and default-ness is *derived* from the tree
  (`Group.default_task` became a read-only property), so a fork or graft
  can never desync it. The name is the mechanism: any task named
  `default` — via the decorator, `@task(name="default")`, or a pull — is
  its group's default through the same validations (an empty body fans
  out, and excludes itself from its own fan-out set; `interactive=True`
  on an empty body still refuses). Two consequences to know: a group
  that declared both `@group.default` and a task literally named
  `default` now collides loudly at load, and a *group* named `default`
  is refused (a group-typed default is incoherent — bare `fm lint`
  resolving to another bare group).

- **BREAKING: a nested task's address is one dotted token, everywhere.**
  `fm docs serve` is now `fm docs.serve`; flat tasks and chains are
  unchanged (`fm build lint test`). One spelling serves every surface —
  running, `--help`, `--where`, completion — and `--help`/`--where` drop
  the space walk for the same strict resolver (`docs..serve`, `.docs`, and
  a trailing `docs.` are taught errors, never silently normalised). The
  space form is permanently *taught*, not parsed: `fm docs serve` answers
  "nested tasks use dots: 'docs.serve'", the lookahead teaching the longest
  resolvable path (`fm footman tools sync` → `footman.tools.sync`), and a
  child of a just-run runnable group teaches the same way (`fm lint python`
  → `lint.python`). Unknown addresses get did-you-mean suggestions over the
  flat dotted index; listings, `--tree`, help, and the markdown exporter
  print full dotted addresses so everything shown is copy-paste-runnable.
- **BREAKING: task completion is path completion — the `.` is footman's
  `/`.** Candidates sit one segment beyond the typed prefix, `ls -F`
  style: a namespace group always carries its trailing dot (`docs.`), a
  runnable group offers itself *plus* its dotted children (space runs the
  default, `.` descends), a task completes terminally. A unique namespace
  match completes straight through to its children, so no shell ever
  strands the cursor with a space after `docs.`.
- **BREAKING: task and group names may not contain `.` or whitespace** —
  `.` is the address separator, so `group("v2.0")` or
  `@task(name="docs.build")` would alias into fake nesting; both refuse at
  load time with a taught `RegistrationError`.

- **`os.environ` is virtualised for the run.** Reads inside a task see the
  run-start snapshot plus the task's own overlay — exactly what the
  subprocess branch of the same call injects as `env=`, so in-process and
  subprocess tool calls finally read the same world. Writes from a task
  body scope to the task's overlay: visible to its own reads and every
  child it spawns, invisible to siblings — with a one-time, task-attributed
  stderr note naming the deliberate spelling (`env=` / `ctx.env`). Deleting
  a variable has no additive spelling and is a taught error. The env
  overlay for in-process calls now rides this router (thread-confined, no
  lock — concurrent overlaid calls no longer serialise); outside a run,
  `os.environ` behaves exactly as stock Python, and bare `run(callable,
  env=…)` calls keep the classic guarded global patch as their fallback.
- **`serial=` and `exclusive=` — declared serialisation, the only kind
  left.** `@task(serial=True)` (and `.opts(serial=True)` per use) declares
  "this task owns the process globals": the scheduler runs at most one
  serial task at a time, *overlapping the full parallel pool*, and inside
  it footman restores the old conveniences safely — a real chdir to the
  task's resolved cwd, the env overlay applied to the real `os.environ`,
  both snapshotted and restored, with the routers and guards standing
  down. `@task(exclusive=True)` is the honest full drain for benchmarks
  and migrations: it runs with nothing else in flight, exempting only
  ancestors parked waiting on their own children. Lane waits are never
  silent (a note names the holder after two seconds), new starts yield to
  a waiting exclusive, and a fan-out child of a lane holder inherits the
  lane — a lineage extends a hold, it never contends with it. Body-calls
  keep inheriting the caller's regime; the markers are scheduling
  declarations, read at task boundaries — which is what keeps the whole
  design deadlock-free. **`footman.chdir()`** completes the serial story:
  real directory changes as a context manager (default target the task's
  own cwd, marker-grammar arguments, `ctx.cwd` kept in sync, everything
  restored) — legal in serial/exclusive tasks and a taught error in
  parallel ones.
- **Raw `subprocess` is quietly correct in parallel.** `subprocess.Popen`
  (and everything that funnels through it — `subprocess.run`, `os.popen`,
  third-party code) gets the task's context filled in when a spawn passes
  neither `cwd=` nor `env=`: the child starts in the task's directory with
  the snapshot-plus-overlay environment, exactly as `run()` would spawn it —
  with a one-time note suggesting the deliberate spellings. Explicit
  arguments always win, `env={}` stays a deliberately clean environment, and
  the `unmanaged` policy is the one off-switch.
- **An interactive task no longer stops the world.** `interactive=True`
  used to force the entire run sequential; it now claims the arbiter's
  *console* lane instead — one terminal owner at a time, granted
  atomically with any serial/exclusive lane so partial holds can't chain —
  while the parallel pool keeps running around it, captured. A finished
  sibling's buffered output queues until the wizard frees the terminal,
  so nothing splats over a prompt — and the status line *suspends* for
  exactly the ownership window instead of yielding for the whole run: it
  clears when a wizard takes the terminal and repaints, truthfully, the
  moment it frees. A wizard costs you the terminal — not the run's
  parallelism, and no longer its progress line. Listings and `--json`
  also carry a `lane` key (`serial`/`exclusive`), so the scheduling
  declarations show where `interactive`/`infinite`/`confirm` already do.
- **stdin is guarded like the global it is.** A bare `input()` (or any
  `sys.stdin` read) in a plain parallel task is now a taught error naming
  the exits — declare the value with `ask()`, or mark the task
  `interactive=True` to own the terminal — instead of a silent hang or a
  stolen read. Interactive and serial tasks, the framework's own boundary
  prompts, and anything outside a run pass through untouched.
- **In-process tool calls demote instead of breaking.** A `tools.*` call
  that would run in-process but needs a cwd other than the live process
  directory runs as its subprocess twin instead — same command, same
  semantics, right directory, still fully parallel; the in-process
  startup saving is the only loss (a `-v` note says so). Equal target and
  live cwd — the common single-package case — stays in-process untouched,
  and a serial task's cwd is really applied, so it stays in-process too.
  `Tool.opts()` gains **`cwd=` and `rel=`** beside `nofail`/`capture` —
  the bridge's per-call override, threading straight into `run()`, so
  `tools.npm.opts(rel="web").run("build")` roots one call in a
  subdirectory (and a bound `web_npm = tools.npm.opts(rel="web")` roots
  every call through it).
- **The process globals are guarded in parallel tasks.** `os.chdir` /
  `os.fchdir` raise a taught error (the cwd belongs to no one in a parallel
  run); `os.putenv`/`os.unsetenv` raise one too (they bypass env scoping
  even in plain Python); `os.getcwd` warns once per task toward
  `footman.cwd()`; `os.fork` warns that forking a threaded process is
  unsafe; and `multiprocessing`'s `BaseProcess.start` (which also covers
  `ProcessPoolExecutor`) notes that in-process workers inherit the real
  environment, not the task's overlay — and that self-parallelising tools
  lose little by taking the serial lane. Everything passes through
  untouched outside a run.
- **Breaking: footman never chdirs in a parallel task.** In-process calls
  used to get a real `os.chdir` (guarded by a process-wide lock) whenever
  the task's directory differed from the process cwd — which silently
  serialised the run for every such call, whether the callable cared or
  not. The chdir is gone: an in-process call whose target directory equals
  the live process cwd (the common single-package case) runs untouched and
  fully parallel; a *foreign* target is now a taught error naming the exits
  — run it as a subprocess (which gets `cwd=` for free), build paths from
  the new **`footman.cwd()`** (the task's resolved directory as a concrete
  path), or declare `@task(cwd="unmanaged")` if the call genuinely doesn't
  care. The env overlay for in-process calls is unchanged. Subprocesses were
  always spawned with an explicit `cwd=` and keep working exactly as before.
- **A tool's `.opts()` is now footman run-control; a tool's own globals move to
  `.flags()`.** A `tools.*` call is pure flags and positionals again: run-control
  no longer rides reserved call kwargs. `.opts(nofail=…, in_process=…,
  capture=…, title=…)` is a closed vocabulary that rides *beside* the call and
  never becomes a tool flag — `git.opts(nofail=True).push()` — mirroring a task's
  policy-vs-work `.opts()`. A tool's own global options (bound before a verb)
  move from `.opts(host=…)` to **`.flags(host=…)`** — `docker.flags(host="x").ps()`.
  Two consequences: `capture` and `title` reach the bridge for the first time
  (`pytest.opts(capture=False)` streams a run live), and a tool that really has
  a `--capture` (pytest) can now be spelled in the call, `pytest(capture="no")`,
  since footman's `capture` lives on `.opts()`. Migration: `tools.x(…,
  nofail=True)` → `tools.x.opts(nofail=True)(…)`; `tools.x.opts(host=…)` →
  `tools.x.flags(host=…)`.
- **The test-harness result is renamed `InvokeResult`.** `Runner.invoke()` now
  returns `footman.testing.InvokeResult` (was `Result`), freeing the prominent
  `footman.Result` to name the run-step `Result` above. Test code that calls
  `Runner().invoke(...)` and reads `.ok`/`.stdout`/`.exit_code` is unaffected —
  only the type's own name changed.
- **A trailing underscore is stripped from a CLI flag.** A task or parameter
  named with Python's keyword-escape underscore (`sync_`, `import_`) now maps
  to `--sync` / `--import`, not `--sync-` — matching the `tools.*` bridge,
  which already stripped it.
- **`fm --json` step entries carry `stdout` and `stderr`** separately, in place
  of the previous merged `output` field.
- **`help` is now a reserved task parameter name.** A flag or option parameter
  named `help` maps to `--help`, which footman intercepts anywhere on the line
  to render help and never run a task — so the option could never bind. Instead
  of silently shadowing the parameter, footman now rejects it at manifest-build
  time with a taught error that names the fix (rename to e.g. `show_help`). The
  check is precise: a required positional `<help>` or a variadic `*help` never
  produces `--help`, so both stay legal. This is the only reserved name — every
  other global must precede the first task, so a task parameter may reuse it.

### Fixed

- **`include()` carries a group's default and finalizers.** Grafting a module
  with `include()` kept its tasks but silently dropped the group's
  `@group.default` — breaking the bare-group command and hiding its options —
  and its `@finalize` hooks; both now come across, so a `tasks.py` composed from
  per-module `include()`s behaves the same as one that defines them directly.
- **`parallel()` collects a `SystemExit`.** A thunk that calls `sys.exit()` or
  `raise SystemExit(...)` — a common "fail this task" idiom — is now collected
  like any other failure instead of escaping and crashing the whole pool.
- **A task's `sys.exit("reason")` shows its reason.** A task body that raises
  `SystemExit` with a string (Python's "print this message, exit 1" idiom) used
  to fail with a bare `exited with code 1`, the message swallowed; the debugging
  tax was re-running the task under Python to read the traceback. The reason now
  surfaces in the failure line and the `--json` `error` field, rendered verbatim
  (no `SystemExit:` prefix). An int/`None` code (`sys.exit(2)` — the
  fail-with-a-code idiom) is unchanged. (A reason raised inside a `parallel()`
  thunk is still normalised to a code-only failure — the fan-out deliberately
  converts a `SystemExit` to a catchable `RunFailed`.)
- **`run("a | b")` teaches instead of mis-running.** A string command with a
  bare shell operator (`|`, `>`, `&&`, …) now raises a taught error: `run()`
  uses no shell, so the operator would otherwise ride along as a literal
  argument and the pipeline would silently not happen. Reach for a shell
  explicitly (`tools.bash("-c", …)`) or split into steps.
- **`fm footman tools provision`** skips the hand-written shell drivers instead
  of printing a spurious `uv tool install` failure for each.
- **`fm --help "group task"` accepts a quoted or dotted path.** A path handed as
  one shell token (`"docs serve"`) or dotted (`docs.serve`) is split into its
  components and resolved, instead of failing with a self-referential "did you
  mean 'docs serve'?". A genuinely unknown quoted path now names its first bad
  component and suggests a real neighbour.
- **A missing `include()` module names the call, not the file.** `include("x.y")`
  for a module that can't be imported raised "failed to import `<tasks.py>`",
  blaming the tasks file; it now raises a taught error naming the `include()`
  call and the reason (`include('x.y'): failed to import (ModuleNotFoundError:
  …)`), the same shape `plugin()` already gives.
- **`Runner.invoke` never hands off to uv.** The uv re-exec replaces the
  process via `execvp` when the interpreter running footman sits outside a
  project venv whose `uv.lock` pins footman. `Runner.invoke` could reach it,
  so an embedded invocation could exec the *host* process — under pytest-xdist
  that is the worker whose stdio carries the test protocol, and every
  Runner-based test died as `worker 'gwN' crashed` with no traceback. Embedded
  invocations now always run in-process; real CLI entry still hands off.
- **`fm footman docs cast` retries a cast that produced no output.** A cold
  pwsh (.NET startup) on a loaded CI runner can exceed the 1.5s pty settle
  window, and zero frames looked identical to a dead session. An empty cast
  now retries once with a 5s settle; the happy path pays nothing, and a
  genuinely broken shell still raises the same error.

### Docs

- Added an abbreviations glossary with site-wide hover tooltips for footman's
  coined vocabulary (manifest, cascade, chain, taught error, …), split the
  execution-model pages (new **Asking for input** and **Progress & timing**
  guide pages), moved `@finalize`/`TaskView` onto the composing page, added
  mermaid diagrams for the dependency graph and completion refresh, and added
  docs-drift test guards so undocumented public symbols and stale version pins
  fail the gate.
- Documented the plain-prose output convention (task docstrings/`doc()` render
  as plain text in `--help` and export cleanly to markdown; footman paints no
  rich markup in the terminal — colour is the one styling it applies), and
  recorded an optional post-1.0 rich-terminal renderer in the roadmap.

## [0.19.0] — 2026-07-23

### Added

- **Per-subtree keep-going scoping.** The failure policy is now resolved *per
  node*, not run-wide: a keep-going gate keeps its own prerequisites going with
  it, while an independent task in the same run keeps its own policy. So
  `fm check deploy` — `check` keep-going, `deploy` fail-fast — surfaces every
  `check` failure *and* still bails `deploy` on the first, where before one
  task's `keep_going=True` forced the whole run to keep going. A command-line
  `-k`/`--fail-fast` still overrides every scope at once, and a task's own (or
  `.opts()`-set) policy wins over one inherited from a gate above it. True
  fail-fast reaps only the *fail-fast* subprocess trees still in flight on a
  failure, so a keep-going task's long-running child keeps going while a doomed
  fail-fast branch dies at once.
- **Per-use option overrides — `.opts()`.** A task or runnable group carries an
  `.opts(...)` that overrides its orchestration options *for one use* without
  touching the registered task: `pre=[fmt.opts(atomic=True)]`,
  `pre=[lint.opts(keep_going=True)]`, or a body call
  `deploy.opts(atomic=True)("prod")`. It takes the policy options — `keep_going`,
  `atomic`, `interactive`, `progress`, `confirm`, `infinite` — and rejects a
  task parameter with a taught error, because work goes in the call and policy
  rides beside it, the same split `tools.*` draw with their `.opts()`.
  Keep-going resolution now spans the whole dependency graph, so a declared or
  opted `keep_going` on a `pre`/`post` prerequisite counts, not only a task
  named in the chain. As a side benefit, `@task` now **forwards the wrapped
  function's signature** in the type system (parameters and return type), where
  a decorated task used to be typed `Callable[..., Any]` — so a body call with a
  wrong or missing argument is now a type error, not silently `Any`.
- **`TaskView` round-out for finalizers.** A `@finalize` hook's `TaskView` now
  reads a task's owning `group` (or `None` at top level), its policy flags
  (`keep_going`, `atomic`, `infinite`, `interactive`, `timed`, `confirm`) and
  its **cascade provenance** —
  `defining_dir` (the folder it was defined in), `shadowed` (the task it
  overrides one cascade level up), `shadow_chain` (it and everything it
  shadows), and `source_file` — so a finalizer can make decisions by *where* a
  task came from and *what* it overrode (e.g. gate every task defined under an
  `infra/` folder). New `set_opts(**overrides)` sets a task's policy for every
  use of it — the permanent, tree-wide counterpart to `.opts()`, taking the same
  options and rejecting a task parameter the same way. A command-line
  `-k`/`--fail-fast` still wins over a set `keep_going`.

### Changed

- **Homebrew resolution for host-read tools on macOS (stub generation only).**
  The tools footman reads straight off the host — git, docker, uv, never
  provisioned into an isolated prefix — resolve their Homebrew **keg**
  (`opt/<name>/bin/<name>`, which survives `brew unlink`) before falling back to
  `PATH`, so on macOS the stub describes the newest build (Homebrew git over
  Apple's older `/usr/bin/git`). Every **provisioned** tool (ruff, mkdocs,
  pytest, gh, …) still resolves on plain `PATH`, so a `provision --sync` prefix
  and a venv win and no stale `/opt/homebrew/bin` console-script shim can shadow
  them. Only `<tool> --help`/`--version` parsing is affected; running a
  `tools.*` task resolves on `PATH` as before.

### Fixed

- **`fm footman tools provision --sync` no longer strips plugin flags.** A
  provisioned prefix is a bare install, so reading pytest's stub from it dropped
  every `--cov*` flag (they come from pytest-cov). A driver can now name extra
  wheels to install alongside a tool — `Provision(plugins=("pytest-cov",))` —
  which `provision` adds with `uv --with`, so the prefix holds a plugin-complete
  pytest and the sync reads its full flag surface.
- **A `v`-prefixed `0.x` version is read whole.** The version a stub records is
  scraped from `<tool> --version`, and a tool that printed `v0.23.1` was recorded
  as `0.23.1`'s tail, `23.1`, because the match required a word boundary the `v`
  removed. It now reads `0.23.1` (markdownlint-cli2, and any tool that glues `v`
  to a `0.` version).

## [0.18.0] — 2026-07-22

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
  The group tab-completes and self-documents like a first-class command, and is
  **callable from a task body** — `check` can call `lint(fix=fix)` the way it
  calls any task, running the default's action (or fanning out) in order.
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
- **Availability by `@requires` decorators.** Task availability moved from
  `@task(when=/requires=/reason=)` to stacked decorators: `@requires_dep` (a
  Python module importable), `@requires_tool` (a command on `PATH`),
  `@requires_env` (an environment variable set), and the generic `@requires`
  (a live predicate) they build on. Each carries its own `reason=`, so a task
  gated on both a missing package and a missing variable can say *both* — and
  `availability()` now collects **every** failing gate instead of stopping at
  the first, each in its own words. Gates are still re-checked live on every
  run, and a failing one lists the task with its reason rather than hiding it.
  **Breaking (pre-1.0):** `@task`'s `when=`, `requires=`, and `reason=` are
  removed; stack the decorators above `@task` instead.

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

[Unreleased]: https://github.com/willemkokke/footman/compare/v0.26.0...HEAD
[0.26.0]: https://github.com/willemkokke/footman/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/willemkokke/footman/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/willemkokke/footman/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/willemkokke/footman/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/willemkokke/footman/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/willemkokke/footman/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/willemkokke/footman/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/willemkokke/footman/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/willemkokke/footman/compare/v0.17.0...v0.18.0
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
