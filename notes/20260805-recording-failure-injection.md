# Failure injection in recordings

Status: FEATURE REQUEST — analysis only, nothing designed or built.
The ask (outstanding, not yet filed as an issue): a test using
`recording()` should be able to introduce failure points — designate
that specific recorded calls yield a failing `Result` (chosen code,
chosen stderr) instead of the default success shape — so a task
body's error paths (`nofail`, `keep_going`, fail-fast, Result
adjudication) can be exercised without live tools.

Written 2026-08-05, the day the toolroom split was fully ruled
([20260801-tools-namespace-package.md](20260801-tools-namespace-package.md),
[20260801-tools-separate-repo.md](20260801-tools-separate-repo.md)),
because the interaction analysis between the two threads is worth
keeping: the split barely touches this feature, and the feature
stress-tests two of the split's rulings and validates both.

## Where it lives

`recording()` is a Context mode: each `run()`/`tools.*` call inside
the block appends a `Result` instead of executing. Injection is
therefore an execution-lane feature at the `run()` door — it lands in
footman, nothing of it moves to toolroom, no shared machinery
appears. After the split, every bridge call still passes that door
via host detection, so injection covers `tools.*` calls without
toolroom knowing the feature exists.

## How it interacts with the split

- **Host detection is load-bearing.** Mount-dependent executor wiring
  would let a test without `plugin("toolroom")` silently bypass the
  recording — injected failures would never fire. The docstring's
  "each `run()`/`tools.*` call appends" promise survives the split
  *because* routing is detected, not mounted.
- **The one guardrail the split imposes:** the matching/addressing
  API must speak stdlib — positions, token tuples, string patterns —
  never bridge types (no `isinstance(step, toolroom.Tool)`).
  toolroom vocabulary inside footman would recreate the arrow the
  inversion removed. A recorded step's identity is its token list, so
  this costs nothing.
- **Injected failures exercise the real paths.** The faked `Result`
  is footman's sealed class (the bridge never constructs one), so an
  injected failure flows back through the bridge into
  `nofail`/`keep_going`/fail-fast handling exactly like a live one.
- **Standalone toolroom needs nothing.** The Executor protocol *is* a
  failure point: a fake executor returns whatever failure a
  standalone test likes. Any standalone testing ergonomics are a
  toolroom-side convenience over the seam — later, if ever.

## Consequences for sequencing and surface

- **Build it in footman, before the split.** It hardens the
  `run()`-door contract the split leans on, and toolroom's post-split
  CI (dogfooding a *released* footman) wants exactly this to test
  provision/audit error paths.
- **It is seam-adjacent public surface from day one.** toolroom's CI
  and hse will pin it — design it as public `footman.testing` API
  with a stability note, not an internal hook.
- **The deliberate blind spot stays.** `installed_version()` sits
  outside the task context "so dry-run and recording can't lie to
  it", and the split preserves that property. Injection therefore
  cannot simulate a missing or outdated tool — if the request
  includes that scenario, it is a separate availability-faking lane
  to scope explicitly, not a bolt-on to recording.

## The honesty line

The `step=False` arc taught that report-shaping switches in
production get counterfeited. Recordings are the other side of the
line — sanctioned test doubles whose job is to lie. Failure injection
there is the honest lane; no counterfeit risk.

## Open (the feature itself is undesigned)

- The addressing surface: by index, by token pattern, or both.
- The failure payload: code + stderr (+ stdout?); one-shot vs
  every-match.
- Whether availability faking (the missing-tool scenario) is wanted
  as its own lane, or explicitly out of scope.
