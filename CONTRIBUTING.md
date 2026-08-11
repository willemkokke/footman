# Contributing

Bug reports, questions and corrections are all welcome — including
corrections to the [comparison](https://willemkokke.github.io/footman/comparison/),
which is measured against real tools and should be told when it has gone
stale or unfair.

footman is pre-1.0 and moving quickly. If you are about to write something
large, open an issue first: the surface may already be moving under you, and
it is nicer to say so before you have spent an evening.

## The gate

One command has to pass before anything lands:

```sh
uv run fm check
```

That is `ruff format --check`, `ruff check`, `basedpyright`, and the test
suite under coverage — run in parallel, because that is what footman is for.

**The exit code is the verdict.** Don't pipe it through `tail`, `grep` or
anything else: a pipe replaces the gate's status with the filter's, and a
green tail on a red run is how a broken commit gets pushed. If you want the
output out of the way, redirect it, which keeps the code:

```sh
uv run fm check > /tmp/gate.log 2>&1     # read the log only if it fails
```

If you touched `docs/`, also:

```sh
uv run fm docs.build --check
```

## Two invariants that are not negotiable

- **Zero runtime dependencies.** Nothing under `src/footman/` may import a
  third-party package. Dev, test and docs tooling lives in `uv` groups,
  never in `dependencies`.
- **The completion hot path stays stdlib-only and never imports the
  framework.** A <kbd>Tab</kbd> press is one file read, one JSON parse and a
  tree walk. It does not import footman, and it does not import your tasks.
  That is the whole feature.

Coverage is enforced at 92% on the merged CI matrix.

## Commits and pull requests

- Conventional-commit prefixes: `feat`, `fix`, `docs`, `test`, `refactor`,
  `chore`.
- One logical change per commit. The body explains the root cause and the
  fix, not just the diff.
- CI runs 3 operating systems × Python 3.11–3.14 including free-threaded,
  plus real-shell completion tests. All of it is required to merge.

## Getting set up

```sh
uv sync --all-groups
uv run fm --list
```

The shell-completion tests drive real shells and skip the ones you do not
have installed, so a partial local run is normal — CI has all five.
