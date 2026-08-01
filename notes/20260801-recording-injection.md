# Scripted replies for recording() — first-class output/failure injection

Status: PLAN — nothing built. Decisions marked **open** await Willem's
call. Origin: hse's 0.29.1 conversion report, twice: three of their
suites assert on tool *output* (parsing `uv tool list`), on *failure*
(a build failure restoring the README), and on *environment* (a var
not leaking into an install) — and `recording()` can do none of that,
so they built a `FakeTool` in `hse.devkit.testing`. Their acceptance
test is the right one and is adopted here verbatim: **when this
ships, that fake becomes deletable.**

## Why the old doubles stopped working

Patching a module's `run` no longer intercepts bridge calls: the
bridge binds `from footman.context import run as _run` at import, so
neither a caller-module patch nor a late `footman.context.run` patch
reaches it. That is not a regression to fix — the alias is deliberate
(`tools.run` must never resolve to the function) — it is why the
faking lane has to be footman's own.

## The shape

`recording()` accepts scripted replies; a `run()`/`tools.*` call that
matches one is faked *with that reply* instead of the blank success:

```python
with recording(replies=[
    on("uv tool list", stdout=LISTING),
    on("uv build", code=1, stderr="boom"),
]) as steps:
    ...
```

Sketch, not signature — every part below is open.

## Decision points

1. **Keying.** Sequence-position is brittle; a matcher against the
   *normalised shown command* (`Result.command`, what recordings
   already assert on) keeps one vocabulary for both directions —
   telling the tape what happened and asking it what happened.
   **Open**: substring vs glob vs predicate; lean substring-or-
   predicate, the two ends of the ladder, nothing between.
2. **Reply shape.** `code`/`stdout`/`stderr` kwargs, not a
   hand-built `Result` (Results are sealed, minted by the runtime).
   **Open**: whether a reply can also set `timed_out`.
3. **Failure semantics.** A scripted non-zero must behave exactly as
   live: raises `RunFailed` unless `nofail=True`, seats a failed step,
   fail-fast sees it. Anything less and the double lies about the lane
   it doubles. Lean: not open, this is the point.
4. **Reviewers run.** `pre_record=` hooks review the scripted draft —
   a reviewer is part of the story being tested (hse's djlint-style
   exit-code adjudication wants testing *against* scripted exits).
   Lean yes.
5. **Off-the-record calls.** Today `recorded=False` calls *execute*
   under `recording()` — "a value read is not the story". But hse's
   `uv tool list` parse IS a value read; without interception their
   suite still shells out. Lean: an explicit reply match intercepts
   even off-record calls — the test author declaring the world beats
   honest execution — while unmatched off-record calls keep executing.
   **Open**, and the subtlest call here.
6. **Unmatched recorded calls** keep today's blank success — the
   existing suites must not notice this feature shipping.
7. **Consumption.** A reply answering once vs every match. Lean: every
   match (a listing asked twice is the same listing), with an optional
   `times=`/ordered mode only if a real suite needs it. **Open.**
8. **`Runner.invoke` channel.** The same `replies=` on Runner, so
   branded-CLI suites test identically. Lean yes.
9. **In-process tools** under recording are faked like spawns; a
   matching reply supplies their output the same way. Lean yes.
10. **Naming.** `replies=` / `script=` / `answers=`. **Open.**

## Non-goals

- Not a general mocking framework: no call-count assertions, no
  argument capture beyond what `steps` already records.
- Not for live runs: replies exist only inside `recording()` (and
  `Runner.invoke` if 8 lands). `--dry-run` stays exactly as it is —
  a rehearsal of the real world, never a scripted one.

## Related but separate

- `tools.python.at(path)` — hse's identity-channel suggestion
  (executable selection must not ride `.opts()`, which is policy).
  Separate small build, undecided.
- Docs seams from the same report, cheap and independent: state the
  placement rule plainly on the bridge page (call keywords land after
  positionals; `.flags()` hoists to tool level, before any
  subcommand), and warn that `Result.raw` is the platform-exact
  spelling — parse `stdout`, never `raw`.
