# Hand-written, not generated: cspell is not installed on the machine that
# last ran `fm footman tools sync`, so these flags were read from its
# `--help` by hand. Running sync where cspell *is* installed replaces this
# file with what the tool itself reports.
from typing import Any

from footman.tools import Tool, _Flag

class _Cspell(Tool):
    def lint(
        self,
        *globs: str,
        config: str | None = ...,
        words_only: _Flag = ...,
        quiet: _Flag = ...,
        gitignore: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
