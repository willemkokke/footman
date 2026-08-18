"""Download files into footman's cache — `fetch()`.

Build tasks fetch things: a toolchain tarball, a schema, a fixture. Doing
it well means caching by URL, revalidating instead of re-downloading,
verifying what arrived, and reporting progress — and doing it *here*
means it composes with everything already built:

- the cached copy lives under `footman_cache_dir()`, so
  `FOOTMAN_CACHE_DIR` relocates it and the cache collector tends it;
- every fetch records a `Result`, so `--dry-run` prints without
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

Which one you name changes the socket, and nothing else. All four
revalidate with the same headers, refuse a body that arrived short, and
land the finished file with the same rename — a backend that behaved its
own way would make the choice a behaviour change rather than a plumbing
one, and the conformance table in `tests/test_fetch.py` runs one set of
scenarios against every backend installed to keep it that way.

Deliberately *not* automatic: a fetch that silently picks a different
engine depending on what happens to be importable would change its TLS
trust store and proxy semantics when an unrelated dependency appears.
`backend = "auto"` exists for people who want that, spelled out as a
choice rather than a surprise.

This is for build artifacts, not a general HTTP client. Anything exotic
belongs in a real curl call — `run(["curl", ...])`, or toolroom's typed
`curl(...)` handle.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import shutil
import tempfile
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


def _scratch(near: Path, suffix: str = ".part") -> Path:
    """A unique scratch file beside *near*, for a download in flight.

    Beside, so the finished body lands on its cache path with a rename on
    the same filesystem. Named `.part`, so the collector can sweep one
    whose process died before it got that far.
    """
    fd, name = tempfile.mkstemp(dir=near.parent, prefix=f"{near.stem}.", suffix=suffix)
    os.close(fd)
    return Path(name)


def _download(
    backend: str, url: str, dest: Path, meta: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Fetch *url* into *dest*; returns `(downloaded, validators)`.

    Two separate facts that used to share one value: whether bytes moved
    (what the receipt reports), and the validators to persist for the next
    revalidation (what the sidecar stores). A revalidation that comes back
    `304` moves no bytes and keeps the validators it already had, which
    under the shared value read as a fresh download of nothing.

    *dest* is a scratch path, never the cache path — `fetch()` renames it
    into place, so every backend inherits the same atomic landing."""
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


