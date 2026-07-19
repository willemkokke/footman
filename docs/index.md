---
title: A typed task runner with instant completion
---

# Footman

[![PyPI version](https://img.shields.io/pypi/v/footman?label=PyPI&color=blue)](https://pypi.org/project/footman/)
[![Python versions](https://img.shields.io/pypi/pyversions/footman)](https://pypi.org/project/footman/)
[![License](https://img.shields.io/pypi/l/footman)](https://github.com/willemkokke/footman/blob/main/LICENSE)
[![Docs built with Zensical](https://img.shields.io/badge/docs-Zensical-4051b5)](https://zensical.org)

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and the
UX of [typer](https://typer.tiangolo.com/): typed function signatures become
real flags and positionals, modules become nested command groups, and shell
completion answers from a cached manifest in **~25 ms — without importing your
code**.

```sh
fm lint --fix
fm format lint --fix test          # a chain: three tasks, no separator
fm workspace mount --share <TAB>   # main  scratch  archive
```

![fm --list in a terminal: bold task names, dim group prefixes, one-line help](_generated/shots/list.svg)

Ships two console scripts: `footman` and the two-letter `fm`. (That
screenshot is generated from the real CLI on every docs build — like every
terminal image on this site, it cannot drift from what footman actually
prints.)

!!! note "Beta"

    footman is pre-1.0: the surface is settling, but minor versions may still
    include breaking changes — always called out in the
    [changelog](changelog.md), never in a patch release. Pin the minor
    (`footman~=0.13.0`) if you build on it. The written stability promise
    lands with 1.0 — the road there is on the [roadmap](roadmap.md).

--8<-- "docs/_generated/latest-changes.md"

The full history lives in the [changelog](changelog.md).

## Why

`duty` gets a lot right — the `run()` capture model, the tools wrappers, the
decorator ergonomics — and footman keeps those ideas. Where it pushes is the
parts that compound:

- Completion answers from a cache instead of re-importing your whole project
  on every <kbd>Tab</kbd> — ~15× faster, measured.
- Types and choices validate eagerly, including unions and dynamic value
  sets, with errors that teach.
- Modules become nested command groups, and task signatures carry no `ctx`
  boilerplate.
- Independent tasks run in parallel by default, scheduled from the chain and
  each task's `pre`/`post` dependencies — duty and invoke run these serially.
- A monorepo task cascade merges a `tasks.py` per folder, from the repo root
  down to where you stand.

The receipts live in the [comparison](comparison.md) — a measured
head-to-head against duty, invoke, poe, and typer, every number reproducible
from the repo's `comparison/` directory.

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
