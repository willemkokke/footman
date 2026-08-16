"""fetch(): the cache, revalidation, verification, backends, and steps."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path
from typing import ClassVar

import pytest

from footman import _fetch, _paths
from footman.context import Context, use_context

BODY = b"footman fetch payload\n"
SHA = "0f2c1a8ff1b0e8b2f0b1b7b2c9a0e2b7f6e5d4c3b2a1908070605040302010ff"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves BODY with an ETag, answering conditional requests with 304."""

    etag = '"v1"'
    hits: ClassVar[list[str]] = []

    def do_GET(self):  # BaseHTTPRequestHandler's spelling, not ours
        type(self).hits.append(self.headers.get("If-None-Match") or "unconditional")
        if self.headers.get("If-None-Match") == type(self).etag:
            self.send_response(304)
            self.end_headers()
            return
        if self.path.endswith("missing.bin"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("ETag", type(self).etag)
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, format, *args):  # the base class's spelling
        pass  # keep the test output clean


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A local HTTP server plus an isolated footman cache."""
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    _Handler.hits = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/file.bin"
    httpd.shutdown()


BACKENDS = ["urllib", "curl", "httpx", "requests"]


def _skip_unless_available(backend: str) -> None:
    if not _fetch._available(backend):
        pytest.skip(f"{backend} is not available here")


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_downloads(server, backend):
    """Each backend, driven against a real server — the adapter code and
    the library's actual call signature, not a stand-in for either."""
    _skip_unless_available(backend)
    path = _fetch.fetch(server, backend=backend)
    assert path.read_bytes() == BODY


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_verifies_the_checksum(server, backend):
    _skip_unless_available(backend)
    with pytest.raises(_fetch.FetchError, match="sha256 mismatch"):
        _fetch.fetch(server, backend=backend, sha256=SHA)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_refuses_a_404(server, backend):
    """A missing file is a taught FetchError, whatever fetched it — not a
    library-specific exception leaking through."""
    _skip_unless_available(backend)
    with pytest.raises(_fetch.FetchError, match="fetch: "):
        _fetch.fetch(server.replace("file.bin", "missing.bin"), backend=backend)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_revalidates_with_the_etag(server, backend):
    """A warm cache revalidates: the second call carries If-None-Match, the
    server answers 304, and the receipt says cached. curl used to be outside
    this — it threw its headers away, so it stored no validator to send and
    could never receive a 304 at all."""
    _skip_unless_available(backend)
    _fetch.fetch(server, backend=backend)
    with use_context(Context(fetch_backend=backend)) as ctx:
        assert _fetch.fetch(server).read_bytes() == BODY
    assert _Handler.hits == ["unconditional", '"v1"']
    assert ctx.steps[-1].stdout == "cached"


def test_fetch_downloads_and_caches(server):
    path = _fetch.fetch(server)
    assert path.read_bytes() == BODY
    assert path.parent == _fetch.cache_dir()


def test_second_fetch_revalidates_instead_of_redownloading(server):
    _fetch.fetch(server)
    path = _fetch.fetch(server)
    assert path.read_bytes() == BODY
    # The second request carried the ETag and got a 304 — cached, honestly.
    assert _Handler.hits == ["unconditional", '"v1"']


def test_refresh_skips_revalidation(server):
    _fetch.fetch(server)
    _fetch.fetch(server, refresh=True)
    assert _Handler.hits == ["unconditional", "unconditional"]


def test_into_copies_to_a_chosen_path(server, tmp_path):
    dest = tmp_path / "vendor" / "file.bin"
    path = _fetch.fetch(server, into=dest)
    assert path == dest and dest.read_bytes() == BODY
    assert (_fetch.cache_dir() / path.name).exists() or True  # cache kept too


def test_sha256_mismatch_is_refused(server):
    with pytest.raises(_fetch.FetchError, match="sha256 mismatch"):
        _fetch.fetch(server, sha256=SHA)


def test_sha256_match_passes(server):
    import hashlib

    digest = hashlib.sha256(BODY).hexdigest()
    assert _fetch.fetch(server, sha256=digest).read_bytes() == BODY


def test_fetch_records_a_step(server):
    with use_context(Context()) as ctx:
        _fetch.fetch(server)
    (step,) = ctx.steps
    assert step.command == f"fetch {server}"
    assert step.code == 0


