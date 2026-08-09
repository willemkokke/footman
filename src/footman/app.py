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

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from footman import __version__


def _prefix_from(name: str) -> str:
    """An environment-variable prefix from a display *name*.

    Uppercased, every run of non-alphanumerics collapsed to one `_`, and a
    leading digit padded — `"footman"` gives `FOOTMAN`, so footman's own
    variables are exactly what they always were. Written with `str` methods
    rather than `re`, which this import-light module should not pay for.
    """
    out: list[str] = []
    for char in name:
        if char.isalnum() and char.isascii():
            out.append(char.upper())
        elif out and out[-1] != "_":
            out.append("_")
    cleaned = "".join(out).strip("_")
    if not cleaned:
        return "FOOTMAN"
    return f"_{cleaned}" if cleaned[0].isdigit() else cleaned


@dataclass(frozen=True)
class Brand:
    """The names a CLI shows the user, and where it keeps its things.

    `name` is the long / display name (the `--version` banner); `prog` is
    the short command name (the error prefix and hints); `version` is *your*
    version string; `tasks_file` is the filename your users write tasks in
    (config `tasks` still overrides it per project).

    `dist` is the distribution that ships your CLI — the name a user would
    `pip install`. It is what the handoffs reason about: which package a
    project's lockfile must pin, and which one a tasks file carrying its
    own PEP 723 dependencies must declare. Left `None` (the default for a
    custom brand), both handoffs simply stay out of your way.

    `home` is where this CLI keeps everything it owns — completion
    manifests, timing history, fetched files, the collector stamp, the
    user-level config file and the user tasks file. **You** compute it, so
    footman never has to guess at your product's layout:

    ```python
    home=Path(os.environ.get("ACME_HOME", Path.home() / ".acme")) / "runner"
    ```

    Left `None` (the default), locations fall back to XDG plus this brand's
    own environment variables, exactly as footman has always behaved.

    `home_env` names an environment variable that overrides `home` when it
    is set — for running two installations side by side under different
    identities. You name it; footman never infers one, because a product's
    own `*_HOME` usually means more than the runner's corner of it.

    `env_prefix` is the namespace for this CLI's environment variables,
    defaulting to `name` uppercased (`acme` reads `ACME_CACHE_DIR`). A
    branded CLI reads **only** its own prefix: a stray `FOOTMAN_CACHE_DIR`
    must never move someone else's product. Set it explicitly when your
    display name would make an awkward or unstable prefix.

    `config_name` names both the standalone config file (`acme.toml`) and
    the table inside `pyproject.toml` (`[tool.acme]`), from one field so
    the two cannot drift apart.
    """

    name: str = "footman"
    prog: str = "fm"
    version: str = __version__
    tasks_file: str = "tasks.py"
    dist: str | None = "footman"
    home: Path | None = None
    home_env: str | None = None
    env_prefix: str | None = None
    config_name: str = "footman"

    @property
    def prefix(self) -> str:
        """The environment-variable namespace — `env_prefix`, else from `name`."""
        return self.env_prefix or _prefix_from(self.name)

    def env(self, suffix: str) -> str:
        """This brand's spelling of an environment variable: `ACME_CACHE_DIR`."""
        return f"{self.prefix}_{suffix}"

    def resolved_home(self) -> Path | None:
        """`home_env`'s value when set, else `home`; `None` means XDG fallback.

        Read per invocation rather than cached, so two processes launched
        with different values genuinely get different homes.
        """
        if self.home_env:
            override = os.environ.get(self.home_env)
            if override:
                return Path(override).expanduser()
        return self.home

    def config_file(self) -> str:
        """This brand's standalone config filename — `acme.toml`."""
        return f"{self.config_name}.toml"


DEFAULT_BRAND = Brand()


class App:
    """A branded footman CLI — call `run` from your console-script entry."""

    brand: Brand

    def __init__(
        self,
        name: str = "footman",
        prog: str = "fm",
        version: str | None = None,
        tasks_file: str = "tasks.py",
        dist: str | None = None,
        home: Path | str | None = None,
        home_env: str | None = None,
        env_prefix: str | None = None,
        config_name: str | None = None,
    ) -> None:
        # `dist` is opt-in for a branded CLI: footman cannot guess which
        # distribution ships someone else's runner, and a wrong guess would
        # hand a user's invocation to an environment without it. Unset, the
        # handoffs stay out of the way (documented in custom-cli.md).
        self.brand = Brand(
            name=name,
            prog=prog,
            version=version or __version__,
            tasks_file=tasks_file,
            dist=dist,
            home=Path(home).expanduser() if home is not None else None,
            home_env=home_env,
            env_prefix=env_prefix,
            # Default to `name`, so a branded CLI's users write `acme.toml`
            # rather than a config file named after a dependency.
            config_name=config_name if config_name is not None else name,
        )

    def run(self, argv: list[str] | None = None) -> int:
        """Resolve and run the CLI, returning the process exit code.

        Handles the stdlib-only `--complete` hot path before importing the
        framework, so completion stays fast even through a custom entry point.
        """
        args = list(sys.argv[1:] if argv is None else argv)
        # Locations first, and on both paths: the completion hot path reads
        # this brand's cache exactly as the execution path writes it.
        from footman import _paths

        _paths.configure(
            prefix=self.brand.prefix,
            home=self.brand.resolved_home(),
            config_name=self.brand.config_name,
            tasks_file=self.brand.tasks_file,
        )
        if args and args[0] == "--complete":
            from footman._complete import complete_cli

            return complete_cli(args[1:])
        from footman import _app

        return _app.run(args, brand=self.brand)
