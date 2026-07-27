"""Past releases of a curated tool, for priming its option history.

Provisioning fetches the *latest* of everything into one prefix. Priming is
the other direction: one specific release at a time, into a throwaway
environment, read once and thrown away — walking backwards from the newest
because the current version is the one that matters most, and because a
backward walk appends to the history rather than rewriting it.

Five tiers can be listed: PyPI (`uv`), npm (`node`), release assets from
GitHub and GitLab — which covers bun's own releases too — and CPython, whose
index is the provisioned uv's own. What remains unlistable is the `system`
tier (git, docker read from the host, with no fetch source wired yet). A tool
footman cannot list is named and skipped rather than silently treated as
having no history — the same doctrine `audit` follows.
"""

from __future__ import annotations

import json
import os
import re
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
    tag: str = ""
    """What the *forge* calls this release, when that differs from the version
    it reports — bun tags `bun-v1.3.13` for a binary answering `1.3.13`.
    Carried rather than guessed: an install that pattern-matched the tag could
    only ever cover the spellings someone had already met."""
    date: str = ""
    """`YYYY-MM-DD`, from the index. Not the ordering key — the version is —
    but what breaks a tie between two builds of one base (`0.6.0-wk.5`),
    which is the one comparison a version cannot make."""


LISTABLE = ("uv", "node", "github", "gitlab", "bun", "python")
"""The tiers with a release index footman can read. `system` is absent
because git and docker are read from the host and have no fetch source."""


def can_list(driver: Driver) -> bool:
    """Whether this tool's past releases can be enumerated."""
    return driver.provision.kind in LISTABLE and driver.source != "manual"


def releases(driver: Driver) -> list[Release]:
    """Every published release, **newest first**, whatever the tier.

    A tier with no index at all returns nothing: the caller names it as
    skipped, and a tool nobody can list is not a tool with no history.

    An index that *exists* and could not be read raises `Unreachable`
    instead. The two used to share the empty list, which is the wrong shape
    for the question a release job asks — "is there anything new" answered
    "no" by a throttled registry ends the job with "nothing to release".
    """
    if not can_list(driver):
        # Not just the unlistable tiers: a hand-written stub carries the
        # *default* provision kind, so `bash` would otherwise be looked up on
        # PyPI — where a package by that name exists and is a different thing
        # entirely.
        return []
    kind = driver.provision.kind
    if kind == "uv":
        found = _pypi(driver)
    elif kind == "node":
        found = _npm(driver)
    elif kind in ("github", "gitlab", "bun"):
        found = _forge(driver, "gitlab" if kind == "gitlab" else "github")
    elif kind == "python":
        found = _uv_python()
    else:
        return []
    return _stable(found)


_PRERELEASE = re.compile(
    r"(?:a|b|rc|alpha|beta|dev|pre)\.?\d*$|-(?:alpha|beta|rc|dev|pre)", re.I
)


def _stable(found: list[Release]) -> list[Release]:
    """Releases only — an alpha is not something to say a flag arrived in.

    Also what made two tools *look* like they ship series in parallel:
    coverage's 4.5.4 landing after 5.0a6, cspell's 6.31.3 after
    7.0.1-alpha.8. Neither is concurrent maintenance; both are a pre-release
    sorting as though it were the final one.
    """
    return [release for release in found if not _PRERELEASE.search(release.version)]


def _order(found: list[Release]) -> list[Release]:
    """Newest first, **by version** — with the date breaking a tie.

    Not by date, which was the first answer and the wrong one. This history
    answers a version question — does *my* build carry this flag — and three
    tools here keep more than one series alive at once: cmake 3.31.x beside
    4.x, pytest's 4.6 LTS beside 5.x, CPython's five. For those, publication
    order and version order genuinely differ, and a date-ordered walk steps
    from 3.14.6 to 3.13.14 and reads every 3.14 option as dropped and then
    re-added a few entries later.

    Version order was avoided because `version_tuple` could not separate
    `0.6.0-wk.5` from `0.6.0` — a fact about the comparator rather than about
    versions, and fixed there. Measured across all 24 listable tools and
    3,214 stable releases, this ordering is total with no collisions.
    """
    from footman.tools import version_tuple

    return sorted(found, key=lambda r: (version_tuple(r.version), r.date), reverse=True)


class Unreachable(Exception):
    """An index that could not be read at all.

    Raised rather than returned as an empty listing, because the two are
    opposite answers that used to share a value. A release job asks "is
    there anything new" and stops when the answer is no; a throttled
    registry answering `{}` would end that job with "nothing new, nothing to
    release" when the truth is that nobody looked. An exception is the shape
    that cannot be read past by accident.
    """

    def __init__(self, source: str, cause: object) -> None:
        super().__init__(f"cannot read {source}: {cause}")
        self.source = source


