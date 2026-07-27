# Tool option history → scheduled refresh → honest auto-release

**Parked 2026-07-26.** Unifies two plans that were never separate: a JSON
history of every tool's option surface, and the scheduled job that keeps it
current and decides when a release is warranted. The audit groundwork landed
(#77, #79); everything below is unwritten.

The thesis: **a stub stops being a snapshot and becomes a rendering.** Today a
stub *is* the record — read from one binary, on one machine, at one moment, and
carrying its version in a header comment. Replace that with a per-tool event
log, and the stub becomes a view of it, the refresh becomes an append, and "do
we need to release?" becomes a question about events rather than version
numbers.

## 1. The file: a base at HEAD, deltas pointing backwards

One JSON file per curated tool, under `_history/`. The **newest** observed
release is stored whole; every older one is a delta describing how to step
back to it. `since`/`until` are never stored — they are derived by walking
the chain.

Pointing the deltas *backwards* is what makes the format fit the work:

- **Priming backwards is pure append.** Each older release adds one delta
  against the current oldest; nothing already written is touched. That is
  the dominant workflow, and it is the cheapest possible write.
- **The current version costs no replay** — it *is* the base. "The current
  version matters most" becomes a property of the format rather than an
  optimisation on top of it.
- **Midfill rewrites exactly one entry**: the inserted release's successor,
  recomputed against it. Local, never cascading.
- **A refresh** promotes the new release to base and demotes the old base to
  a delta — two entries touched, and only when something changed.
- **The release gate is "is this delta non-empty"** — no hashing, no
  comparison of surfaces.

Real, from prek 0.4.11 (base) stepping back to 0.4.10:

```jsonc
{
  "tool": "prek",
  "observed_from": "0.4.0",         // a fact: the oldest release actually read
  "base": {
    "version": "0.4.11", "date": "2026-07-24", "extractor": 1,
    "surface": {
      "run/--glob":      { "help": "Run hooks on tracked files matching the specified glob pattern",
                           "type": "str", "neg": "" },
      "run/--all-files": { "help": "Run hooks on all tracked files in the repository",
                           "type": "bool", "neg": "" }
      // …96 more
    }
  },
  "deltas": {                        // each: how to get from the newer release to this one
    "0.4.10": { "date": "2026-07-16", "extractor": 1,
                "drop": ["run/--glob", "/--glob"],
                "revert": { "run/--all-files": { "help": "Run on all files in the repo",
                                                 "type": "bool", "neg": "" } } },
    "0.4.9":  { "date": "2026-07-11", "extractor": 1 },   // empty: nothing changed
    "0.4.8":  { "date": "2026-07-04", "extractor": 1 }
  }
}
```

An empty delta is the common case and says something precise: that release
was **observed** and changed nothing. It is not the same as a release nobody
looked at, which simply is not in the file.

### Why not the alternatives

Measured, not guessed — seven prek surfaces really extracted, then projected
to 100 releases with a distinct change every fifth, which is what a live tool
does:

| encoding | 100 releases |
| --- | ---: |
| content-addressed full surfaces | 188 KB |
| per-option intervals + change points | 17 KB |
| **base + backward deltas** | **13 KB** |

Content addressing only dedups *identical* surfaces, and a tool that keeps
changing never repeats one — so it degenerates to a full copy per change.
Across the curated set that is the difference between a store measured in
tens of megabytes and one in hundreds of kilobytes.

Per-option intervals come close on size and read better for one question
("when did `--glob` appear?"), but that is a derived index, and generating it
beats storing it.

### The costs, stated

Reconstructing an *old* surface means replaying the chain rather than reading
a blob — microseconds, but no longer a direct read. And a chain has an
integrity property a flat file does not: a mis-ordered or corrupted insert
breaks everything below it. So the writer owes a verify step that replays the
whole chain and checks it reproduces each observed release, run in CI beside
the refresh.

Queries, all derived:

- `exact(version | date)` -> base, then replay deltas down to it.
- `union(start, end)` -> the base plus every option the deltas record as
  dropped; `union(epoch, now)` is what the shipped stubs render.
- "were events appended" -> the new release's delta is non-empty.

The negation and wrapper tables — the two facts the *runtime* reads — come
from the same surfaces, so `tools.audit`'s hard-failure case keeps its
meaning.

Left to implementation: whether `spellings` is the whole identity, or an
option also needs a stable id for the case where every spelling changes at
once. My reading is no — that is a rename, and renames are remove-plus-add
(decision 2).

## 2. Priming: newest first, to a per-tool budget

The current version matters most, so the prime walks **backwards** from the
newest release and stops at a per-tool count — tweakable by popularity, and
living on the **driver** beside `Provision`, never in the history file. The
file records what was observed; how far we chose to look is a setting, and
the two come apart the moment a prime is interrupted. `observed_from` already
answers "where does this history reach", and answers it truthfully. Scale, from PyPI on 2026-07-26: ruff
416 releases (2022-08 →), uv 297, djlint 202, pytest 192, mypy 139, prek 77.
Across ~26 extractable tools a full prime is thousands of install-and-extract
cycles, so it must be **resumable**: skip what is already recorded, and run it
again until it is done. Rate limits make that a requirement, not a nicety.

The budget makes the floor differ wildly per tool — 100 releases is about six
months of ruff and a decade of pytest — which is why `observed_from` records
what was actually seen. An option present in the oldest release read is
`at or before <that release>`, never `since <that release>`: the file must not
assert history it never looked at. A later, deeper prime lowers one tool's
floor without touching any other.

Patch granularity, not minors. The user-facing claim is "added in 0.4.11", and
priming only `0.x.0` releases would attribute a change to the wrong one. A log
that guesses attribution is worse than one that admits a floor.

## 3. The scheduled refresh

A CI job that re-provisions the latest of every curated tool, extracts, and
**appends any new events** to the JSON. Public repo, so the minutes are free.
Cadence was written as monthly in the original sketch and weekly in the later
one — pick one; weekly costs little and shortens the window in which the docs
are behind.

```sh
fm tools.provision                              # into .tools-latest, no --clean
fm --json tools.audit --prefix .tools-latest    # {"checked","behind","skipped","resnapshotted"}
```

`--prefix` is not optional: without it both tasks read the runner instead of
the provisioned set. Locally that was 8 "behind" against the host PATH versus
3 against the prefix — the host number was mostly noise about one laptop.

## 4. The release gate: new events, not new versions

**This is the point of unifying the two.** A new tool version does not
necessarily change its command-line surface, and today it always looks like it
does — the version lives *in* the stub header, so every bump produces a diff by
construction. An auto-release driven by `behind` would cut releases for header
lines.

v0.21.0 (2026-07-26) was the first release to retake snapshots under the new
runbook step, and it is exactly the case the job must reason about:

| tool | changed lines | what actually changed |
| --- | --- | --- |
| `prek` 0.4.10 → 0.4.11 | 23 | a real surface change (new `glob` option) |
| `uv` 0.11.31 → 0.11.32 | 2 | the `Read from` header only |
| `djlint` 1.42.2 → 1.42.3 | 2 | the `Read from` header only |

Two of three had nothing to say. With an event log the gate is exact: **were
any events appended?** No events, no release — record the new version in the
history and stop. This also makes the release notes write themselves, since the
events *are* the changelog entry ("prek 0.4.11 adds `--glob`").

## 5. Constraints carried in

- **A partial fetch must not read as drift.** Provisioning pulls from four
  tiers (PyPI via `uv tool install`, bun's own release, GitHub and GitLab
  assets). A rate-limited or flaky fetch leaves the prefix short, and anything
  missing must be *left alone*, never read from the runner's own PATH — that
  guard shipped in #79 (`not in the prefix`), and the job must additionally
  require every non-skipped tier to report `ok` before trusting the result.
- **A snapshot only ever moves forward** (#79): a reading older than the record
  is ignored rather than written backwards.
- **Plugin-sensitive tools.** A bare provision strips pytest's `--cov*`; the
  driver declares `plugins=("pytest-cov",)` so the prefix holds a
  plugin-complete pytest. Any new tool with plugin-provided flags needs the
  same.
- **git and docker** sit on the `system` tier only because their real "latest"
  sources were deferred (git from source; docker via download.docker.com static
  builds). A refresh on a clean CI box has to fetch them properly anyway, so
  those sources fall out of this work rather than being tracked separately.
- **`main` is protected**, so the job cannot push a `chore(release)` commit
  directly; it lands through a PR like every other release.
- `.tools-latest/` is gitignored; a full provision is ~27s locally, all 24
  fetchable tools ok (6 shells are hand-written stubs, git/docker are `system`).

## 6. Decided (2026-07-26)

1. **The event key is short-if-exists, else long** — shorts are the stabler
   spelling, the reverse of generation's preference (long is friendlier as an
   API). *Both* spellings are recorded on the option and a new reading matches
   on either, so an option that later gains or loses a short extends its
   interval instead of reading as a removal plus an addition.
2. **Renames and verb moves are a removal plus an addition.** Nothing in
   `--help` relates the two spellings, a synthesised "moved" would be a guess,
   and it would surface nowhere that helps a reader.
3. **Help text is state**, so a reworded description is a real change — with
   two noise sources closed first, or the log manufactures releases: extraction
   pins the terminal width (in the prime too, or history disagrees with the
   present), and each event records the **extractor version**, so improving
   `_toolhelp`/`_toolspec` rewrites state without counting as tool events.
   Same mechanism covers a tool flipping between the click and `--help` paths.
4. **Version and state are tracked separately.** Only a state change is due a
   release; the version rides along and is stored as the latest actual version
   for the docs. That is what ends "every bump is a diff": the stub header
   stops being state.
5. **Ordering is by release date, labelled by version, indexed both.** Version
   strings cannot order themselves across this set — `version_tuple` truncates
   at build tags, so `0.6.0-wk.5` and `0.6.0` compare equal — while every
   source publishes a date. The version is what a reader knows, so it is what
   the docstring says; the date is what sorts. The prime must therefore capture
   a date for every historical release it fetches.
6. **Stubs are the union, never pruned.** A removed option stays in the stub so
   completion works against any version ever; `added in` / `removed in` live in
   its docstring. This inverts nothing: a stub already suggests without
   forbidding (`**flags: Any`), and the tool remains the only judge of what it
   accepts. An `exact(version)` variant falls out of the same file later.
7. **Platforms merge coverage-style**, and **absence is never removal** — a
   platform that did not run observed nothing; only a platform that ran and no
   longer sees a flag it had may narrow that flag's platform set. Help text is
   always the latest, resolved linux > macos > windows when they differ.
8. **The history lives in the repo, not the wheel.** Generation is a maintainer
   action run from a checkout, and users read the stubs, which already carry
   everything the log is for. Later, and probably after the toolgen quartet is
   spun out as its own library: an auto-download option and local regeneration
   matching your own machine exactly. Keep the store inside the
   `_toolspec`/`_toolhelp`/`_stubgen`/`_drivers` neighbourhood so that spin-out
   stays a `git mv` (see the standalone-stubgen memory).
9. **A stub-only release is a patch bump**, with the CHANGELOG entry written
   from the events themselves.

## 7. Still open

Nothing blocking. Two things deliberately deferred:

1. **Refresh cadence** — config, not design (Willem, 2026-07-26). Decide it
   when the workflow is written.
2. **Per-tool budget values** — a data edit on the drivers, best chosen once
   the prime has been run wide enough to know what a tool costs.

Everything else is decided (§6) and the file shape is worked out against real
extractions (§1), so the first commit is the seeding step in §8.

## 8. Where to start

**Steps 1 and 2 landed 2026-07-27.** `_toolhistory.py` holds the format
(surface serialisation, delta, replay, load/save); `tools.sync` records its
reading and renders the stub from the history; all 26 stubs regenerate
byte-identical through the round-trip. The store sits in `tool-history/`,
outside `src/`, and the built wheel was checked to confirm it does not ship.
**Step 3 landed 2026-07-27** for the PyPI tier: `fm tools.prime` walks
backwards, resumably, and prek's checked-in history carries ten real
releases. Ordering needed one refinement the plan did not anticipate —
same-day releases are common, so the sort is (date, version) rather than
date alone; resolved by index order the walk skipped 0.4.8 and would have
appended it below its own successor on the next run.

**Rendering the union landed the same day**: stubs carry every option ever,
annotated `Added in X` / `Gone since Y` where the chain can prove it, and the
reference pages inherit it because they render from the stub docstrings. An
option present at the floor carries no `since` — the honesty rule, enforced
in code rather than remembered.

What remains: the other tiers (bun/node, GitHub, GitLab release assets), the
per-tool budget as a driver field, the release gate reading the deltas, and
the scheduled refresh itself.

Not the prime — it is the expensive, network-bound, most-likely-to-stall part.
Start where the data already exists:

1. Seed each tool's `_history/<tool>.json` with a base and no deltas, at the
   version its current stub records — the surface is already extractable, and
   `observed_from` states the floor honestly. A history of one release is a
   valid history, which is the point: the format degrades to "what we know
   today" rather than requiring the prime to mean anything.
2. Switch `_stubgen` to render from the history rather than a live `ToolSpec`.
   Nothing user-visible changes; the stubs should regenerate byte-identical
   apart from the header, which proves the schema against all 32 tools.
3. Only then teach the fetchers to walk backwards, which turns the seed into
   history and needs no change to anything above it.

That sequencing makes the risky part optional: the refresh becomes "append to
a file that already exists", the gate becomes "is this delta non-empty", and a tool
that is never primed simply has a short history rather than a broken one.

## Until the job exists

Stubs are resynced **just before a release**, not when audit notices (Willem,
2026-07-26), which is now step 2 of the Releasing runbook in `CLAUDE.md`:

```sh
fm tools.provision
fm tools.audit --prefix .tools-latest    # what moved → the CHANGELOG line
fm tools.sync  --prefix .tools-latest
```
