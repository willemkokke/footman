"""Shared fixtures: load the sample task surface and build its manifest."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from footman import manifest, registry

FIXTURE = Path(__file__).parent / "fixtures" / "sample_tasks.py"


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