def _index(url: str) -> dict:
    """A registry's JSON. Raises `Unreachable` when it cannot be read."""
    from footman._provision import api_headers

    request = urllib.request.Request(url, headers=api_headers(url))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as cause:
        raise Unreachable(url, cause) from cause


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
                tag=str(e.get("tag_name", "")),
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
                tag=str(e.get("tag_name", "")),
                date=str(e.get("released_at"))[:10],
            )
            for e in entries
        ]
    return _order([r for r in found if r.version and r.date[:1].isdigit()])


_PBS_DATE = re.compile(r"/download/(\d{8})/")
_STABLE = re.compile(r"\d+\.\d+\.\d+")


def _uv_python() -> list[Release]:
    """CPython's releases, from uv's own download index.

    uv ships that index *inside the binary*, so the reading is only as
    current as the uv doing it — which is why the prime puts a provisioned
    prefix on `PATH` rather than trusting whatever uv a machine happens to
    have. A stale uv silently reports a stale newest python.

    The date is python-build-standalone's build date, read out of the
    download URL. It is not CPython's own release date, but it is when the
    artifact we install was published, and it is the only date the index
    carries. Several series share one build date, which `_order` breaks on
    the version.
    """
    listing = _capture(
        [
            "uv",
            "python",
            "list",
            "--all-versions",
            # Downloads only, or the index answers differently on every
            # machine: installing a version *replaces* its download entry
            # with the local path and drops the URL, so a prime would erase
            # releases from the very listing it walks.
            "--only-downloads",
            "--output-format",
            "json",
        ]
    )
    try:
        entries = json.loads(listing)
    except ValueError as cause:
        # No uv, or a uv that would not answer. Not "CPython has no
        # releases" — see `Unreachable`.
        raise Unreachable("uv python list", cause) from cause
    found: dict[str, Release] = {}
    for entry in entries if isinstance(entries, list) else ():
        version = str(entry.get("version", ""))
        if entry.get("implementation") != "cpython":
            continue  # pypy and graalpy are not this stub's tool
        if entry.get("variant") != "default":
            continue  # free-threaded is a build of a release, not a release
        if not _STABLE.fullmatch(version):
            continue  # 3.15.0a7 is not something to claim an option arrived in
        stamp = _PBS_DATE.search(str(entry.get("url") or ""))
        if stamp and version not in found:
            day = stamp.group(1)
            found[version] = Release(
                version=version, date=f"{day[:4]}-{day[4:6]}-{day[6:]}"
            )
    return _order(list(found.values()))


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


def install(driver: Driver, release: Release, into: Path) -> Path | None:
    """Install one release into its own directory; return the `bin` to read.

    Per release rather than into a shared prefix, because the point is to read
    *this* version's `--help` and then forget it. `None` means this release
    could not be had — the caller ends that tool's walk rather than leaving a
    hole in the chain.
    """
    kind = driver.provision.kind
    into.mkdir(parents=True, exist_ok=True)
    if kind == "uv":
        return _install_pypi(driver, release.version, into)
    if kind == "node":
        return _install_npm(driver, release.version, into)
    if kind in ("github", "gitlab", "bun"):
        return _install_asset(driver, release, into)
    if kind == "python":
        return _install_python(release.version)
    return None


def _install_python(version: str) -> Path | None:
    """`uv python install` this exact patch, and read where it landed.

    Unlike every other tier this ignores the throwaway directory: uv keeps
    its interpreters in one managed store, so the install is shared and
    already-cached versions cost nothing on a re-run. The store is uv's to
    clean (`uv python uninstall`), not the prime's.

    The directory uv reports already holds a plain `python` alongside the
    versioned name, which is what the extractor invokes.
    """
    if not _run(["uv", "python", "install", version]):
        return None
    found = _capture(["uv", "python", "find", version]).strip()
    if not found or not Path(found).exists():
        return None
    return Path(found).parent


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


def _install_asset(driver: Driver, release: Release, into: Path) -> Path | None:
    """Download this release's asset for this platform and unpack it.

    Addressed by the tag the listing recorded, not by one derived from the
    version. A forge tag is `v2.96.0` on one project, `2.96.0` on the next and
    `bun-v1.3.13` on a third, while the binary answers a bare number in every
    case — so guessing covered exactly the spellings someone had already met,
    and bun's whole history was unreachable without anyone noticing, because
    listing it worked and only installing failed. The version is still tried
    as a fallback, for a listing that recorded no tag.
    """
    from footman import _provision

    host = "gitlab" if driver.provision.kind == "gitlab" else "github"
    bindir = into / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for tag in (release.tag, release.version, f"v{release.version}"):
        if not tag:
            continue
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


def _capture(argv: list[str]) -> str:
    """What a command printed, or empty when it could not be run.

    Empty is not "nothing to report": the callers treat a tool they cannot
    read as one they have not looked at, the same as an unreachable index.
    """
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            # Passed rather than inherited, so footman reads the spawn as
            # deliberate — and so the prefix `prime` puts on `PATH` is what
            # picks the uv that carries the index.
            env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


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