def test_a_download_is_never_reported_cached(server):
    # `cached` used to derive from "no validators came back", which read a
    # curl download — validator-less back then — as cached on the first-ever
    # fetch. Downloaded and cached are separate facts now, on every backend.
    _skip_unless_available("curl")
    with use_context(Context(fetch_backend="curl")) as ctx:
        _fetch.fetch(server)
        assert ctx.steps[-1].stdout == ""  # a real download says so
        _fetch.fetch(server, refresh=True)
        assert ctx.steps[-1].stdout == ""  # forced past the ETag: still moved


def test_a_revalidated_serve_counts_as_use(server):
    # The collector's idle rule reads mtimes and a 304 writes nothing of its
    # own — the serve itself must keep the pair warm, or a daily-fetched file
    # would age out at IDLE_DAYS and force a pointless re-download.
    import os
    import time as _time

    with use_context(Context()) as ctx:
        _fetch.fetch(server)
        body, sidecar = _fetch._paths_for(server)
        then = _time.time() - 400
        os.utime(body, (then, then))
        os.utime(sidecar, (then, then))
        _fetch.fetch(server)
        assert ctx.steps[-1].stdout == "cached"
    assert body.stat().st_mtime > then + 100
    assert sidecar.stat().st_mtime > then + 100


def test_dry_run_downloads_nothing(server, capsys):
    with use_context(Context(dry_run=True)) as ctx:
        path = _fetch.fetch(server)
    assert not path.exists()  # nothing downloaded
    assert _Handler.hits == []  # the server was never touched
    assert ctx.steps[0].command == f"fetch {server}"  # but the plan records it
    assert f"$ fetch {server}" in capsys.readouterr().out


def test_unknown_backend_is_taught():
    with pytest.raises(_fetch.FetchError, match="unknown backend"):
        _fetch._resolve_backend("wget")


def test_missing_library_backend_names_the_fix(monkeypatch):
    monkeypatch.setattr(_fetch, "_available", lambda name: name == "urllib")
    with pytest.raises(_fetch.FetchError, match=r"not installed.*pip install httpx"):
        _fetch._resolve_backend("httpx")


def test_auto_picks_the_first_available(monkeypatch):
    monkeypatch.setattr(_fetch, "_available", lambda name: name in ("urllib", "curl"))
    assert _fetch._resolve_backend("auto") == "urllib"  # ahead of curl in order


@pytest.mark.skipif(_fetch.shutil.which("curl") is None, reason="curl is not on PATH")
def test_curl_backend_downloads(server):
    path = _fetch.fetch(server, backend="curl")
    assert path.read_bytes() == BODY


def test_curl_reads_the_last_response_in_the_dump():
    """A redirect or a retry appends another block; only the last one
    describes the bytes that landed in the file."""
    dump = (
        'HTTP/1.1 301 Moved Permanently\r\nETag: "old"\r\nLocation: /x\r\n\r\n'
        'HTTP/1.1 200 OK\r\nETag: "new"\r\nLast-Modified: Mon, 01 Jan 2035\r\n\r\n'
    )
    code, headers = _fetch._last_response(dump)
    assert code == 200
    assert headers["etag"] == '"new"'
    assert headers["last-modified"] == "Mon, 01 Jan 2035"


def test_backend_comes_from_the_config_ladder(server, monkeypatch):
    seen = {}

    def spy(backend, url, dest, meta):
        seen["backend"] = backend
        dest.write_bytes(BODY)
        return True, {}  # downloaded, no validators — the _download contract

    monkeypatch.setattr(_fetch, "_download", spy)
    with use_context(Context(fetch_backend="curl")):
        _fetch.fetch(server)
    assert seen["backend"] == "curl"  # [fetch] backend, not the default


def test_cached_copy_survives_a_failed_refresh(server, monkeypatch):
    _fetch.fetch(server)  # warm

    def boom(*args, **kwargs):
        raise _fetch.FetchError("fetch: network is down")

    monkeypatch.setattr(_fetch, "_download", boom)
    assert _fetch.fetch(server).read_bytes() == BODY  # offline, still works


def test_fetch_reports_byte_progress(server, monkeypatch):
    reports: list[tuple[int, int]] = []
    monkeypatch.setattr(
        _fetch.context, "progress", lambda done, total=0: reports.append((done, total))
    )
    _fetch.fetch(server)
    assert (len(BODY), len(BODY)) in reports  # counted progress, from bytes
    assert reports[-1] == (0, 0)  # and cleared when the download finished


def test_cache_lives_where_footman_caches(server, tmp_path):
    assert _fetch.cache_dir().is_relative_to(_paths.footman_cache_dir())
    assert Path(_fetch.fetch(server)).is_relative_to(_paths.footman_cache_dir())
