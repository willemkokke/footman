"""Public parameter markers, used inside `Annotated` annotations.

Dynamic completion (`suggest`), one-or-more (`Many`), comma-split opt-out
(`nosplit`), path requirements (`exists`/`isfile`/`isdir`), numeric bounds
(`between`, or a bare `range`), environment fallbacks (`env`), and custom
validators (`check`). Each carries no runtime weight beyond a small marker
object; `footman.coerce.peel` reads them all in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class suggest:
    """Attach a dynamic completer to a parameter, via `Annotated`:

    ```python
    def build(project: Annotated[str, suggest(list_projects)]): ...
    ```

    `list_projects() -> list[str]` returns the candidate values. footman runs
    it on the execution path — refreshing a cache the completion hot path serves
    — and, when *strict* (the default), validates the supplied value against a
    fresh call. A bare callable in `Annotated` is treated as `suggest(fn)`.
    """

    __slots__ = ("fn", "strict")

    def __init__(self, fn: Callable[[], Any], *, strict: bool = True) -> None:
        self.fn = fn
        self.strict = strict


# `Many[T]` is exactly `list[T]`: a parameter that is *always* a list — one or
# more values, variadic when positional. It reads more intentfully than a bare
# `list[T]` at a call site, but carries no runtime marker of its own.
Many = list


class _NoSplitMarker:
    """Marker for `nosplit`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "nosplit"


nosplit = _NoSplitMarker()
"""Opt a list/dict parameter OUT of comma-splitting, via `Annotated`:

```python
def build(names: Annotated[list[str], nosplit] = ()): ...
```

By default a collection parameter splits a single token on commas
(`--tag a,b,c` -> `["a", "b", "c"]`) *in addition to* the repeatable form
(`--tag a --tag b`). Mark it `nosplit` when a value may itself contain a comma:
then only the repeated flag adds items and `--name "a,b"` stays the literal
`"a,b"`."""


class _PathRequirement:
    """Marker for `exists` / `isfile` / `isdir`."""

    __slots__ = ("_name", "kind")

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self._name = name

    def __repr__(self) -> str:
        return self._name


exists = _PathRequirement("exists", "exists")
"""Require a `Path` parameter to name something that exists on disk:

```python
def rm(target: Annotated[Path, exists]): ...
```

Validated eagerly (at parse time) with a taught error. See also `isfile`
and `isdir`."""

isfile = _PathRequirement("file", "isfile")
"""Require a `Path` parameter to name an existing *file* (see `exists`)."""

isdir = _PathRequirement("dir", "isdir")
"""Require a `Path` parameter to name an existing *directory* (see `exists`)."""


class between:
    """Inclusive numeric bounds for an `int`/`float` parameter:

    ```python
    def test(jobs: Annotated[int, between(1, 32)] = 4): ...
    ```

    Validated eagerly with a taught error (`--jobs must be between 1 and 32`).
    Either bound may be `None` for open-ended ranges. A bare `range` in
    `Annotated` also works for ints, with Python's half-open semantics
    (`range(0, 8)` accepts 0 through 7).
    """

    __slots__ = ("hi", "lo")

    def __init__(self, lo: float | None, hi: float | None) -> None:
        self.lo = lo
        self.hi = hi


class env:
    """Fall back to an environment variable when the option isn't given:

    ```python
    def deploy(target: Annotated[str, env("DEPLOY_ENV")] = "staging"): ...
    ```

    Precedence is CLI > `$DEPLOY_ENV` > default. The env value flows through
    the same coercion and validation as a command-line token. Only valid on a
    parameter with a default — an env fallback *makes* it optional, so it
    needs somewhere to fall.
    """

    __slots__ = ("var",)

    def __init__(self, var: str) -> None:
        self.var = var


class check:
    """A custom validator, run after coercion; raise `ValueError` to reject:

    ```python
    def tag(version: Annotated[str, check(semver)]): ...
    ```

    The callable receives the coerced value (each element, for collections).
    Its `ValueError` message is shown to the user, so write it for them.
    """

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self.fn = fn
