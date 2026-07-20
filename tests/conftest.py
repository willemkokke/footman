"""Shared fixtures: load the sample task surface and build its manifest."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from footman import manifest, registry

FIXTURE = Path(__file__).parent / "fixtures" / "sample_tasks.py"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def pytest_configure(config: pytest.Config) -> None:
    """Measure the children too, but only when the parent is measuring.

    footman's most interesting code runs in processes it spawns: the
    completion hot path every shell hook invokes, the detached manifest
    refresh, the cache collector, the uv handoff, the `fm` inside a docs
    cast. `COVERAGE_PROCESS_START` makes coverage's installed `.pth` arm
    itself in each child; setting it unconditionally would start a
    coverage session in every subprocess of every plain test run, so it
    is set only when a `--cov` run is actually in progress.
    """
    plugin = config.pluginmanager.get_plugin("_cov")
    if plugin is not None and getattr(plugin, "cov_controller", None) is not None:
        os.environ["COVERAGE_PROCESS_START"] = str(PYPROJECT)


@pytest.fixture(autouse=True)
def _no_cache_override(monkeypatch, tmp_path_factory):
    """A real FOOTMAN_CACHE_DIR would bypass every cache_home patch — the
    override is env-first by design, so the suite must clear it. The same
    goes for the user-level config file: the developer's own
    ~/.config/footman/config.toml must never leak settings into the suite,
    so FOOTMAN_CONFIG points at a path that doesn't exist. The
    step-alignment width is a per-run learning global for the same reason:
    reset it, or one test's wide command pads another's lines."""
    monkeypatch.delenv("FOOTMAN_CACHE_DIR", raising=False)
    monkeypatch.setenv(
        "FOOTMAN_CONFIG",
        str(tmp_path_factory.getbasetemp() / "no-global-config.toml"),
    )
    from footman import context

    context.seed_cmd_width(0)


def load_tasks(path: Path) -> registry.Group:
    """Import a tasks file into an isolated registry (no global leak).

    Importing under `registry.capture()` keeps the ~25 sample tasks out of the
    process-global `registry.root` — isolating both directions: prior session
    state can't pollute the fixture, and the fixture can't pollute later tests.
    """
    spec = importlib.util.spec_from_file_location("sample_tasks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with registry.capture() as captured:
        spec.loader.exec_module(module)
    return captured


@pytest.fixture
def root() -> registry.Group:
    return load_tasks(FIXTURE)


@pytest.fixture
def tree(root: registry.Group) -> dict:
    return manifest.build_manifest(root)["tree"]
