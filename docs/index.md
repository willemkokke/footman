---
title: A typed task runner with instant completion
---

# footman

[![PyPI version](https://img.shields.io/pypi/v/footman?label=PyPI&color=blue)](https://pypi.org/project/footman/)
[![Python versions](https://img.shields.io/pypi/pyversions/footman)](https://pypi.org/project/footman/)
[![License](https://img.shields.io/pypi/l/footman)](https://github.com/willemkokke/footman/blob/main/LICENSE)

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and the
UX of [typer](https://typer.tiangolo.com/): typed function signatures become
real flags and positionals, modules become nested command groups, and shell
completion answers from a cached manifest in **~19 ms — without importing your
code**.

```sh
fm lint --fix
fm format lint --fix test          # a chain: three tasks, no separator
fm workspace mount --share <TAB>   # main  scratch  archive
```

Ships two console scripts: `footman` and the two-letter `fm`.

!!! warning "Very early code"

    footman is alpha and moving fast — the public API, the decorator surface,
    the manifest format, and the CLI grammar can all change without notice or a
    deprecation cycle. Pin an exact version if you build on it.

## Why

`duty` got a lot right — the `ctx.run` capture model, the tools wrappers, the
decorator ergonomics — and footman keeps those ideas. Where it pushes is the
parts that compound:

- **Completion that answers from a cache** instead of re-importing your whole
  project on every <kbd>Tab</kbd> (~15× faster in practice).
- **Eager type and choice validation**, including unions and dynamic value sets.
- **Native command groups** — modules become nested subcommands.
- **No `ctx` boilerplate** in task signatures.
- **A DAG scheduler** that runs independent tasks in parallel by default (duty
  and invoke can't).
- **A monorepo task cascade** that merges a `tasks.py` per folder from the repo
  root down to where you stand.

A measured head-to-head against duty, invoke, poe, and typer lives in the
repository's `comparison/` directory — modern duty has real flags and chaining,
so the gap is validation, ergonomics, and completion latency, not grammar.

## Install

```sh
uv add --dev footman        # or: pip install footman
```

Requires Python 3.11+. Zero runtime dependencies.

## A first taste

Write a `tasks.py` in your project root:

```python
from footman import task, group

@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ...

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    "Serve the docs locally."
    ...
```

Then:

```sh
fm lint --fix
fm docs serve --port 8001
fm --list
```

Head to [Getting started](getting-started.md) to go deeper.
