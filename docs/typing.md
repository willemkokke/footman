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

`Many[T]` accepts either a single value or several, collapsing to a scalar when
one is given and a list when more are:

```python
from footman import Many

@task
def build(target: Many[str]): ...    # fm build web   -> "web"
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

## Dynamic completion

`suggest` attaches a completer that runs on the execution path (its results are
cached into the manifest), so <kbd>Tab</kbd> stays instant while still offering
live values:

```python
from footman import task, suggest

def shares() -> list[str]:
    return ["main", "scratch", "archive"]

@task
def mount(share: suggest[str, shares]): ...
```
