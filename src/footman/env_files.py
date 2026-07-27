"""Load a .env file into the run's environment — pulled, never ambient.

`plugin("footman.env_files")` in a tasks file switches it on; unpulled, it is
inert metadata like any other plugin. At the invocation's single-threaded
moment the file is read and every key the environment does not already carry
is set — **env wins**, so a real environment variable always beats the file,
and a run is never surprised by a checkout. Parsing is python-dotenv's (an
optional dependency, taught by name when it is missing), with interpolation
off: a value is the text on its line.

The default file is `.env` in the invocation's directory; `--env-file=PATH`
names another (path-typed, so completion offers files). The named file must
exist — a missing default is nothing to do, a missing *named* file is a
refusal. The manifest refresh child runs this hook too, with no command
line, so what completion bakes reflects the default file — availability is
re-checked live at execution either way.
"""

from __future__ import annotations

import os
from pathlib import Path

import footman
from footman import GlobalOption

ENV_FILE = GlobalOption(
    "env-file", Path, help="the .env file to load (default: ./.env)"
)


@footman.pre_tasks
def load(inv: footman.Invocation) -> None:
    raw = inv.cli.get("env_file")
    explicit = isinstance(raw, str)
    path = Path(str(raw)) if explicit else Path(inv.cwd or ".") / ".env"
    if not path.is_file():
        if explicit:
            footman.fail(f"--env-file: {path} does not exist")
        return  # no .env is nothing to do, not a problem
    try:
        from dotenv import dotenv_values
    except ImportError:
        footman.fail(
            "plugin('footman.env_files') needs the python-dotenv package — "
            "add it to the project's environment (uv add python-dotenv), "
            "or drop the pull"
        )
    for key, value in dotenv_values(path, interpolate=False).items():
        if value is not None:
            os.environ.setdefault(key, value)  # env wins, always
