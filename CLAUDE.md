# CLAUDE.md

Guidance for Claude Code (and any agent) working in this repo.

## What footman is

A task runner: typed Python function signatures become real CLI flags and
positionals, modules become nested command groups, independent tasks run in
parallel by default, and shell completion answers from a cached JSON manifest in
~30 ms **without importing your code**. Ships two console scripts, `footman` and
`fm`. **Zero runtime dependencies** (standard library only). Python 3.11+.
Pre-1.0 and moving fast — the API, decorator surface, manifest format, and CLI
grammar may break without a deprecation cycle.

## Hard invariants — do not violate

- **Zero runtime deps.** Nothing under `src/footman/` may import a third-party
  package. Dev/test/docs tooling lives in `uv` groups, never in `dependencies`.
  One blessed exception: a first-party plugin task may lazily import an
  optional third-party package *inside its body* when gated with a stacked
  `@requires_dep("…")` (e.g. `docs.shots` imports rich) — the package is
  never a declared dependency, never imported at module import time, and the
  task lists as unavailable without it.
- **The completion hot path is stdlib-only and import-free of the framework.**
  `_complete.py` (and the detached refresh child it spawns, `_refresh.py`, at
  the moment it decides to spawn) must not import `footman` internals or user
  tasks — a TAB press is one file read + JSON parse + tree walk. `main()` in
  `__init__.py` dispatches `--complete` *before* importing anything.
- **Coverage ≥ 92%.** Enforced in CI (`fail_under = 92`).

## The gate (run before every commit)

This project dogfoods itself, so use `uv run fm …`:

```sh
uv run fm check                                   # ruff format --check, ruff check, basedpyright, covered pytest (all parallel)
uv run --group docs zensical build --clean --strict   # ONLY when docs/ changed
```

**The exit code is the verdict — never put a filter between it and you.** A
pipe replaces the gate's status with the filter's (`fm check | tail -4`
reports tail's 0 and shows the step summary while the failing step scrolls
past), which has produced a false "green" twice here and twice in a
downstream repo. Redirection *keeps* the code, so when the output is
unwelcome, redirect rather than pipe and read the file only on failure:

```sh
uv run fm check > /tmp/gate.log 2>&1     # exit code preserved; read the log if non-zero
```

A `PreToolUse` hook (`fm hooks.pre-bash`) refuses the piped form. It is a
nudge, not a sandbox — `grep`/`sed`/`wc` hide a verdict just as well.

`fm check` is the whole gate: its test step runs `pytest -n auto` under
coverage against a local floor (`--cov-fail-under=90`) — no separate `--cov`
command. That floor is deliberately below `fail_under = 92`, which is the
*merged* bar: CI combines three OSes x five Pythons plus the shell jobs and
disables the per-job threshold, because one slice can only ever see its own
branches. Driving the local number up to 92 means installing every shell
(`zsh`, `fish`, `nu`, `pwsh`) and still falling short — don't chase it.
The suite runs across cores via pytest-xdist (`addopts = "-n auto"`); to debug
one test serially (live `-s`, `--pdb`, `-x`), override with `-n0`. ruff line
length is 88; target `py311`; type-checker is basedpyright.

## Layout

