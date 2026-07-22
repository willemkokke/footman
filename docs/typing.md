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
| `template: Path`                | required positional (consumed by exact count)       |
| `*cmd: str`                     | variadic trailing passthrough                       |

## Unions and one-or-many values

A parameter can accept a union of types; footman validates the value against the
union and coerces it by specificity — the most specific member that accepts the
value wins (`int` → `float` → `Path` → `str`, with `str` as the universal
fallback):

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
from footman import task, between, check, doc, env, isfile

@task
def deploy(
    config: Annotated[Path, isfile],                       # must exist, be a file
    jobs: Annotated[int, between(1, 32)] = 4,              # inclusive bounds
    target: Annotated[str, env("DEPLOY_ENV")] = "staging", # CLI > $DEPLOY_ENV > default
    version: Annotated[str, check(semver)] = "0.0.0",      # your own validator
    force: Annotated[bool, doc("skip the health check")] = False,  # help text
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
  bare `range(0, 8)` also works for ints, with Python's half-open semantics
  (`0` through `7`; the end is excluded, exactly as in a `for` loop).
- **Env fallbacks** — `env("VAR")` fills an *absent* option from the
  environment; the value flows through the same coercion, bounds, and checks
  a command-line token would (just at binding time — the parser never sees
  the environment). Only valid on a parameter with a default, because a
  fallback needs somewhere to fall.
- **Custom validators** — `check(fn)` runs after coercion, per element for
  collections; raise `ValueError` with a message written for the user.
- **Help text** — `doc("…")` puts one line of your own words on a
  parameter. It leads the option's line in `fm --help <task>`, becomes the
  option's description in shells that render one (zsh, fish, nushell,
  PowerShell tooltips), and rides along in the `fm --json --list` catalog.
  The task's own help stays the docstring's first line; `doc` is for the
  parameters.

## Terse aliases, and forwarding

A **bare** marker — one that takes no arguments — has a `Name[T]` shorthand, the
way `Many[T]` reads better than `list[T]`:

- `NoSplit[list[str]]` ≡ `Annotated[list[str], nosplit]`.
- `Exists`, `IsFile`, `IsDir` ≡ `Annotated[Path, exists/isfile/isdir]` — bare,
  no subscript, since the type is always `Path`: `def rm(target: Exists)`.
- `Forward[T]` ≡ `Annotated[T, forward]`.

Arg-taking markers (`suggest`, `between`, `env`, `check`, `doc`, `ask`) keep the
full `Annotated[...]` form — their value can't ride in a type subscript.

The `forward` marker threads a parameter's value to the tasks a task dispatches —
its `pre`/`post` prerequisites and a runnable group's surfaces. It's an
orchestration tool, covered in
[Chaining & parallelism](orchestration.md#forward-a-value-to-what-a-task-dispatches).
Markers compose by listing them: `Annotated[bool, ask("Fix?"), forward]` both
prompts for the value and forwards it — one prompt at the top, the answer
flowing down.

## Or just write a docstring

footman reads the parameter docs you already write — Google, NumPy, and
Sphinx styles, auto-detected per docstring. Everything a `doc("…")` marker
feeds (help lines, completion descriptions, the catalog) fills from the
docstring instead, the body between the summary and the section renders in
`fm --help <task>` as the task's long help, and an explicit `doc("…")`
always wins over a docstring entry for the same parameter:

=== "Google"

    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        Checks out, builds, and uploads — see the release runbook.

        Args:
            target: where to deploy
            fix: apply fixes first
        """
    ```

=== "NumPy"

    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        Parameters
        ----------
        target : str
            where to deploy
        fix : bool
            apply fixes first
        """
    ```

=== "Sphinx"

    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        :param target: where to deploy
        :param fix: apply fixes first
        """
    ```

A docstring entry that names no real parameter earns a `UserWarning` — the
same loudness a broken annotation gets. The parser itself is public and
standalone (`footman.docstrings.parse`) if you want structured docstrings
for your own tooling.

One honest asymmetry to know about: path and bounds violations on the command
line are caught *eagerly* (before anything runs); the same violations in an
env-supplied value are caught at binding time, because that's when the
environment is read.

## Dynamic completion

`suggest` attaches a completer — a function that returns live values (git
branches, deploy targets, the shares below). footman runs it **fresh** each time
you complete that value, in a short-lived subprocess, rather than serving a copy
baked into the manifest: a value you <kbd>Tab</kbd> to answer a build-critical
question must be current, not a snapshot from your last run. The recompute is
bounded and isolated, so a slow or failing completer degrades to no candidates —
never the old values, never a hung keystroke. This holds for *every* completer,
whether or not the task owns the terminal (`interactive=True`); a real run
validates the value you pass against the same live call.

```python
from typing import Annotated
from footman import task, suggest

def shares() -> list[str]:
    return ["main", "scratch", "archive"]

@task
def mount(share: Annotated[str, suggest(shares)]): ...
```

Keep a completer's imports **inside its body**, the way footman keeps optional
dependencies out of a task's import path. Loading your tasks file stays cheap —
the completer's cost (a subprocess, a network round-trip) is paid only when it
runs, not every time the file is imported:

```python
def branches() -> list[str]:
    import subprocess  # here, not at module top

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True, text=True,
    )
    return out.stdout.split()
```

The first example, recorded in PowerShell: the demo project's tasks.py is
extracted from this page at build time, so the code above and the session below
cannot disagree. <kbd>Tab</kbd> offers what `shares()` returned; <kbd>Tab</kbd>
again walks the menu.

![Animated: fm mount TAB offers main, scratch, archive from the suggest completer; TAB again moves the selection](_generated/shots/pwsh-suggest-cast.svg)
