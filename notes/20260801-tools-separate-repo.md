# footman-tools as a separate repository

Status: PLAN — nothing built. The sibling alternative to
[20260801-tools-namespace-package.md](20260801-tools-namespace-package.md):
same surface (`footman.tools`, graft-packaged, executor auto-wired),
but the tools distribution lives in **its own repository** instead of a
uv-workspace dist in this one. Ends with a merits comparison of the two
plans. Decisions marked **open** await Willem's call.

## What is identical to the namespace plan

The user-visible contract does not change between the plans:

- **Import name**: `footman.tools`, via the graft (option A) — the new
  dist ships only `footman/tools/`; footman's wheel stops shipping it;
  pip overlays the two dists in one package directory. The graft works
  the same regardless of which repo builds the wheel. Fallback to the
  shim (B) on the same trigger (editable/uv dev-mode verification).
- **The hookpoint**: one injected `Executor` protocol
  (`(argv, /, **policy) -> ResultLike`), the bridge never importing
  `footman.context`, `installed_version()` staying on plain
  `subprocess`. Phase 1 of the namespace plan ("carve in place") is a
  prerequisite of *both* plans and is unchanged.
- **Auto-configuration**: the wiring must need nothing from the user.
  `footman/tools/__init__.py` (in the grafted dist) resolves its
  executor by importing a *named, public* registration point from
  footman (e.g. `footman.hookpoints.tools_executor` — name **open**).
  footman without the graft teaches `pip install footman-tools`; the
  graft atop a too-old footman raises a taught error citing the
  declared floor. No entry points, no configuration files: the
  contract is an import in one direction and a version floor in the
  other.

One verification from the namespace plan's inventory is now done:
**item 5 (completion/manifest coupling) is nil** — `_manifest.py` has
no reference to the bridge; `fm tools.*` are ordinary tasks whose
bodies use the bridge like any user task would. Nothing in the
completion lane crosses either boundary.

## What differs — everything is repo logistics

### The new repo

`willemkokke/footman-tools` contains:

- `src/footman/tools/` — the bridge (as the grafted subpackage), its
  `__init__.pyi`-equivalent typing surface, and `_stubs/`.
- The parity test, stub-focused tests, and the consumer-typing checks
  that exercise `tools.*` resolution.
- **The stub-taking machinery**: `_drivers.py`, `_provision.py`,
  `_toolfetch.py`, and the `tools.provision/audit/sync` task
  definitions. In the namespace plan these could stay dev-side in
  footman's repo; with a separate repo they follow the stubs — the
  machinery exists to regenerate files that live here.
- **`tool-history/` and the weekly refresh workflow.** The namespace
  plan kept them "in this repo either way" because same-repo was the
  lean; a separate repo takes them along. The refresh opens its PRs
  against this repo and its "were events appended" trigger drives
  *this repo's* releases.
- Its own `tasks.py`, dogfooding footman as a **dev dependency**: the
  tools repo is itself a footman consumer (`uv run fm check`,
  `uv run fm tools.audit`, …), which is a standing integration test of
  the published footman it pins.

### What stays in footman's repo

- The executor adapter and its public registration point.
- The taught error for the missing graft.
- Nothing else tools-related: no refresh workflow, no history, no
  stub churn in CI or git history.

### CI shape

Each repo gets the CI it actually needs. footman keeps the
3 OS x 5 Python + shells matrix and sheds the refresh workflow. The
tools repo needs the three-platform stub gather, the parity/stub
tests, and the four type checkers against the graft layout — but no
shell matrix and no completion timing jobs. Coverage gates untangle:
the 92% merged bar stays footman's; the tools repo sets its own.

### Versioning

- footman-tools versions independently (plain `vX.Y.Z` tags — no
  prefix scheme needed, which dissolves the namespace plan's open
  question 6).
- footman-tools declares `footman>=X.Y` for the executor contract;
  the contract gets a name and a stability note in both CHANGELOGs.
  Bumping the contract bumps the floor.
- footman declares nothing (zero runtime deps holds); its only
  awareness of the graft is the taught install error.
- The weekly refresh becomes the tools repo's release heartbeat:
  gather → PR → merge → tag, without footman's repo hearing about it.
  Whether the refresh auto-tags or a human merges+tags stays **open**
  (same question as the namespace plan's, now scoped to one repo).

## Migration phases (each gate-green, mergeable alone)

1. **Carve in place** — identical to the namespace plan's phase 1;
   happens in this repo; valuable regardless of which plan proceeds
   (or neither).
2. **Extract.** New repo seeded with the bridge + typing surface +
   stubs + machinery + `tool-history/` (history-preserving subtree
   export, or a clean start with a pointer back — **open**). Graft
   packaging verified there; footman's wheel drops `tools.py` in the
   same release window and gains the taught error.
3. **Refresh rehome.** The workflow moves to the new repo; its PR
   target, gather matrix, and release trigger re-point. footman's
   `.github/workflows/refresh.yml` is deleted.
4. **Docs + runbooks.** tools-bridge.md and the typing pages own the
   two-dist install story; each repo's CLAUDE.md and release section
   describes only its own train; hse pins updated after the first
   tools release. Cross-repo compatibility statement (which floor
   pairs are tested) written down once, in the tools repo.

## Merits: same-repo workspace vs separate repo

Engineering effort excluded by instruction; this is steady-state
merit only.

### Where the separate repo wins

- **Cadence independence is real, not simulated.** The weekly refresh
  is a genuinely different release train (data updates on a schedule)
  from the framework's (design work when it's ready). Same-repo, the
  two trains share a main branch, a CHANGELOG discipline, a tag
  namespace, and a PR stream — the choreography question (two tag
  schemes out of one repo) exists *only* in the workspace plan.
  Separate, each train is boring on its own.
