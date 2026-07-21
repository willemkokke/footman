# Hand-written, not generated: bun is not installed on the machine that
# last ran `fm footman tools sync`, so these flags were read from its
# `--help` by hand. Running sync where bun *is* installed replaces this
# file with what the tool itself reports.
from typing import Any

from footman.tools import Tool, _Flag

class Bun(Tool):
    def add(
        self,
        *packages: str,
        dev: _Flag = ...,
        global_: _Flag = ...,
        nofail: bool = False,
        **flags: Any,
    ) -> int: ...
    def install(
        self, *, frozen_lockfile: _Flag = ..., nofail: bool = False, **flags: Any
    ) -> int: ...
    def run(
        self, script: str, *args: str, nofail: bool = False, **flags: Any
    ) -> int: ...
    def build(self, *entrypoints: str, nofail: bool = False, **flags: Any) -> int: ...
