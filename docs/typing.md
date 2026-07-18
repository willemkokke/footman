# Typed signatures

footman reads your function signature and turns it into a CLI — the same idea
typer popularised, applied to a task runner. Types are validated *eagerly*, at
parse time, with taught error messages.

## The core mapping

| Signature                       | CLI shape                                            |
| ------------------------------- | ---------------------------------------------------- |
| `fix: bool = False`             | flag `--fix` / `--no-fix`                            |
| `mode: str = "loose"`           | option `--mode VALUE`                               |
| `mode: Literal["a", "b"]`       | completable, eagerly-validated choices              |
| `count: int = 100`              | typed option, validated at parse time               |
| `paths: list[Path] = ()`        | repeatable or comma-separated (`--paths a,b`)       |
| `env: dict[str, int]`           | `--env KEY=VAL` pairs (repeatable or comma-separated)|
| `template: Path`                | required positional (exact arity)                   |
| `*cmd: str`                     | variadic trailing passthrough                       |

## Unions and one-or-many values

A parameter can accept a union of types; footman validates the value against the
union and coerces it by specificity (`int` → `float` → `Path` → `str`, with
`str` as the universal fallback):

```python
@task
def scale(factor: int | float): ...
```

`Many[T]` is exactly `list[T]` — a parameter that accepts one or more values and
is **always a list**, even for a single value (it reads more intentfully than a
bare `list[T]` at a positional). Required when positional, so at least one value
must be given:

```python
from footman import Many

@task
def build(targets: Many[str]): ...   # fm build web     -> ["web"]
                                      # fm build web api -> ["web", "api"]
```

## Comma-splitting and `nosplit`

Every collection parameter (list or dict) splits a single token on commas **by
default**, on top of the repeatable form — so `--tag a,b,c` and
`--tag a --tag b --tag c` both work. Only `,` is a separator (no alternatives),
and it is shell-portable, including PowerShell:

```python
@task
def release(tags: list[str]): ...   # fm release --tags a,b,c  -> ["a", "b", "c"]
```

When a value may itself contain a comma, mark the parameter `nosplit`: then only
the repeated flag adds items, and a comma stays literal.

```python
from typing import Annotated
from footman import nosplit

@task
def notify(lines: Annotated[list[str], nosplit]): ...
# fm notify --lines "Smith, John" --lines "Doe, Jane"  -> two names, commas kept
```

## Dictionaries

`dict[K, V]` maps `KEY=VALUE` pairs, and it composes with the rest of the type
system — `dict[str, int | str]`, and even `dict[str, list[...]]`:

```python
@task
def env(vars: dict[str, int | str]): ...   # fm env --vars port=8080 --vars name=web
```

## Custom types

Any type with a typed constructor works — footman calls it. `datetime` uses
`fromisoformat`; everything else is constructed as `T(value)`:

```python
from uuid import UUID
from decimal import Decimal
from datetime import datetime

@task
def record(id: UUID, amount: Decimal, when: datetime): ...
```

## Validation markers

Eager, taught validation is the whole pitch, so constraints ride in
`Annotated` — the same idiom as `suggest` and `nosplit`:

```python
from pathlib import Path
from typing import Annotated
from footman import task, between, check, env, isfile

@task
def deploy(
    config: Annotated[Path, isfile],                       # must exist, be a file
    jobs: Annotated[int, between(1, 32)] = 4,              # inclusive bounds
    target: Annotated[str, env("DEPLOY_ENV")] = "staging", # CLI > $DEPLOY_ENV > default
    version: Annotated[str, check(semver)] = "0.0.0",      # your own validator
): ...
```

```console
$ fm deploy missing.toml
fm: deploy: <config> must be an existing file (got 'missing.toml')
$ fm deploy app.toml --jobs 99
fm: deploy: --jobs must be between 1 and 32 (got '99')
$ DEPLOY_ENV=prod fm deploy app.toml      # target == "prod"
```

- **Paths** — `exists`, `isfile`, `isdir` require the value to name something
  real on disk; validated at parse time like a bad choice would be.
- **Bounds** — `between(lo, hi)` is inclusive; either end may be `None`. A
  bare `range(0, 8)` also works for ints, with Python's half-open semantics.
- **Env fallbacks** — `env("VAR")` fills an *absent* option from the
  environment; the value flows through the same coercion, bounds, and checks
  a command-line token would (just at binding time — the parser never sees
  the environment). Only valid on a parameter with a default, because a
  fallback needs somewhere to fall.
- **Custom validators** — `check(fn)` runs after coercion, per element for
  collections; raise `ValueError` with a message written for the user.

One honest asymmetry to know about: path and bounds violations on the command
line are caught *eagerly* (before anything runs); the same violations in an
env-supplied value are caught at binding time, because that's when the
environment is read.

## Dynamic completion

`suggest` attaches a completer that runs on the execution path (its results are
cached into the manifest), so <kbd>Tab</kbd> stays instant while still offering
live values:

```python
from typing import Annotated
from footman import task, suggest

def shares() -> list[str]:
    return ["main", "scratch", "archive"]

@task
def mount(share: Annotated[str, suggest(shares)]): ...
```