def _download_urllib(
    url: str, dest: Path, meta: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
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
            if total and received != total:
                # A short body reads as a small one to everyone upstream, and
                # cached it is forever: the ETag off this same response goes
                # in the sidecar, the healthy origin answers 304 from then
                # on, and the half file is served as a hit. CPython won't
                # raise here itself — HTTPResponse.read's own comment says it
                # "might break compatibility" — so the check is ours to make.
                raise FetchError(
                    f"fetch: {url} — the body ended early: {received} of {total} bytes"
                )
            return True, {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # not modified: the cached copy stands
            return False, {}
        raise FetchError(f"fetch: {url} — HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(
            f"fetch: {url} — {exc.reason}. If this machine needs the system "
            f'curl (a corporate proxy or TLS store), set `backend = "curl"` '
            f"under [fetch] — in this project, or once for every project in "
            f"{_paths.footman_config_file()}"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        # A connection that dies mid-body, after the headers arrived. It has
        # to read as a FetchError or the cached copy never gets its say.
        raise FetchError(f"fetch: {url} — {exc}") from exc


def _download_curl(
    url: str, dest: Path, meta: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    import subprocess

    from footman import _globals

    dump = _scratch(dest, suffix=".headers.part")
    argv = ["curl", "-fsSL", "--retry", "2", "-D", str(dump), "-o", str(dest), url]
    for header, value in _conditional_headers(meta).items():
        argv += ["-H", f"{header}: {value}"]
    # This spawn is footman working on the body's behalf, so the Popen
    # injector must not attribute it to the task — the "prefer run()" note is
    # teach-once, and spending it here swallows the note a real raw spawn in
    # the same task would have earned. `internal()` stands the injector down;
    # cwd and env are passed explicitly so nothing changes with it gone:
    # every path on the command line is absolute (cwd is unobservable, pinned
    # to the cache directory anyway), and the environment is snapshotted
    # through the router *before* entering `internal()`, so a managed task's
    # curl sees the same proxy variables the urllib backend reads in-process.
    env = dict(os.environ)
    try:
        with _globals.internal():
            done = subprocess.run(
                argv, capture_output=True, text=True, cwd=dest.parent, env=env
            )
        if done.returncode != 0:
            # Exit 18 lives here too: curl counts the body against
            # Content-Length itself and calls a short one a failure.
            raise FetchError(f"fetch: {url} — curl: {done.stderr.strip()}")
        code, headers = _last_response(dump.read_text("utf-8", errors="replace"))
    finally:
        with contextlib.suppress(OSError):
            dump.unlink()
    if code == 304:  # not modified: curl wrote nothing, the cached copy stands
        return False, {}
    return True, {
        "etag": headers.get("etag"),
        "last_modified": headers.get("last-modified"),
    }


def _last_response(dump: str) -> tuple[int, dict[str, str]]:
    """The status and headers of the final response in a curl `-D` dump.

    Redirects and retries each append their own block; only the last one
    describes the bytes that landed in the file.
    """
    code = 0
    headers: dict[str, str] = {}
    for line in dump.splitlines():
        if line.upper().startswith("HTTP/"):
            parts = line.split()
            code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            headers = {}
        elif ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    return code, headers


def _download_lib(
    name: str, url: str, dest: Path, meta: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    import importlib

    client = importlib.import_module(name)
    # Each library's own error base, reached through the module object that is
    # already in hand. Naming them costs no import up here — which is the one
    # thing this file may not do — and `except Exception` was too much net: it
    # dressed footman's own bugs as network failures and then served a stale
    # cached copy on the strength of it. Both bases cover the transport
    # failures that matter (httpx's RemoteProtocolError, requests'
    # ChunkedEncodingError) and nothing else.
    transport_error: type[BaseException] = (
        client.HTTPError if name == "httpx" else client.exceptions.RequestException
    )
    try:
        response = (
            client.get(url, headers=_conditional_headers(meta), follow_redirects=True)
            if name == "httpx"
            else client.get(
                url, headers=_conditional_headers(meta), allow_redirects=True
            )
        )
        if response.status_code == 304:
            return False, {}
        if response.status_code >= 400:
            raise FetchError(f"fetch: {url} — HTTP {response.status_code}")
        payload = response.content
    except FetchError:
        raise
    except transport_error as exc:
        # A dropped connection has to arrive as a FetchError, or the cached
        # copy never gets its say.
        raise FetchError(f"fetch: {url} — {type(exc).__name__}: {exc}") from exc
    dest.write_bytes(payload)
    return True, {
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
        run(["tar", "-xzf", archive, "-C", "vendor"])
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
        ctx.steps.append(context.Result(0, command=label, raw=label))
        if not ctx.quiet:
            print(f"$ {label}")
        return destination

    chosen = _resolve_backend(backend or _configured_backend(ctx))
    started = time.perf_counter()
    cache_dir().mkdir(parents=True, exist_ok=True)
    meta = {} if refresh else _load_meta(sidecar)
    incoming = _scratch(body)
    try:
        downloaded, fresh = _download(
            chosen, url, incoming, meta if body.exists() else {}
        )
        if downloaded:
            # The cache path is only ever replaced whole. Downloading onto it
            # meant two tasks fetching the same cold URL truncated each other
            # and a transfer that died halfway left its stump behind — and
            # `sha256=` then blamed the server for bytes that arrived intact.
            os.replace(incoming, body)
    except FetchError:
        if body.exists():  # a cached copy beats a failed refresh
            _touch(body, sidecar)
            _record(ctx, label, started, cached=True)
            return _deliver(body, destination, sha256, url)
        raise
    finally:
        with contextlib.suppress(OSError):
            incoming.unlink(missing_ok=True)
    if fresh:
        sidecar.write_text(json.dumps(fresh), encoding="utf-8")
    if not downloaded:
        # A serve is a use: the collector's idle rule reads mtimes, and a
        # 304 writes nothing of its own — untouched, a daily-fetched file
        # would age out and force a pointless re-download.
        _touch(body, sidecar)
    _record(ctx, label, started, cached=not downloaded)
    return _deliver(body, destination, sha256, url)


def _touch(*paths: Path) -> None:
    """Mark a cache serve as use, for the collector's idle rule."""
    for path in paths:
        with contextlib.suppress(OSError):
            os.utime(path)


def _configured_backend(ctx: context.Context) -> str:
    """`[fetch] backend` from the config ladder, defaulting to urllib."""
    return ctx.fetch_backend or "urllib"


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
        context.Result(
            0,
            command=label,
            stdout=note,
            duration=time.perf_counter() - started,
            raw=label,
        )
    )
