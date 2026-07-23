"""The provisioning engine and task — `fm footman tools provision`.

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
    (_provision.bin_dir(tmp_path) / "bun").write_text("#!/bin/sh\n")
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
    assert placed.read_bytes() == b"go-binary" and placed.name == "gh"


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
    assert (_provision.bin_dir(tmp_path) / "gh").read_bytes() == b"gh!"


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


def test_task_sync_runs_sync_with_prefix_on_path(tmp_path, monkeypatch):
    import os

    from footman.tasks import tools

    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    seen = {}
    monkeypatch.setattr(
        tools, "sync", lambda only="": seen.update(path=os.environ.get("PATH", ""))
    )
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
