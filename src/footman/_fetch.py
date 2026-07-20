"""Download files into footman's cache — `fetch()`.

Build tasks fetch things: a toolchain tarball, a schema, a fixture. Doing
it well means caching by URL, revalidating instead of re-downloading,
verifying what arrived, and reporting progress — and doing it *here*
means it composes with everything already built:

- the cached copy lives under `footman_cache_dir()`, so
  `FOOTMAN_CACHE_DIR` relocates it and the cache collector tends it;
- every fetch records a `StepResult`, so `--dry-run` prints without
  downloading, `recording()` asserts on it in tests, `--json` carries
  it, and the step lines show it in the same aligned grid as `run()`;
- byte counts feed `progress()`, so a download drives the live bar.

**Backends.** The default is stdlib `urllib` — always present, zero
dependencies, deterministic, and the only backend that can report bytes
as they arrive. `curl` (shipped in Windows' System32 since build 17063,
and on every POSIX box) is the escape hatch for corporate proxies and
TLS stores that Python's defaults can't see; `httpx` and `requests` are
used only when explicitly named. Choose per call, or set
`[fetch] backend` in any config file — a machine behind a proxy sets it
once in `~/.config/footman/config.toml` and every project follows.

Deliberately *not* automatic: a fetch that silently picks a different
engine depending on what happens to be importable would change its TLS
trust store and proxy semantics when an unrelated dependency appears.
`backend = "auto"` exists for people who want that, spelled out as a
choice rather than a surprise.

This is for build artifacts, not a general HTTP client. Anything exotic
belongs in `tools.curl(...)`, which is right there.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from footman import _paths, context

BACKENDS = ("urllib", "curl", "httpx", "requests", "auto")
_AUTO_ORDER = ("httpx", "requests", "urllib", "curl")
CHUNK = 64 * 1024


class FetchError(Exception):
    """A download failed, or arrived wrong (checksum, missing backend)."""


def cache_dir() -> Path:
    """Where fetched files live: a `fetch/` room in footman's own cache."""
    return _paths.footman_cache_dir() / "fetch"


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _paths_for(url: str) -> tuple[Path, Path]:
    """The cached body and its metadata sidecar (ETag, Last-Modified)."""
    stem = cache_dir() / _key(url)
    return stem.with_suffix(".bin"), stem.with_suffix(".meta.json")


