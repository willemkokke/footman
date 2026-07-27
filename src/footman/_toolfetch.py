"""Past releases of a curated tool, for priming its option history.

Provisioning fetches the *latest* of everything into one prefix. Priming is
the other direction: one specific release at a time, into a throwaway
environment, read once and thrown away — walking backwards from the newest
because the current version is the one that matters most, and because a
backward walk appends to the history rather than rewriting it.

Only the PyPI (`uv`) tier is implemented. The other tiers each need their own
release index and download shape, and a tool footman cannot list is named and
skipped rather than silently treated as having no history — the same doctrine
`audit` follows.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from footman._drivers import Driver

PYPI = "https://pypi.org/pypi/{package}/json"
TIMEOUT = 30


@dataclass(frozen=True)
class Release:
    """One published release: what to install, and when it was published."""

    version: str
    date: str
    """`YYYY-MM-DD`, from the index — the ordering key, because version
    strings across the curated set cannot order themselves (`0.6.0-wk.5`)."""


def can_list(driver: Driver) -> bool:
    """Whether this tool's past releases can be enumerated yet."""
    return driver.provision.kind == "uv" and driver.source != "manual"


def releases(driver: Driver) -> list[Release]:
    """Every published release, **newest first**.

    A release with no files is skipped: PyPI keeps yanked and file-less
    versions in the index, and neither can be installed to be read.
    """
    package = driver.provision.target(driver.name)
    try:
        with urllib.request.urlopen(
            PYPI.format(package=package), timeout=TIMEOUT
        ) as response:
            index = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    from footman.tools import version_tuple

    found = [
        Release(version=version, date=files[0]["upload_time"][:10])
        for version, files in index.get("releases", {}).items()
        if files
    ]
    # Date orders the set — version strings across the curated tools cannot
    # order themselves — but same-day releases are common (prek shipped 0.4.7
    # and 0.4.8 on one day), and a tie resolved by dict order would let the
    # walk skip one and later append it *below* its own successor. Version
    # breaks the tie, which is the one place it can be trusted: within a tool,
    # on a single day.
    return sorted(found, key=lambda r: (r.date, version_tuple(r.version)), reverse=True)


def install(driver: Driver, version: str, into: Path) -> Path | None:
    """Install one release into its own environment; return its `bin`.

    A venv per release rather than `uv tool install`, because the point is to
    read *this* version's `--help` and then forget it — a tool prefix is
    shared state, and the plugins a driver declares (pytest's `pytest-cov`)
    must ride along or the reading loses flags the stub records.
    """
    package = driver.provision.target(driver.name)
    into.mkdir(parents=True, exist_ok=True)
    python = (
        into
        / ("Scripts" if _windows() else "bin")
        / ("python.exe" if _windows() else "python")
    )
    if not _run(["uv", "venv", "--quiet", str(into)]):
        return None
    wanted = [f"{package}=={version}", *driver.provision.plugins]
    if not _run(["uv", "pip", "install", "--quiet", "--python", str(python), *wanted]):
        return None
    return python.parent


def _run(argv: list[str]) -> bool:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _windows() -> bool:
    import sys

    return sys.platform == "win32"