```
src/footman/
  __init__.py     lazy re-exports + main() (dispatches --complete first)
  __main__.py     `python -m footman`
  _complete.py    completion hot path (stdlib only, no framework import)
  _refresh.py     detached stale-while-revalidate manifest rebuild
  _suggest.py     completion child: rerun one dynamic completer fresh
  _shellcomp.py   shell completion installers (bash/zsh/fish/pwsh/nushell)
  _paths.py       brand-scoped locations (cache/data/config) — import-light
  app.py          App / Brand: a branded CLI's names, version, and folders
  _app.py         execution path: _run → _execute → _run_tree / run_group
  _script.py      PEP 723 script blocks + the uv handoff argvs
  _signals.py     stop signals (SIGTERM/SIGHUP/SIGBREAK) + the SIGQUIT stack dump
  _encoding.py    the byte-order-mark table, shared by _config and _shellcomp
  _config.py      the config cascade ([tool.<brand>], <brand>.toml, user-level)
  _discover.py    the monorepo tasks.py cascade (per-file import isolation)
  compose.py      include() / plugin() (footman.tasks entry points)
  registry.py     @task / group() decorators, GlobalOption, capture()
  _manifest.py    introspect tasks → serialisable manifest (baked completer output)
  _split.py       CLI grammar: CORE_OPTIONS declarations + chain splitting
  _coerce.py      type coercion (unions, choices, markers)
  _binder.py      bind a JSON/stdin payload to a typed shape
  params.py       public markers: suggest, Many, nosplit, between, env, check, exists…
  context.py      run(), parallel(), Context, the stdout/stderr router
  _globals.py     process-globals routers (environ/argv/stdin) + the arbiter lanes
  _executor.py    bind + run one task; lifecycle hooks; global-option binding
  _futures.py     body calls as run-scoped futures (the once-cell)
  _schedule.py    the DAG scheduler (parallel/sequential, confirm gates)
  _step.py        step(): lifted work items + the generator pump
  _progress.py    the live status line, eta estimation, timing history
  _describe.py    help/describe phrasing, shared with the markdown exporter
  _fetch.py       fetch(): cached downloads (urllib/curl backends)
  _gc.py          the cache collector (age-swept manifests + timings)
  invocation.py   the frozen Invocation hooks receive
  profile.py      first-party plugin: --profile (Perfetto traces)
  env_files.py    first-party plugin: --env-file loading
  docstrings.py   docstring parsing (summary / params / returns)
  markdown.py     the docs exporter (task tables from the manifest)
  testing.py      Runner (in-process CLI) + recording()
  pytest_plugin.py  pytest fixtures over testing.py
docs/             Zensical (mkdocs-like) site
notes/            design plans, `YYYYMMDD-` prefixed — tracked, never published
tasks.py          footman's own tasks — the gate is `fm check`
```

## Notes

`notes/` holds the design plans — what was decided, what was rejected and
why, what was measured before choosing, and which questions are still open.
The docs say what footman *is*; a note says how it got there and what it
nearly was instead. They are tracked, so a plan outlives the laptop it was
written on, but they are **not published**: the site builds from `docs/` with
an explicit nav, so nothing in `notes/` reaches the website or `llms-full.txt`.

**Name them `YYYYMMDD-<slug>.md`, dated the day the note was started**, so the
directory sorts into the order the thinking happened
(`20260726-tool-option-history.md`). Same-day collisions sort arbitrarily and
that is fine. Keep the date of the *first* draft when a note grows — the
prefix records when the thread opened, not when it was last touched; a plan
that turns into a different plan gets a new note and links back.

A note that has landed says so at the top rather than being deleted: the
CHANGELOG carries what shipped, the note carries the reasoning that never
reaches a docs page.

## Testing conventions

- Test-helper names by file: `run`/`build_tree` (test_params, test_markers),
  `_run` (test_binding), `drive` (test_context, test_schedule), the `tree`
  fixture + `ERROR_CASES` (test_split/complete), `specs(fn)` (test_manifest).
  Branding tests use `Runner(App(...)).invoke(line)`.
- **`from __future__ import annotations` gotcha:** in test files, annotations
  become strings evaluated via `eval_str`, so a class/function referenced in an
  annotation must be **module-level**, not local to the test, or it won't
  resolve (e.g. import `Literal`, `Colour`, validators at module scope).
- Functional shell-completion tests (`test_shellcomp.py`) drive real
  bash/zsh/fish/nushell/pwsh and skip if the shell is absent; CI installs them
  all.
- ruff nits that fail the gate: line length 88; RUF043 (regex metachars in
  `pytest.raises(match=…)` → raw string, escape `.`/`|`); RUF003 (en-dash in
  comments → hyphen); I001 import order; RUF022 (`__all__` sort). Fix fast with
  `uv run ruff check --fix . && uv run ruff format .` (the whole repo, as CI
  lints it — `notes/` and `comparison/` are tracked too).

## Work in a worktree, and clean up after yourself

**Every agent session works in its own git worktree, from before its first
edit.** The maintainer edits the main checkout live, so an agent editing
there shares a tree with uncommitted work it did not write: `git add -A`
sweeps someone else's half-finished change into your commit, a `git stash`
around your own gate run takes their edits with it, and a failing test can
belong to either of you with nothing to say which. All three happened in one
afternoon. `EnterWorktree` before touching a file; the main checkout is the
maintainer's.

