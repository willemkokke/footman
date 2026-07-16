"""Public parameter markers: dynamic completion (`suggest`) and one-or-more
(`Many`). Both are used inside annotations and carry no runtime weight beyond a
small marker object.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

#: Metadata sentinel placed in ``Annotated`` by :data:`Many` to mark a list
#: parameter as "one or more" (variadic when positional).
MANY = "footman.many"


class suggest:
    """Attach a dynamic completer to a parameter, via ``Annotated``::

        def build(project: Annotated[str, suggest(list_projects)]): ...

    ``list_projects() -> list[str]`` returns the candidate values. footman runs
    it on the execution path — refreshing a cache the completion hot path serves
    — and, when *strict* (the default), validates the supplied value against a
    fresh call. A bare callable in ``Annotated`` is treated as ``suggest(fn)``.
    """

    __slots__ = ("fn", "strict")

    def __init__(self, fn: Callable[[], Any], *, strict: bool = True) -> None:
        self.fn = fn
        self.strict = strict


if TYPE_CHECKING:
    # Type-checkers see ``Many[X]`` as ``list[X]``; at runtime it expands to an
    # Annotated list carrying the MANY marker.
    Many = list
else:

    class _Many:
        def __class_getitem__(cls, item: Any) -> Any:
            return Annotated[list[item], MANY]

    Many = _Many
