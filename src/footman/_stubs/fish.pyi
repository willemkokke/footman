# Hand-written: a shell is invoked to run a command *string*, not for its own
# flags, so `fm footman tools sync` never touches this file (its driver is
# `source="manual"`).
from typing import Any

from footman.tools import Tool

class Fish(Tool):
    def __call__(  # type: ignore[override]
        self,
        command: str,
        /,
        *,
        nofail: bool = False,
        **flags: Any,
    ) -> int:
        """Run a command string in fish — `fish -c "<command>"`.

        A real shell: pipes, redirects, globbing and `$VAR` all work. Reach
        for this when you deliberately want a shell; `run("…")` stays
        shell-free.

        Args:
            command: the command line to run in fish.
        """
        ...
