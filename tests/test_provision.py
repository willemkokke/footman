"""The provisioning engine and task — `fm tools.provision`.

The tiers are driven with the real driver metadata but mocked at their one
outward edge (subprocess, HTTP), so the grouping, dedup, asset matching and
unpacking are exercised without installing anything or hitting the network.
"""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from footman import _provision
from footman._drivers import Driver, Provision


def _tar_gz(path: Path, arcname: str, data: bytes) -> None:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(arcname)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _zip(path: Path, arcname: str, data: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(arcname, data)


# --- tiers -------------------------------------------------------------------


def test_system_and_deferred_are_reported_not_fetched(tmp_path):
    drivers = (
        Driver("git", provision=Provision(kind="system")),
        Driver(
            "tea", provision=Provision(kind="deferred", note="hangs until > 0.14.2")
        ),
    )
    by = {o.key: o for o in _provision.provision(drivers, tmp_path)}
    assert by["git"].status == "skip" and "system git" in by["git"].detail
    assert by["tea"].status == "deferred" and "hangs" in by["tea"].detail


def test_uv_tier_installs_each_package_once(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        _provision, "_run", lambda argv, env: calls.append((argv, env)) or True
    )
    drivers = (
        Driver("ruff", provision=Provision()),
        Driver("ruff", attr="ruff_format", base=("format",), provision=Provision()),
        Driver("mypy", provision=Provision()),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    assert [argv[-1] for argv, _ in calls] == ["ruff", "mypy"]  # deduped
    assert all(o.status == "ok" for o in outcomes)
    argv, env = calls[0]
    assert argv[:4] == ["uv", "tool", "install", "--upgrade"]
    assert env["UV_TOOL_BIN_DIR"] == str(_provision.bin_dir(tmp_path))
    assert env["UV_TOOL_DIR"] == str(tmp_path / "uv-tools")


def test_uv_tier_failure_is_a_fail_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(_provision, "_run", lambda argv, env: False)
    (out,) = _provision.provision((Driver("ruff"),), tmp_path)
    assert out.status == "fail"


def test_node_tier_fails_without_bun(tmp_path):
    drivers = (Driver("cspell", provision=Provision(kind="node")),)
    (out,) = _provision.provision(drivers, tmp_path)
    assert out.status == "fail" and "bun" in out.detail


def test_node_tier_installs_through_bun(tmp_path, monkeypatch):
    _provision.bin_dir(tmp_path).mkdir(parents=True)
    bun_name = "bun.exe" if sys.platform == "win32" else "bun"
    (_provision.bin_dir(tmp_path) / bun_name).write_text("#!/bin/sh\n")
    calls: list = []
    monkeypatch.setattr(
        _provision, "_run", lambda argv, env: calls.append((argv, env)) or True
    )
    drivers = (
        Driver("cspell", provision=Provision(kind="node")),
        Driver(
            "markdownlint-cli2", attr="markdownlint", provision=Provision(kind="node")
        ),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    argv, env = calls[0]
    assert argv[1:3] == ["add", "--global"]
    assert argv[3:] == ["cspell", "markdownlint-cli2"]  # sorted, deduped
    assert env["BUN_INSTALL"] == str(tmp_path)
    assert all(o.status == "ok" for o in outcomes)


# --- asset selection ---------------------------------------------------------


@pytest.fixture
def mac_arm(monkeypatch):
    monkeypatch.setattr(_provision.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_provision.platform, "machine", lambda: "arm64")


def test_pick_asset_matches_aliases_and_prefers_archive(mac_arm):
    assets = [
        ("tool_Linux_x86_64.tar.gz", "linux"),
        ("tool-darwin-aarch64", "bare"),  # aarch64 == arm64; bare binary
        ("tool_macOS_arm64.tar.gz", "archive"),  # macOS == darwin
        ("tool_macOS_arm64.tar.gz.sha256", "sidecar"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "archive"  # archive beats the bare binary, sidecar excluded


def test_pick_asset_no_match_raises(mac_arm):
    with pytest.raises(_provision.ProvisionError, match="no release asset"):
        _provision._pick_asset([("tool_Windows_x86_64.zip", "u")])


@pytest.fixture
def win_amd64(monkeypatch):
    monkeypatch.setattr(_provision.platform, "system", lambda: "Windows")
    monkeypatch.setattr(_provision.platform, "machine", lambda: "AMD64")


def test_pick_asset_win_never_matches_the_tail_of_darwin(win_amd64):
    """bun's spelling. `bun-darwin-x64.zip` contains `win` and is one
    character shorter than the Windows asset, so substring matching plus the
    shortest-name tiebreak shipped a Mach-O binary to every Windows box."""
    assets = [
        ("bun-darwin-x64.zip", "mac"),
        ("bun-windows-x64.zip", "win"),
        ("bun-windows-x64-baseline.zip", "variant"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "win"


def test_pick_asset_goreleaser_spelling_on_windows(win_amd64):
    assets = [
        ("eclint_Darwin_x86_64.tar.gz", "mac"),
        ("eclint_Linux_x86_64.tar.gz", "linux"),
        ("eclint_Windows_x86_64.tar.gz", "win"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "win"


# --- extraction --------------------------------------------------------------


def test_extract_binary_from_tar_gz(tmp_path):
    archive = tmp_path / "eclint_Darwin_arm64.tar.gz"
    _tar_gz(archive, "eclint-0.6/eclint", b"ELF-ish")
    placed = _provision._extract_binary(archive, "eclint", tmp_path / "bin")
    assert placed.read_bytes() == b"ELF-ish"
    if sys.platform != "win32":
        assert placed.stat().st_mode & 0o111  # +x — Windows has no exec bit


def test_extract_binary_from_zip(tmp_path):
    archive = tmp_path / "gh_macOS_arm64.zip"
    _zip(archive, "gh_2.0_macOS_arm64/bin/gh", b"go-binary")
    placed = _provision._extract_binary(archive, "gh", tmp_path / "bin")
    want = "gh.exe" if sys.platform == "win32" else "gh"
    assert placed.read_bytes() == b"go-binary" and placed.name == want


def test_extract_binary_names_the_exe_on_windows(tmp_path):
    """PATHEXT makes an extensionless PE invisible to `shutil.which`, so the
    placed name carries `.exe` even when the archive member did not. The
    platform arrives as a parameter (the `_bash_path` idiom) — patching the
    global `os.name` takes down the whole xdist worker on POSIX 3.11."""
    archive = tmp_path / "eclint_Windows_x86_64.tar.gz"
    _tar_gz(archive, "eclint-0.6/eclint", b"PE-ish")
    placed = _provision._extract_binary(
        archive, "eclint", tmp_path / "bin", windows=True
    )
    assert placed.name == "eclint.exe" and placed.read_bytes() == b"PE-ish"


def test_extract_binary_missing_is_an_error(tmp_path):
    archive = tmp_path / "x.tar.gz"
    _tar_gz(archive, "something-else", b"nope")
    with pytest.raises(_provision.ProvisionError, match="not found inside"):
        _provision._extract_binary(archive, "gh", tmp_path / "bin")


# --- release tier end to end -------------------------------------------------


def test_release_github_flow(tmp_path, monkeypatch, mac_arm):
    monkeypatch.setattr(
        _provision,
        "_get_json",
        lambda url: {
            "assets": [
                {
                    "name": "gh_macOS_arm64.zip",
                    "browser_download_url": "http://x/gh.zip",
                }
            ]
        },
    )

    def fake_download(url, prefix):
        archive = prefix / ".cache" / "gh.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        _zip(archive, "gh/bin/gh", b"gh!")
        return archive

    monkeypatch.setattr(_provision, "_download", fake_download)
    driver = Driver("gh", provision=Provision(kind="github", repo="cli/cli"))
    (out,) = _provision.provision((driver,), tmp_path)
    assert out.status == "ok"
    want = "gh.exe" if sys.platform == "win32" else "gh"
    assert (_provision.bin_dir(tmp_path) / want).read_bytes() == b"gh!"


def test_release_gitlab_parses_links(monkeypatch):
    monkeypatch.setattr(
        _provision,
        "_get_json",
        lambda url: {
            "assets": {
                "links": [{"name": "eclint_Darwin_arm64.tar.gz", "url": "http://u"}]
            }
        },
    )
    assets = _provision._latest_assets("gitlab", "willemkokke/eclint")
    assert assets == [("eclint_Darwin_arm64.tar.gz", "http://u")]


def test_release_missing_repo_fails(tmp_path):
    driver = Driver("gh", provision=Provision(kind="github"))
    (out,) = _provision.provision((driver,), tmp_path)
    assert out.status == "fail" and "no repo" in out.detail


def test_latest_assets_unknown_host_raises():
    with pytest.raises(_provision.ProvisionError, match="unknown release host"):
        _provision._latest_assets("bitbucket", "a/b")


# --- the low-level HTTP edges (mocked urlopen) -------------------------------


def test_get_json_reads_response(monkeypatch):
    monkeypatch.setattr(
        _provision.urllib.request,
        "urlopen",
        lambda req, timeout=0: io.BytesIO(b'{"tag_name": "v1"}'),
    )
    assert _provision._get_json("http://x")["tag_name"] == "v1"


def test_get_json_error_is_provision_error(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("no net")

    monkeypatch.setattr(_provision.urllib.request, "urlopen", boom)
    with pytest.raises(_provision.ProvisionError):
        _provision._get_json("http://x")


def test_download_caches_by_name(tmp_path, monkeypatch):
    hits = []
    monkeypatch.setattr(
        _provision.urllib.request,
        "urlopen",
        lambda req, timeout=0: hits.append(1) or io.BytesIO(b"payload"),
    )
    first = _provision._download("http://x/thing.tar.gz", tmp_path)
    second = _provision._download("http://x/thing.tar.gz", tmp_path)
    assert first == second and first.read_bytes() == b"payload"
    assert len(hits) == 1  # second call served from cache


# --- the task ----------------------------------------------------------------


def test_task_prints_table_and_export(tmp_path, monkeypatch, capsys):
    from footman.tasks import tools

    monkeypatch.setattr(
        _provision,
        "provision",
        lambda drivers, prefix, only="": [
            _provision.Outcome("ruff", "uv", "ok", "ruff")
        ],
    )
    tools.provision(prefix=tmp_path)
    out = capsys.readouterr().out
    assert "ok" in out and "ruff" in out
    assert f'export PATH="{_provision.bin_dir(tmp_path)}:$PATH"' in out


def test_task_sync_runs_sync_against_the_prefix(tmp_path, monkeypatch):
    """`--sync` hands the prefix to `sync`, which puts its `bin/` on PATH for
    the read — the same `--prefix` any caller can pass by hand."""
    import os

    from footman.tasks import tools

    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    seen = {}

    def fake_sync(only="", prefix=""):
        with tools._on_path(prefix):
            seen.update(only=only, path=os.environ.get("PATH", ""))

    monkeypatch.setattr(tools, "sync", fake_sync)
    tools.provision(prefix=tmp_path, sync_=True)
    assert str(_provision.bin_dir(tmp_path)) in seen["path"]


def test_pytest_provisions_with_its_cov_plugin():
    from footman import _drivers

    pytest_driver = next(d for d in _drivers.DRIVERS if d.key == "pytest")
    # The prefix install carries pytest-cov, so provision reads a pytest whose
    # --cov* flags are present — no dev-env special case, no skip.
    assert pytest_driver.provision.plugins == ("pytest-cov",)


def test_uv_tier_installs_plugins_as_with_packages(tmp_path, monkeypatch):
    from footman._drivers import Driver, Provision

    calls: list[list[str]] = []
    monkeypatch.setattr(
        _provision, "_run", lambda argv, env: calls.append(argv) or True
    )
    drivers = (Driver("pytest", provision=Provision(plugins=("pytest-cov",))),)
    outcomes = _provision.provision(drivers, tmp_path)
    argv = calls[0]
    assert argv[:4] == ["uv", "tool", "install", "--upgrade"]
    assert "pytest" in argv and "--with=pytest-cov" in argv
    assert outcomes[0].status == "ok" and "pytest-cov" in outcomes[0].detail


def test_task_clean_removes_prefix(tmp_path, monkeypatch):
    from footman.tasks import tools

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    tools.provision(prefix=prefix, clean=True)
    assert not prefix.exists()


def test_a_token_reaches_the_api_and_nothing_else(monkeypatch):
    """GitHub allows 60 unauthenticated API calls an hour *per IP* and 5,000
    with a token. Sixty is ample for two forge-hosted tools until the IP is a
    shared CI runner, where strangers spend the budget.

    Scoped to the API host deliberately: urllib carries headers across
    redirects, and a release asset redirects to a CDN that has no business
    seeing a credential.
    """
    from footman._provision import api_headers

    monkeypatch.setenv("GH_TOKEN", "s3cret")
    assert api_headers("https://api.github.com/repos/cli/cli/releases") == {
        "User-Agent": "footman-provision",
        "Authorization": "Bearer s3cret",
    }
    for elsewhere in (
        "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun.zip",
        "https://objects.githubusercontent.com/whatever",
        "https://gitlab.com/api/v4/projects/x/releases",
        "https://pypi.org/pypi/ruff/json",
        "https://registry.npmjs.org/cspell",
    ):
        assert "Authorization" not in api_headers(elsewhere), elsewhere


def test_the_older_github_token_spelling_is_accepted(monkeypatch):
    """Actions exports `GITHUB_TOKEN`; `gh` exports `GH_TOKEN`. Both, so the
    workflow and a laptop need not disagree."""
    from footman._provision import api_headers

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "from-actions")
    url = "https://api.github.com/rate_limit"
    assert api_headers(url)["Authorization"] == "Bearer from-actions"


def test_no_token_still_works_just_on_the_smaller_budget(monkeypatch):
    """A token is an offer, never a requirement — a fresh clone with no
    credentials still primes, against 60 calls an hour."""
    from footman._provision import api_headers

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert api_headers("https://api.github.com/rate_limit") == {
        "User-Agent": "footman-provision"
    }


def test_the_interpreter_is_placed_however_the_platform_allows(tmp_path, monkeypatch):
    """Windows grants symlinks only with developer mode or elevation, and a
    copied `python.exe` is a broken interpreter — CPython finds its standard
    library relative to the real executable, so a lone copy finds nothing.
    A launcher is the one fallback that still runs."""
    import os

    from footman import _provision

    target = tmp_path / "real" / "python"
    target.parent.mkdir()
    target.write_text("#!/bin/sh\n")

    placed = _provision._place_interpreter(tmp_path / "bin", target)
    assert placed is not None and placed.exists()
    assert placed.resolve() == target.resolve()  # a symlink, where they work

    def refuse(*_a, **_k):
        raise OSError("a required privilege is not held by the client")

    monkeypatch.setattr(_provision.Path, "symlink_to", refuse)
    monkeypatch.setattr(os, "name", "nt")
    placed = _provision._place_interpreter(tmp_path / "win", target)
    assert placed is not None and placed.name == "python.cmd"
    assert str(target) in placed.read_text(encoding="utf-8")
