"""Public application entry: build a custom-branded CLI on top of footman.

footman's own `fm` / `footman` commands are just the default-branded
`App`. Point your own console script at an `App` carrying your
project's names and version, and every message the user sees — errors,
`--version`, the completion hint — uses them:

```python
# acme/cli.py
from footman import App

app = App(name="Acme", prog="acme", version="1.4.0")

def main() -> None:
    raise SystemExit(app.run())
```

```toml
# your pyproject.toml
[project.scripts]
acme = "acme.cli:main"
```

Tasks are discovered exactly as they are for `fm`: the `tasks.py` cascade
from the repo root down to the current directory.

This module is kept import-light so the completion hot path stays fast: nothing
here imports the registry, the manifest, or the execution layer at module load —
those are deferred into `App.run`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from footman import __version__


@dataclass(frozen=True)
class Brand:
    """The names a CLI shows the user.

    `name` is the long / display name (the `--version` banner); `prog` is
    the short command name (the error prefix and hints); `version` is *your*
    version string; `tasks_file` is the filename your users write tasks in
    (config `tasks` still overrides it per project).
    """

    name: str = "footman"
    prog: str = "fm"
    version: str = __version__
    tasks_file: str = "tasks.py"


DEFAULT_BRAND = Brand()


class App:
    """A branded footman CLI — call `run` from your console-script entry."""

    def __init__(
        self,
        name: str = "footman",
        prog: str = "fm",
        version: str | None = None,
        tasks_file: str = "tasks.py",
    ) -> None:
        self.brand = Brand(
            name=name,
            prog=prog,
            version=version or __version__,
            tasks_file=tasks_file,
        )

    def run(self, argv: list[str] | None = None) -> int:
        """Resolve and run the CLI, returning the process exit code.

        Handles the stdlib-only `--complete` hot path before importing the
        framework, so completion stays fast even through a custom entry point.
        """
        args = list(sys.argv[1:] if argv is None else argv)
        if args and args[0] == "--complete":
            from footman._complete import complete_cli

            return complete_cli(args[1:])
        from footman import _app

        return _app.run(args, brand=self.brand)
