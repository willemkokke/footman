# CLAUDE.md

Guidance for Claude Code (and any agent) working in this repo.

## What footman is

A task runner: typed Python function signatures become real CLI flags and
positionals, modules become nested command groups, independent tasks run in
parallel by default, and shell completion answers from a cached JSON manifest in
~20 ms **without importing your code**. Ships two console scripts, `footman` and
`fm`. **Zero runtime dependencies** (standard library only). Python 3.11+.
Pre-1.0 and moving fast — the API, decorator surface, manifest format, and CLI
grammar may break without a deprecation cycle.

## Hard invariants — do not violate

- **Zero runtime deps.** Nothing under `src/footman/` may import a third-party
  package. Dev/test/docs tooling lives in `uv` groups, never in `dependencies`.
  One blessed exception: a first-party plugin task may lazily import an
  optional third-party package *inside its body* when gated with a stacked
  `@requires_dep("…")` (e.g. `docs shots` imports rich) — the package is
  never a declared dependency, never imported at module import time, and the
  task lists as unavailable without it.
- **The completion hot path is stdlib-only and import-free of the framework.**
  `_complete.py` (and the detached refresh child it spawns, `_refresh.py`, at
  the moment it decides to spawn) must not import `footman` internals or user
  tasks — a TAB press is one file read + JSON parse + tree walk. `main()` in
  `__init__.py` dispatches `--complete` *before* importing anything.
- **`tools.py` ↔ `tools.pyi` parity.** Every module-level runtime binding in
  `tools.py` must be declared in the stub; `test_tools.py` enforces this with an
  AST test. Module imports are aliased private (`import re as _re`, …) so
  `tools.<name>` always resolves to a `Tool` via `__getattr__`.
- **Coverage ≥ 92%.** Enforced in CI (`fail_under = 92`).

## The gate (run before every commit)

This project dogfoods itself, so use `uv run fm …`:

```sh
uv run fm check                                   # ruff format --check, ruff check, basedpyright, pytest (parallel)
uv run pytest -q --cov=footman --cov-report=      # ENFORCES coverage; `fm check`'s pytest does NOT
uv run --group docs zensical build --clean --strict   # ONLY when docs/ changed
```

`fm check`'s pytest does not enforce coverage — always run the explicit
`--cov` command too. ruff line length is 88; target `py311`; type-checker is
basedpyright.

## Layout

```
src/footman/
  __init__.py     lazy re-exports + main() (dispatches --complete first)
  _complete.py    completion hot path (stdlib only, no framework import)
  _refresh.py     detached stale-while-revalidate manifest rebuild
  _shellcomp.py   shell completion installers (bash/zsh/fish/pwsh/nushell)
  _app.py         execution path: _run → _execute → _run_tree / run_group
  registry.py     @task / group() decorators, capture()
  discover.py     the monorepo tasks.py cascade (per-file import isolation)
  compose.py      include() / plugin() (footman.tasks entry points)
  manifest.py     introspect tasks → serialisable manifest (baked completer output)
  split.py        CLI grammar: globals + chain splitting; GLOBALS is the source of truth
  coerce.py       type coercion (unions, choices, markers)
  params.py       public markers: suggest, Many, nosplit, between, env, check, exists…
  context.py      run(), parallel(), the stdout/stderr router
  executor.py     bind + run one task
  schedule.py     the DAG scheduler (parallel/sequential, live progress line)
  config.py       [tool.footman] discovery
  tools.py/.pyi   the tools.* bridge + its typing stub
  testing.py      Runner (in-process CLI) + recording()
docs/             Zensical (mkdocs-like) site
tasks.py          footman's own tasks — the gate is `fm check`
```

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
  `uv run ruff check --fix src tests && uv run ruff format src tests`.

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
  `uv run fm docs build --check`. Coverage HTML embeds via `fm docs coverage`.
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
2. Bump both version files to `X.Y.Z`.
3. Move CHANGELOG `[Unreleased]` → `[X.Y.Z]` with today's date; add the
   `[X.Y.Z]: …/compare/vPREV...vX.Y.Z` link and repoint `[Unreleased]` to
   `…/compare/vX.Y.Z...HEAD`.
4. Commit `chore(release): vX.Y.Z — <summary>`, push the branch, open a PR.
5. When its checks are green, **merge it by hand** (repo auto-merge is off).
6. Fast-forward local `main` (`git fetch` then `git merge --ff-only`), then tag
   that merged commit `vX.Y.Z` and push **only the tag**.

Pushing a `v*` tag triggers `.github/workflows/release.yml`: it runs full CI,
verifies the version, builds the sdist + wheel, and publishes to PyPI
(environment `pypi`). Never `git push` casually — the maintainer drives
releases; a stray tag publishes.