def _load_meta(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest()


def _resolve_backend(name: str) -> str:
    """The backend to use, refusing a named-but-missing one out loud."""
    if name == "auto":
        for candidate in _AUTO_ORDER:
            if _available(candidate):
                return candidate
        raise FetchError("fetch: no usable backend (not even urllib?)")
    if name not in BACKENDS:
        options = ", ".join(BACKENDS)
        raise FetchError(f"fetch: unknown backend {name!r} — choose one of {options}")
    if not _available(name):
        if name == "curl":
            raise FetchError("fetch: backend 'curl' is not on PATH")
        raise FetchError(
            f"fetch: backend {name!r} is not installed — `pip install {name}`, "
            f"or leave [fetch] backend unset to use the stdlib"
        )
    return name


def _available(name: str) -> bool:
    if name == "urllib":
        return True
    if name == "curl":
        return shutil.which("curl") is not None
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _download(backend: str, url: str, dest: Path, meta: dict[str, Any]) -> dict:
    """Fetch *url* into *dest*; return the new metadata (empty = unchanged)."""
    if backend == "curl":
        return _download_curl(url, dest, meta)
    if backend in ("httpx", "requests"):
        return _download_lib(backend, url, dest, meta)
    return _download_urllib(url, dest, meta)


def _conditional_headers(meta: dict[str, Any]) -> dict[str, str]:
    headers = {}
    if etag := meta.get("etag"):
        headers["If-None-Match"] = str(etag)
    if modified := meta.get("last_modified"):
        headers["If-Modified-Since"] = str(modified)
    return headers


def _download_urllib(url: str, dest: Path, meta: dict[str, Any]) -> dict:
    request = urllib.request.Request(url, headers=_conditional_headers(meta))
    try:
        with urllib.request.urlopen(request) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with open(dest, "wb") as fh:
                while chunk := response.read(CHUNK):
                    fh.write(chunk)
                    received += len(chunk)
                    if total:
                        context.progress(received, total)
            if total:
                context.progress(0, 0)  # done reporting: back to the estimate
            return {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # not modified: the cached copy stands
            return {}
        raise FetchError(f"fetch: {url} — HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(
            f"fetch: {url} — {exc.reason}. If this machine needs the system "
            f'curl (a corporate proxy or TLS store), set `backend = "curl"` '
            f"under [fetch] — in this project, or once for every project in "
            f"{_paths.footman_config_file()}"
        ) from exc


def _download_curl(url: str, dest: Path, meta: dict[str, Any]) -> dict:
    import subprocess

    argv = ["curl", "-fsSL", "--retry", "2", "-o", str(dest), url]
    for header, value in _conditional_headers(meta).items():
        argv += ["-H", f"{header}: {value}"]
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise FetchError(f"fetch: {url} — curl: {done.stderr.strip()}")
    return {}  # curl's revalidation story is its own; re-fetch is honest


def _download_lib(name: str, url: str, dest: Path, meta: dict[str, Any]) -> dict:
    import importlib

    client = importlib.import_module(name)
    response = (
        client.get(  # type: ignore[attr-defined]
            url, headers=_conditional_headers(meta), follow_redirects=True
        )
        if name == "httpx"
        else client.get(  # type: ignore[attr-defined]
            url, headers=_conditional_headers(meta), allow_redirects=True
        )
    )
    if response.status_code == 304:
        return {}
    if response.status_code >= 400:
        raise FetchError(f"fetch: {url} — HTTP {response.status_code}")
    dest.write_bytes(response.content)
    return {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
    }


def fetch(
    url: str,
    *,
    into: Path | str | None = None,
    sha256: str = "",
    backend: str = "",
    refresh: bool = False,
) -> Path:
    """Download *url* (cached), returning the path to the local file.

    A second call for the same URL revalidates with the server (ETag /
    Last-Modified) rather than re-downloading; a `304 Not Modified`
    costs one round trip and keeps "cached" honest. Pass *refresh* to
    skip revalidation and fetch unconditionally.

    ```python
    @task
    def deps():
        "Fetch the toolchain."
        archive = fetch(TOOLCHAIN_URL, sha256="9f86d0…")
        tools.tar("-xzf", archive, "-C", "vendor")
    ```

    *into* copies the cached file to a path of your choosing (and
    returns that path). *sha256* verifies what arrived and refuses a
    mismatch — the way to make a build reproducible. *backend* overrides
    the configured one for this call.

    Under `--dry-run` nothing is downloaded: the step is recorded and
    the would-be cache path returned, so a plan can be inspected safely.
    """
    ctx = context.current()
    label = f"fetch {url}"
    body, sidecar = _paths_for(url)
    destination = Path(into) if into is not None else body

    if ctx.dry_run:
        ctx.steps.append(context.StepResult(label, 0, "", 0.0, raw=label))
        if not ctx.quiet:
            print(f"$ {label}")
        return destination

    chosen = _resolve_backend(backend or _configured_backend(ctx))
    started = time.perf_counter()
    cache_dir().mkdir(parents=True, exist_ok=True)
    meta = {} if refresh else _load_meta(sidecar)
    try:
        fresh = _download(chosen, url, body, meta if body.exists() else {})
    except FetchError:
        if body.exists():  # a cached copy beats a failed refresh
            _record(ctx, label, started, cached=True)
            return _deliver(body, destination, sha256, url)
        raise
    if fresh:
        sidecar.write_text(json.dumps(fresh), encoding="utf-8")
    _record(ctx, label, started, cached=not fresh)
    return _deliver(body, destination, sha256, url)


def _configured_backend(ctx: context.Context) -> str:
    """`[fetch] backend` from the config ladder, defaulting to urllib."""
    configured = getattr(ctx, "fetch_backend", "") or ""
    return str(configured) or "urllib"


def _deliver(body: Path, destination: Path, sha256: str, url: str) -> Path:
    if sha256:
        actual = _digest(body)
        if actual != sha256.lower():
            raise FetchError(
                f"fetch: {url} — sha256 mismatch\n  expected {sha256.lower()}\n"
                f"  received {actual}"
            )
    if destination != body:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(body, destination)
    return destination


def _record(ctx: context.Context, label: str, started: float, *, cached: bool) -> None:
    """A fetch is a step: same grid, same --json entry, same recording()."""
    note = "cached" if cached else ""
    ctx.steps.append(
        context.StepResult(label, 0, note, time.perf_counter() - started, raw=label)
    )
