"""Past releases of a curated tool, for priming its option history.

Provisioning fetches the *latest* of everything into one prefix. Priming is
the other direction: one specific release at a time, into a throwaway
environment, read once and thrown away — walking backwards from the newest
because the current version is the one that matters most, and because a
backward walk appends to the history rather than rewriting it.

Four tiers can be listed: PyPI (`uv`), npm (`node`), and release assets from
GitHub and GitLab — which covers bun's own releases too. What remains
unlistable is the `system` tier (git, docker read from the host, with no
fetch source wired yet) and provisioned interpreters. A tool footman cannot
list is named and skipped rather than silently treated as having no history —
the same doctrine `audit` follows.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
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


LISTABLE = ("uv", "node", "github", "gitlab", "bun")
"""The tiers with a release index footman can read. `system` is absent
because git and docker are read from the host and have no fetch source; a
provisioned interpreter is absent because a python release is not a tool
release."""


def can_list(driver: Driver) -> bool:
    """Whether this tool's past releases can be enumerated."""
    return driver.provision.kind in LISTABLE and driver.source != "manual"


def releases(driver: Driver) -> list[Release]:
    """Every published release, **newest first**, whatever the tier.

    A tier that cannot be listed returns nothing rather than raising: the
    caller names it as skipped, and a tool with no listable index is not a
    tool with no history — it is one nobody can read yet.
    """
    if not can_list(driver):
        # Not just the unlistable tiers: a hand-written stub carries the
        # *default* provision kind, so `bash` would otherwise be looked up on
        # PyPI — where a package by that name exists and is a different thing
        # entirely.
        return []
    kind = driver.provision.kind
    if kind == "uv":
        return _pypi(driver)
    if kind == "node":
        return _npm(driver)
    if kind in ("github", "gitlab", "bun"):
        return _forge(driver, "gitlab" if kind == "gitlab" else "github")
    return []


def _order(found: list[Release]) -> list[Release]:
    """Newest first: by date, with the version breaking a same-day tie."""
    from footman.tools import version_tuple

    return sorted(found, key=lambda r: (r.date, version_tuple(r.version)), reverse=True)


def _index(url: str) -> dict:
    """A registry's JSON, or `{}` when it cannot be read — a prime that
    cannot list a tool skips it rather than failing the run."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}


def _npm(driver: Driver) -> list[Release]:
    """npm's registry keeps a `time` map of version to publication date."""
    package = driver.provision.target(driver.name)
    index = _index(f"https://registry.npmjs.org/{package}")
    times = index.get("time", {})
    return _order(
        [
            Release(version=version, date=str(published)[:10])
            for version, published in times.items()
            if version not in ("created", "modified")
            and version in index.get("versions", {})
        ]
    )


def _forge(driver: Driver, host: str) -> list[Release]:
    """GitHub and GitLab releases, with the tag normalised to a version.

    A tag is `v2.96.0` on one project and `2.96.0` on the next, while the
    binary reports the bare number — and the history keys on what the binary
    says, or a primed release would never match the base it belongs under.
    """
    from footman.tools import read_version

    repo = driver.provision.repo
    if not repo:
        return []
    if host == "github":
        index = _index(f"https://api.github.com/repos/{repo}/releases?per_page=100")
        entries = index if isinstance(index, list) else []
        found = [
            Release(
                version=read_version(e.get("tag_name", "")),
                date=str(e.get("published_at"))[:10],
            )
            for e in entries
            if not e.get("draft") and not e.get("prerelease")
        ]
    else:
        quoted = urllib.parse.quote(repo, safe="")
        index = _index(f"https://gitlab.com/api/v4/projects/{quoted}/releases")
        entries = index if isinstance(index, list) else []
        found = [
            Release(
                version=read_version(e.get("tag_name", "")),
                date=str(e.get("released_at"))[:10],
            )
            for e in entries
        ]
    return _order([r for r in found if r.version and r.date[:1].isdigit()])


def _pypi(driver: Driver) -> list[Release]:
    """PyPI's index, minus the versions with no files.

    It keeps yanked and file-less versions, and neither can be installed to
    be read.
    """
    package = driver.provision.target(driver.name)
    index = _index(PYPI.format(package=package))
    found = [
        Release(version=version, date=files[0]["upload_time"][:10])
        for version, files in index.get("releases", {}).items()
        if files
    ]
    return _order(found)


def install(driver: Driver, version: str, into: Path) -> Path | None:
    """Install one release into its own directory; return the `bin` to read.

    Per release rather than into a shared prefix, because the point is to read
    *this* version's `--help` and then forget it. `None` means this release
    could not be had — the caller ends that tool's walk rather than leaving a
    hole in the chain.
    """
    kind = driver.provision.kind
    into.mkdir(parents=True, exist_ok=True)
    if kind == "uv":
        return _install_pypi(driver, version, into)
    if kind == "node":
        return _install_npm(driver, version, into)
    if kind in ("github", "gitlab", "bun"):
        return _install_asset(driver, version, into)
    return None


def _install_pypi(driver: Driver, version: str, into: Path) -> Path | None:
    """A venv per release. The plugins a driver declares (pytest's
    `pytest-cov`) ride along, or the reading loses flags the stub records."""
    package = driver.provision.target(driver.name)
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


def _install_npm(driver: Driver, version: str, into: Path) -> Path | None:
    """`bun add --global` at a pinned version, with the prefix to itself.

    bun is how the node tier is provisioned, so priming borrows it rather than
    adding a second package manager. Without bun on PATH there is nothing to
    install with, and the walk stops.
    """
    import os
    import shutil

    if shutil.which("bun") is None:
        return None
    package = driver.provision.target(driver.name)
    env = {
        **os.environ,
        "BUN_INSTALL": str(into),
        "PATH": f"{into / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    if not _run(["bun", "add", "--global", f"{package}@{version}"], env=env):
        return None
    return into / "bin"


def _install_asset(driver: Driver, version: str, into: Path) -> Path | None:
    """Download this release's asset for this platform and unpack it.

    The tag is whatever the forge calls it — `v2.96.0` or `2.96.0` — while the
    history keys on what the binary reports, so both spellings are tried.
    """
    from footman import _provision

    host = "gitlab" if driver.provision.kind == "gitlab" else "github"
    bindir = into / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for tag in (version, f"v{version}"):
        try:
            assets = _provision.assets_for(host, driver.provision.repo, tag)
            _name, url = _provision._pick_asset(assets)
            archive = _provision._download(url, into)
            _provision._extract_binary(archive, driver.name, bindir)
        except _provision.ProvisionError:
            continue
        except (OSError, ValueError):
            return None
        return bindir
    return None


def _run(argv: list[str], env: dict[str, str] | None = None) -> bool:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=300, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _windows() -> bool:
    import sys

    return sys.platform == "win32"
