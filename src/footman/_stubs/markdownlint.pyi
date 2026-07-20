# Hand-written, not generated: markdownlint is not installed on the machine that
# last ran `fm footman tools sync`, so these flags were read from its
# `--help` by hand. Running sync where markdownlint *is* installed replaces this
# file with what the tool itself reports.
from typing import Any

from footman.tools import Tool, _Flag

class _Markdownlint(Tool):
    def __call__(  # type: ignore[override]
        self,
        *globs: str,
        fix: _Flag = ...,
        config: str | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
