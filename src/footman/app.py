"""Public application entry: build a custom-branded CLI on top of footman.

footman's own ``fm`` / ``footman`` commands are just the default-branded
:class:`App`. Point your own console script at an ``App`` carrying your
project's names and version, and every message the user sees — errors,
``--version``, the completion hint — uses them::

    # hse/cli.py
    from footman import App

    app = App(name="HSE", prog="hse", version="1.4.0")

    def main() -> None:
        raise SystemExit(app.run())

.. code-block:: toml

    # your pyproject.toml
    [project.scripts]
    hse = "hse.cli:main"

Tasks are discovered exactly as they are for ``fm``: the ``tasks.py`` cascade
from the repo root down to the current directory.

This module is kept import-light so the completion hot path stays fast: nothing
here imports the registry, the manifest, or the execution layer at module load —
those are deferred into :meth:`App.run`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from footman import __version__


@dataclass(frozen=True)
class Brand:
    """The names a CLI shows the user.

    ``name`` is the long / display name (the ``--version`` banner); ``prog`` is
    the short command name (the error prefix and hints); ``version`` is *your*
    version string.
    """

    name: str = "footman"
    prog: str = "fm"
    version: str = __version__


DEFAULT_BRAND = Brand()


class App:
    """A branded footman CLI — call :meth:`run` from your console-script entry."""

    def __init__(
        self,
        name: str = "footman",
        prog: str = "fm",
        version: str | None = None,
    ) -> None:
        self.brand = Brand(name=name, prog=prog, version=version or __version__)

    def run(self, argv: list[str] | None = None) -> int:
        """Resolve and run the CLI, returning the process exit code.

        Handles the stdlib-only ``--complete`` hot path before importing the
        framework, so completion stays fast even through a custom entry point.
        """
        args = list(sys.argv[1:] if argv is None else argv)
        if args and args[0] == "--complete":
            from footman._complete import complete_cli

            return complete_cli(args[1:])
        from footman import _app

        return _app.run(args, brand=self.brand)