- **History and attention.** Weekly stub-churn PRs, three-platform
  gather logs, and tool-history appends stop diluting footman's
  history and CI minutes. `git log` in footman goes back to being
  about the framework. Issues sort themselves: "the git stub is
  wrong" lands where the stubs live.
- **The contract is forced to stay honest.** A repo boundary makes
  backsliding structurally hard: nobody can quietly re-couple the
  bridge to a footman internal, because there is no same-PR way to do
  it. The executor contract *must* remain named, versioned, and
  floor-pinned. In the workspace, that discipline is a convention the
  gate can't fully enforce.
- **Standing integration test.** The tools repo dogfooding a *pinned,
  released* footman continuously exercises the public contract the
  way external consumers (hse) do. The workspace always tests against
  in-tree footman — which is exactly the configuration no user ever
  runs.
- **The future re-badge gets cheap.** A repo that already stands
  alone, builds alone, and releases alone is one packaging decision
  away from the neutral-name standalone bridge (namespace plan, open
  question 4). The workspace keeps that future a repo-surgery away.
- **Tailored CI.** Neither repo pays for the other's matrix.

### Where the same-repo workspace wins

- **Atomic contract evolution.** An executor-contract change lands as
  one PR, one gate, one review — bridge and adapter move together.
  Separate repos turn every contract change into a two-phase landing:
  ship the footman side, release, bump the floor, then ship the
  bridge side. The contract *will* change pre-1.0; the workspace
  makes that cheap, the split makes it ceremonious. This is the
  single biggest merit on the workspace side.
- **No version-skew surface.** Same-repo, "which footman-tools works
  with which footman" has one answer: this commit. Separate, skew is
  a real user-visible state — floor pins bound it, but the pairing
  matrix (old graft + new footman, and the reverse) needs stating and
  ideally testing. That's a new class of bug that simply cannot exist
  in the workspace.
- **One gate, drift tests with full sight.** The parity test, the
  consumer-typing suites, and any docs-drift guard can see both sides
  of the boundary in one run. Cross-repo, each side tests against a
  *pin* of the other; a breakage introduced on footman main is
  invisible to the tools repo until a release (or a scheduled
  cross-main CI job, which is a new mechanism to own).
- **Dogfooding immediacy.** In the workspace, a bridge improvement is
  usable by footman's own tasks in the same commit. Cross-repo, the
  tools repo can't use an unreleased footman hookpoint (and footman's
  tasks can't use an unreleased bridge) without dev-pin gymnastics.
- **One place to look.** The whole tools story — bridge, machinery,
  history, docs — stays discoverable from the repo people already
  know. Two repos mean split docs, split CHANGELOGs, and a
  compatibility page nobody has to write today.
- **Pre-1.0 honesty.** Everything here is explicitly allowed to break
  without deprecation. A repo boundary is a stabilising force — which
  is a *cost* while the seam is still finding its shape.

### The lean

The merits sort by **contract maturity**. While the executor contract
is young and expected to move (it does not exist yet — phase 1 hasn't
run), the workspace's atomic-change and no-skew properties dominate.
Once the contract has survived a few months unchanged and the weekly
refresh is visibly the only reason the tools dist releases, the
separate repo's cadence and honesty properties dominate — and the
workspace plan's phase 2 output is precisely what a later extraction
would lift out.

Lean: **workspace first, separate repo as the pre-planned second
step** — not either/or. The namespace plan's phases 1–2 are common
prefix; this plan's phases 2–4 are a later fork that gets *cheaper*
after the workspace has hardened the seam. **Open** — Willem may
weight the refresh-cadence annoyance higher than contract churn and
go straight to the split.

## Naming

Constraints: the import stays `footman.tools`, so the dist name only
appears in `pip install`, the repo URL, and CHANGELOGs. Checked on
PyPI 2026-08-01:

- **`footman-tools`** — free. Boring, self-describing, obviously
  paired with `footman`. The graft precedent (dist name ≠ import
  name is already true: dist `footman-tools` ships package
  `footman/tools/`) makes this the path of least surprise.
  **Lean.**
- `equerry` — free. The themed option (the officer in charge of the
  stables — the one who keeps the tools). Only worth taking if the
  neutral-name re-badge future (namespace plan, question 4) is wanted
  *now*: then repo + dist could be `equerry` from day one, with
  `footman-tools` as the graft dist it publishes alongside. Otherwise
  it's a second name for no benefit.
- `valet`, `toolbelt` — taken; recorded so they aren't re-proposed.

Repo name follows the dist name: `willemkokke/footman-tools`.

## Open questions (Willem's calls)

1. Straight to separate repo, or workspace-first-then-extract (the
   lean)?
2. Dist/repo name: `footman-tools` (lean) vs reserving `equerry` for
   a re-badge-shaped future.
3. History-preserving subtree export vs clean-start repo.
4. The public registration point's name (`footman.hookpoints.…`?).
5. Refresh auto-tags releases, or human merges+tags (inherited from
   the namespace plan's question 6, now single-repo-scoped).
