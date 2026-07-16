# Getting started

## Install

```sh
uv add --dev footman        # or: pip install footman
```

footman requires Python 3.11+ and has zero runtime dependencies. Installing it
puts two console scripts on your `PATH`: `footman` and the two-letter `fm`.

## Write a tasks file

Tasks are plain functions. A `@task` decorator registers one; a `group()` opens
a nested command group. Put them in a `tasks.py` at your project root:

```python
from footman import task, group

@task
def lint(fix: bool = False):
    """Run ruff over the project."""
    ...

@task
def test(marker: str = "", *pytest_args):
    """Run the test suite (extra pytest args after --)."""
    ...

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    """Serve the docs locally."""
    ...
```

The command name is the function name with underscores turned into hyphens
(`add_word` → `add-word`). A module of functions becomes a flat set of commands;
each `group()` becomes a subcommand.

## Run tasks

```sh
fm lint --fix
fm docs serve --port 8001
fm --list            # every task, flat
fm --tree            # grouped by command group
```

The signature *is* the CLI: `fix: bool = False` becomes a `--fix` flag,
`port: int = 8000` becomes a typed `--port` option, and a parameter with no
default becomes a required positional. See
[Typed signatures](typing.md) for the full mapping.

## Chain several tasks

List more than one task on a line and footman runs them as a chain — no
separator needed, the split is driven by the manifest:

```sh
fm format lint --fix test
```

Independent tasks in the chain run **in parallel by default**; `-s/--sequential`
forces one-at-a-time. See [Chaining & parallelism](orchestration.md).

## Pass arguments through

Everything after `--` is handed to the task as passthrough, reachable via a
`*args` parameter or `passthrough()`:

```sh
fm test -- -k my_test -x
```

## Dry-run the plan

`-n/--dry-run` prints exactly what footman parsed without running anything:

```console
$ fm --dry-run format lint --fix test
  globals: --dry-run
  -> format
  -> lint  --fix
  -> test
```
