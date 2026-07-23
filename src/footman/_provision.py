"""Fetch the latest curated tools into a throwaway prefix — the engine behind
`fm footman tools provision`.

The stubs are read from the *installed* binaries (`fm footman tools sync`), so
telling an editor what the newest release accepts means having the newest
release on `PATH` — across five ecosystems (PyPI, npm, bun, Go, C++), none of
which should be allowed to touch the machine's own environment.

One isolated prefix answers all of it. Almost every curated tool ships an
installable PyPI wheel — including the Rust ones (ruff, uv, prek, git-cliff)
and the C++ ones (cmake, ninja) — so `uv tool install --upgrade` into a
private `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` covers the majority and cleans up with
one `rm -rf`. What's left is bun (its own release), the node CLIs it installs,
and the Go CLIs (a prebuilt release asset):

* **uv** — `uv tool install --upgrade <pkg>`, tools and launchers under the
  prefix; nothing lands in `~/.local` or the system site-packages.
* **bun** — bun's GitHub release, unpacked into the prefix. Provisioned
  *first*, because the node tier runs through it.
* **node** — `bun add --global` with `BUN_INSTALL` pointed at the prefix.
* **github / gitlab** — the latest release asset for this platform, matched
  from the release's own asset list (so `Darwin`/`x86_64` vs `darwin`/`x64`
  naming needn't be transcribed), unpacked, binary placed in the prefix.
* **system** — git, docker, the uv running this: already on `PATH`, left be.
* **deferred** — parked, with a reason (tea, until it stops hanging on
  `--help`).

Everything writes under one prefix and `PATH="<prefix>/bin:$PATH"` is all a
`sync` needs to read the newest binaries; deleting the prefix undoes it. This
is a maintainer tool: it shells out and downloads, and it is never on the
completion hot path.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from footman._drivers import Driver


class ProvisionError(Exception):
    """A tool could not be fetched — reported per tool, never fatal."""


@dataclass(frozen=True)
class Outcome:
    """What became of one tool: the line `provision` prints."""

    key: str
    kind: str
    status: str  # "ok" | "fail" | "skip" | "deferred"
    detail: str = ""


def bin_dir(prefix: Path) -> Path:
    """The one directory to put on `PATH`; every tier lands its launchers here."""
    return prefix / "bin"


def provision(
    drivers: tuple[Driver, ...], prefix: Path, *, only: str = ""
) -> list[Outcome]:
    """Materialise the latest of each curated tool under *prefix*.

    Tiers run in the one order that matters: bun before the node CLIs that
    need it. Each tool's failure is its own line, never the run's — a missing
    binary should read as one skipped hint, not a broken provision.
    """
    prefix = Path(prefix)
    bin_dir(prefix).mkdir(parents=True, exist_ok=True)
    chosen = [d for d in drivers if not only or d.key == only]
    by_kind: dict[str, list[Driver]] = {}
    for driver in chosen:
        by_kind.setdefault(driver.provision.kind, []).append(driver)

    outcomes: list[Outcome] = []
    for driver in by_kind.get("system", []):
        outcomes.append(
            Outcome(driver.key, "system", "skip", f"uses the system {driver.name}")
        )
    for driver in by_kind.get("deferred", []):
        outcomes.append(
            Outcome(driver.key, "deferred", "deferred", driver.provision.note)
        )
    outcomes += _uv_tier(prefix, by_kind.get("uv", []))
    outcomes += _python_tier(prefix, by_kind.get("python", []))
    for driver in by_kind.get("bun", []):  # before node: node runs through bun
        outcomes.append(_release(prefix, driver, host="github"))
    outcomes += _node_tier(prefix, by_kind.get("node", []))
    for driver in by_kind.get("github", []) + by_kind.get("gitlab", []):
        outcomes.append(_release(prefix, driver, host=driver.provision.kind))
    return outcomes


# --- uv tier -----------------------------------------------------------------


def _uv_env(prefix: Path) -> dict[str, str]:
    """uv's install targets, redirected so nothing escapes the prefix."""
    return {
        **os.environ,
        "UV_TOOL_DIR": str(prefix / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(bin_dir(prefix)),
    }


def _uv_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`uv tool install --upgrade` each distinct package into the prefix.

    A driver's `provision.plugins` ride along as `--with` packages in the tool's
    own isolated environment, so a plugin-extended CLI (pytest + pytest-cov) is
    installed whole and its plugin flags are there to read.
    """
    env = _uv_env(prefix)
    installed: dict[tuple[str, tuple[str, ...]], bool] = {}
    outcomes: list[Outcome] = []
    for driver in drivers:
        package = driver.provision.target(driver.name)
        plugins = driver.provision.plugins
        key = (package, plugins)
        if key not in installed:
            withs = [f"--with={p}" for p in plugins]
            installed[key] = _run(
                ["uv", "tool", "install", "--upgrade", package, *withs], env=env
            )
        ok = installed[key]
        detail = package if not plugins else f"{package} (+{', '.join(plugins)})"
        outcomes.append(Outcome(driver.key, "uv", "ok" if ok else "fail", detail))
    return outcomes


# --- python tier (an interpreter to read `--help` from) ----------------------


def _python_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`uv python install` each requested interpreter, linked into the prefix.

    python is provisioned like any other tool — an interpreter whose `--help`
    is read for the stub. The *runtime* `tools.python` always targets
    `sys.executable`; provisioning only supplies versions to extract from, so
    the stub reflects real pythons rather than whatever `python`/`python3` a
    machine happens to have on PATH.
    """
    outcomes: list[Outcome] = []
    for driver in drivers:
        version = driver.provision.package or "3"
        if not _run(["uv", "python", "install", version], env=dict(os.environ)):
            outcomes.append(
                Outcome(driver.key, "python", "fail", f"uv python install {version}")
            )
            continue
        try:
            found = subprocess.run(
                ["uv", "python", "find", version],
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(os.environ),
            )
            path = Path(found.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            path = Path()
        if not path.name or not path.exists():
            outcomes.append(
                Outcome(driver.key, "python", "fail", f"no python {version} found")
            )
            continue
        link = bin_dir(prefix) / "python"
        link.unlink(missing_ok=True)
        link.symlink_to(path)
        outcomes.append(Outcome(driver.key, "python", "ok", f"{version} ({path})"))
    return outcomes


# --- node tier (through the provisioned bun) ---------------------------------


def _node_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`bun add --global` each package, with bun's install dir the prefix."""
    if not drivers:
        return []
    bun = bin_dir(prefix) / "bun"
    if not bun.exists():
        return [
            Outcome(d.key, "node", "fail", "bun was not provisioned first")
            for d in drivers
        ]
    env = {
        **os.environ,
        "BUN_INSTALL": str(prefix),  # global bin lands in <prefix>/bin
        "PATH": f"{bin_dir(prefix)}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    packages = sorted({d.provision.target(d.name) for d in drivers})
    ok = _run([str(bun), "add", "--global", *packages], env=env)
    return [
        Outcome(d.key, "node", "ok" if ok else "fail", d.provision.target(d.name))
        for d in drivers
    ]


# --- release tier (github / gitlab, and bun) ---------------------------------


def _release(prefix: Path, driver: Driver, *, host: str) -> Outcome:
    """Download the latest release asset for this platform and unpack it."""
    kind = driver.provision.kind
    try:
        assets = _latest_assets(host, driver.provision.repo)
        name, url = _pick_asset(assets)
        archive = _download(url, prefix)
        placed = _extract_binary(archive, driver.name, bin_dir(prefix))
    except ProvisionError as exc:
        return Outcome(driver.key, kind, "fail", str(exc))
    return Outcome(driver.key, kind, "ok", f"{placed.name} ({name})")


def _latest_assets(host: str, repo: str) -> list[tuple[str, str]]:
    """`[(asset name, download url)]` for *repo*'s latest release."""
    if not repo:
        raise ProvisionError("no repo to fetch from")
    if host == "github":
        data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
        assets = data.get("assets", [])
        return [(a["name"], a["browser_download_url"]) for a in assets]
    if host == "gitlab":
        quoted = urllib.parse.quote(repo, safe="")
        data = _get_json(
            f"https://gitlab.com/api/v4/projects/{quoted}/releases/permalink/latest"
        )
        links = data.get("assets", {}).get("links", [])
        return [(a["name"], a.get("direct_asset_url") or a["url"]) for a in links]
    raise ProvisionError(f"unknown release host {host!r}")


# The alias sets that fold one platform's many spellings into a match: bun
# says `darwin`/`aarch64`, goreleaser `Darwin`/`x86_64`, gh `macOS`/`amd64`.
_OS_ALIASES = {
    "darwin": ("darwin", "macos", "apple", "osx"),
    "linux": ("linux",),
    "windows": ("windows", "win"),
}
_ARCH_ALIASES = {
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
    "x86_64": ("x86_64", "amd64", "x64", "x86-64"),
    "amd64": ("x86_64", "amd64", "x64", "x86-64"),
}
_ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".zip")
# Sidecar files that ride alongside a real asset — never the binary.
_SIDECARS = (".sha256", ".sha256sum", ".sig", ".asc", ".txt", ".pem", ".sbom")
# Build variants that sit beside the canonical asset for the same platform:
# bun's `-profile`/`-baseline`, a `-debug` build, a `musl` libc. Preferred
# against, never excluded — the canonical build is what a task wants.
_VARIANTS = ("profile", "baseline", "debug", "musl", "-static")


def _platform_tokens() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """This machine's OS and CPU aliases, for matching an asset name."""
    os_aliases = _OS_ALIASES.get(
        platform.system().lower(), (platform.system().lower(),)
    )
    machine = platform.machine().lower()
    arch_aliases = _ARCH_ALIASES.get(machine, (machine,))
    return os_aliases, arch_aliases


def _pick_asset(assets: list[tuple[str, str]]) -> tuple[str, str]:
    """The one asset for this OS and CPU, archives before bare binaries."""
    os_aliases, arch_aliases = _platform_tokens()

    def matches(name: str) -> bool:
        low = name.lower()
        if low.endswith(_SIDECARS):
            return False
        return any(o in low for o in os_aliases) and any(a in low for a in arch_aliases)

    candidates = [(name, url) for name, url in assets if matches(name)]
    if not candidates:
        raise ProvisionError("no release asset for this platform")

    def rank(asset: tuple[str, str]) -> tuple[bool, bool, int, str]:
        # Prefer an archive over a bare binary, the canonical build over a
        # variant (bun ships `-profile`/`-baseline` beside the plain one), and
        # then the shortest name — a qualifier only ever lengthens it.
        low = asset[0].lower()
        variant = any(marker in low for marker in _VARIANTS)
        return (not low.endswith(_ARCHIVES), variant, len(asset[0]), asset[0])

    candidates.sort(key=rank)
    return candidates[0]


# --- download + unpack -------------------------------------------------------


def _get_json(url: str) -> dict:
    """A JSON API response — GitHub/GitLab both want a User-Agent."""
    request = urllib.request.Request(url, headers={"User-Agent": "footman-provision"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ProvisionError(f"{url}: {exc}") from exc


def _download(url: str, prefix: Path) -> Path:
    """Fetch *url* into the prefix's cache, reusing a prior download by name."""
    cache = prefix / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / url.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size:
        return dest
    request = urllib.request.Request(url, headers={"User-Agent": "footman-provision"})
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            open(dest, "wb") as out,
        ):
            shutil.copyfileobj(response, out)
    except (urllib.error.URLError, OSError) as exc:
        raise ProvisionError(f"{url}: {exc}") from exc
    return dest


def _extract_binary(archive: Path, tool: str, into: Path) -> Path:
    """Unpack *archive* and place its `tool` binary in *into*, executable.

    Release archives nest the binary under a versioned directory, so the
    whole tree is searched for a file named `tool` (or `tool.exe`); a bare
    downloaded binary is taken as-is.
    """
    wanted = {tool, f"{tool}.exe"}
    into.mkdir(parents=True, exist_ok=True)
    dest = into / tool
    if archive.name.lower().endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")):
        with tarfile.open(archive) as tar:
            member = next(
                (m for m in tar.getmembers() if Path(m.name).name in wanted), None
            )
            if member is None:
                raise ProvisionError(f"{tool} not found inside {archive.name}")
            source = tar.extractfile(member)
            if source is None:
                raise ProvisionError(f"{tool} is not a file inside {archive.name}")
            dest.write_bytes(source.read())
    elif archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            name = next((n for n in zf.namelist() if Path(n).name in wanted), None)
            if name is None:
                raise ProvisionError(f"{tool} not found inside {archive.name}")
            dest.write_bytes(zf.read(name))
    else:  # a bare binary, downloaded directly
        dest.write_bytes(archive.read_bytes())
    dest.chmod(0o755)
    return dest


# --- subprocess --------------------------------------------------------------


def _run(argv: list[str], *, env: dict[str, str]) -> bool:
    """Run an install command, quietly; its success is all the caller needs."""
    try:
        done = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=600
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0
