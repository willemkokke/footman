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
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _handwritten_docs())
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
    text = (DOCS / rel).resolve().read_text(encoding="utf-8")
    pin = _current_minor_pin()
    assert pin in text, f"{rel}: expected the pin example {pin!r} to be current"


def test_json_version_example_is_current():
    """The --version JSON example on the JSON page tracks __version__."""
    text = (DOCS / "json.md").read_text(encoding="utf-8")
    assert f'"version": "{footman.__version__}"' in text


KINDS = frozenset(
    {
        "Added",
        "Changed",
        "Deprecated",
        "Removed",
        "Fixed",
        "Security",
        "Documentation",
        "CI",
    }
)
"""The kinds a release may sort its entries into.

Keep a Changelog's six, plus the two this project uses: `Documentation`,
and `CI` for a change to the pipeline that ships nothing.
"""


def test_each_changelog_section_lists_a_kind_once():
    """A release's entries of one kind belong under one heading.

    Sessions append rather than look: `[Unreleased]` reached eight headings
    for four kinds — `Fixed` four times — and `[0.26.0]` shipped with two
    `Changed`. It costs nothing until release, when the runbook moves
    `[Unreleased]` wholesale into a version section, and whatever shape it
    is in is the shape that ships.
    """
    import collections
    import re

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        release = section.splitlines()[0].strip()
        kinds = [
            line[4:].strip() for line in section.splitlines() if line.startswith("### ")
        ]
        repeated = {k: n for k, n in collections.Counter(kinds).items() if n > 1}
        assert not repeated, (
            f"{release} lists {repeated} — merge them under one heading"
        )
        # One heading per kind is only half of it: two names for the same
        # kind divide a release just as effectively. `Docs` and
        # `Documentation` ran side by side for seven releases before
        # anybody noticed they were the same section.
        unknown = [k for k in kinds if k not in KINDS]
        assert not unknown, f"{release} uses {unknown}, not one of {sorted(KINDS)}"


def test_foundations_pages_teach_their_guards():
    """Every process-globals guard has a Foundations page teaching its
    ground. The mapping lives here (runtime error texts don't carry doc
    links yet — making them is a separate, user-visible design call); the
    assert is that each page and its load-bearing lesson exist, so a future
    link always has a stable target."""
    lessons = {
        "foundations-process.md": ["process global", "at spawn"],
        "foundations-cwd.md": ["footman.cwd()", "serial", "anchored"],
        "foundations-env.md": ["scope", "putenv", "ctx.env"],
        "foundations-shell.md": ["shell", "pipes"],
        "foundations-spawning.md": ["fork", "process group", "explicit arguments"],
        "foundations-threads.md": ["GIL", "serial lane"],
        "foundations-deadlocks.md": ["hold-and-wait", "boundary", "declared"],
        "foundations-regimes.md": ["declared", "interactive=True", "exclusive=True"],
    }
    nav = (ROOT / "zensical.toml").read_text(encoding="utf-8")
    for name, needles in lessons.items():
        raw = (DOCS / name).read_text(encoding="utf-8")
        text = " ".join(raw.split())  # wrap-proof: prose reflows freely
        assert name in nav, f"{name} exists but is not in the nav"
        for needle in needles:
            assert needle in text, f"{name} lost its lesson: {needle!r}"
