# Hand-written, not generated: prek is not installed on the machine that
# last ran `fm footman tools sync`, so these flags were read from its
# `--help` by hand. Running sync where prek *is* installed replaces this
# file with what the tool itself reports.
from collections.abc import Sequence
from typing import Any

from footman.tools import Tool, _Flag

class _Prek(Tool):
    def run(
        self,
        *hooks: str,
        all_files: _Flag = ...,
        files: Sequence[str] | None = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(self, *, nofail: bool = False, **flags: Any) -> int: ...
