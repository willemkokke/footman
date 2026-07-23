"""Docs-drift guards — the "audit, don't transcribe" approach.

Rather than generate prose, these tests fail the gate when the hand-written
docs fall behind the source: a new public symbol that nobody documented, or a
version pin/example that went stale after a release bump. They read the repo's
own files, so they only run meaningfully from a source checkout.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import footman

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _handwritten_docs() -> list[Path]:
    return [
        p
        for p in DOCS.rglob("*.md")
        if "_generated" not in p.parts and "htmlcov" not in p.parts
    ]


def test_every_public_symbol_is_documented():
    """Every name re-exported from `footman` appears somewhere in the
    hand-written docs. Catches a new public export that shipped undocumented —
    the drift the reference cheatsheet hit with .opts()/forward/ask."""
    blob = "\n".join(p.read_text() for p in _handwritten_docs())
    exported = [n for n in footman.__all__ if not n.startswith("__")]
    missing = [n for n in exported if not re.search(rf"\b{re.escape(n)}\b", blob)]
    assert not missing, f"public symbols undocumented in docs/: {missing}"


def _current_minor_pin() -> str:
    major, minor, *_ = footman.__version__.split(".")
    return f"footman~={major}.{minor}.0"


@pytest.mark.parametrize("rel", ["../README.md", "index.md"])
def test_minor_pin_example_tracks_the_release(rel):
    """The `pin the minor` example (README + docs home) tracks __version__,
    so it can't sit several minors stale after a bump."""
    text = (DOCS / rel).resolve().read_text()
    pin = _current_minor_pin()
    assert pin in text, f"{rel}: expected the pin example {pin!r} to be current"


def test_json_version_example_is_current():
    """The --version JSON example on the JSON page tracks __version__."""
    text = (DOCS / "json.md").read_text()
    assert f'"version": "{footman.__version__}"' in text