**A session cleans up what it created.** Before you finish: the worktree is
removed (`ExitWorktree`, or `git worktree remove`), every branch you merged
is deleted **locally and on the remote**, and `git worktree list` shows only
the main checkout. A merged branch left on the remote is not inert — the
refresh workflow names its branch for the date, and a leftover one made
`git push` fail and then, once that was fixed, made a closed PR for the same
head look like an open one. Leave `git branch -a` showing `main` and nothing
else.

## Commits & identity

- **Author/committer email is the maintainer's personal `mail@willem.net`, and
  every commit is SSH-signed so GitHub shows "Verified."** A global git
  `includeIf` keyed on the `willemkokke` remote applies the personal email
  automatically; signing is global. If a commit ever shows **Unverified**,
  check both: (a) committer email is `mail@willem.net` (a *verified* account
  email), and (b) the SSH key is registered as a **signing** key, not just an
  auth key — `gh api users/willemkokke/ssh_signing_keys` must be non-empty.
  Signing changes the commit hash (the signature is in the object), so
  "verifying" existing commits means rewriting them.
- **No `Co-Authored-By:` trailers.** The maintainer is the sole author and
  owner of any issues; commit messages end at the body.
- Conventional-commit prefixes (`feat`/`fix`/`docs`/`test`/`refactor`/`chore`),
  one logical change per commit, body explaining root cause + fix.
- 1Password gates SSH signing (caches ~10 min). Don't retry a failed signed
  commit or SSH push — it routes through 1Password; fall back once, say so, and
  wait.

## Docs

- Site is [Zensical](https://zensical.org) in `docs/`; build strictly with
  `uv run fm docs.build --check`. Coverage HTML embeds via `fm docs.coverage`.
- **Plain words — no consultant jargon** ("lever"/"leverage"/"synergy",
  "utilize", "delve", etc.) in README, CHANGELOG, or docs.
- CHANGELOG follows [Keep a Changelog](https://keepachangelog.com/) + SemVer;
  pre-1.0 minors may include breaking changes. Compare-links at the bottom
  reference tags.

## Releasing

The version lives in **two** places that must match: `pyproject.toml` `version`
and `src/footman/__init__.py` `__version__` — the release workflow's
`verify-version` job checks the tag against both **and** the CHANGELOG entry.

**`main` is protected**: every CI check is required, so the `chore(release)`
commit cannot be pushed to `main` directly (`git push origin main` is refused —
the required checks are only satisfiable through a PR). The bump lands through a
PR, and only the merged commit is tagged. Don't tag before the bump is on
`main`, or the tag points at a commit that never reached the branch.

1. Branch `release/vX.Y.Z` off an up-to-date `main`.
2. Bump both version files to `X.Y.Z`, and the doc version references the
   drift test guards: the `footman~=X.Y.0` pin in `README.md` and
   `docs/index.md`, and the `--version` example in `docs/json.md`
   (`tests/test_docs_drift.py` fails the gate if these go stale).
3. Move CHANGELOG `[Unreleased]` → `[X.Y.Z]` with today's date; add the
   `[X.Y.Z]: …/compare/vPREV...vX.Y.Z` link and repoint `[Unreleased]` to
   `…/compare/vX.Y.Z...HEAD`.
4. Commit `chore(release): vX.Y.Z — <summary>`, push the branch, open a PR.
5. When its checks are green, **merge it by hand** (repo auto-merge is off).
6. Fast-forward local `main` (`git fetch` then `git merge --ff-only`), then tag
   that merged commit `vX.Y.Z` and push **only the tag**.

**The typed tool surfaces live in toolroom** (the companion repo,
`willemkokke/toolroom`): the bridge, the stubs, the machinery, and the
weekly refresh all release on their own train there. footman's own tasks
run through toolroom as a dev dependency; a footman release never waits
on a stub reading.

Pushing a `v*` tag triggers `.github/workflows/release.yml`: it runs full CI,
verifies the version, builds the sdist + wheel, and publishes to PyPI
(environment `pypi`). Never `git push` casually — the maintainer drives
releases; a stray tag publishes.
