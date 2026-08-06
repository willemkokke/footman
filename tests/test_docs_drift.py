"""Docs-drift guards — the "audit, don't transcribe" approach.

Rather than generate prose, these tests fail the gate when the hand-written
docs fall behind the source: a new public symbol that nobody documented, or a
version pin/example that went stale after a release bump. They read the repo's
own files, so they only run meaningfully from a source checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import footman

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

sys.path.insert(0, str(ROOT))

from footman import registry as _registry  # noqa: E402

# capture(): the repo's own tasks must not land in the process-global
# registry (test_registry's leak guard trips when xdist co-schedules them).
with _registry.capture():
    import tasks


def _handwritten_docs() -> list[Path]:
    return [
        p
        for p in DOCS.rglob("*.md")
        if "_generated" not in p.parts and "htmlcov" not in p.parts
    ]


def test_every_public_symbol_is_documented():
    """Every name re-exported from `footman` appears somewhere in the docs.
    Catches a new public export that shipped undocumented — the drift the
    reference cheatsheet hit with .opts()/forward/ask. The API page is
    generated (`fm docs.api`), so its content joins the blob by construction
    rather than from disk — a fresh checkout has no docs/api.md until the
    docs build runs."""
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _handwritten_docs())
    blob += "\n" + tasks._api_markdown()
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
    }
)
"""The kinds a release may sort its entries into.

Keep a Changelog's six, plus `Documentation`. A change to the pipeline is
a `Changed`: `CI` was a seventh kind for three entries in two releases of
July 2026, and nothing had used it since.
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


# --- the documented jq recipes ------------------------------------------------
# Prose about the JSON envelope is audited above; these run it. A recipe is a
# consumer like any other, and the one thing no reader of a doc can do is
# notice that the key it walks was renamed three commits ago.

_JQ = re.compile(r"jq\s+(?:-[a-zA-Z]+\s+)*'([^']+)'", re.S)


def _documented_jq() -> list[tuple[str, str]]:
    """`(file:line, program)` for every jq recipe in the hand-written docs."""
    found: list[tuple[str, str]] = []
    for path in sorted(_handwritten_docs()):
        text = path.read_text(encoding="utf-8")
        for match in _JQ.finditer(text):
            line = text[: match.start()].count("\n") + 1
            found.append((f"{path.name}:{line}", match.group(1)))
    return found


RECIPES = _documented_jq()

_ENVELOPE_TASKS = """
import footman
from footman import task


@task
def green():
    "A task that shells out."
    footman.run("echo hello")


@task
def red():
    "A task that fails."
    footman.fail("boom", 3)
"""


@pytest.fixture
def envelope(tmp_path, monkeypatch, capsys) -> str:
    """A real `--json` envelope carrying all three row shapes a recipe walks:
    a task that passed, the step it ran, and a task that failed.

    Real rather than a checked-in fixture, because a fixture would have gone
    stale in exactly the way the recipes did.
    """
    from footman import _app, _paths

    (tmp_path / "tasks.py").write_text(_ENVELOPE_TASKS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _app.run(["--json", "-k", "green", "red"])
    printed: str = capsys.readouterr().out  # capsys is Any-typed; name it once
    return printed


def _jq(program: str, text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", program], input=text, capture_output=True, text=True, check=False
    )


def test_the_docs_still_carry_jq_recipes():
    """The regex above is load-bearing: if it stops matching, every test
    below passes by finding nothing."""
    assert len(RECIPES) >= 5, f"only found {RECIPES}"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
@pytest.mark.parametrize(("where", "program"), RECIPES, ids=[w for w, _ in RECIPES])
def test_documented_jq_recipes_run_against_a_real_envelope(where, program, envelope):
    """Every documented recipe walks keys that exist.

    jq exits 2 on a usage problem, 3 on a compile error and 5 on a runtime
    one; `-e` recipes exit 1 to *mean* false, which is an answer rather than
    a failure. So the verdict is stderr — jq names what it could not do
    there — and the three error codes.

    This is the guard the `results` -> `items` rename went past: the docs
    and the refresh workflow both kept reading a key that no longer existed,
    and the first thing to notice was a scheduled job failing on a Monday.
    """
    done = _jq(program, envelope)
    assert not done.stderr, f"{where}: jq refused this recipe — {done.stderr.strip()}"
    assert done.returncode not in (2, 3, 5), f"{where}: jq exited {done.returncode}"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_the_agent_gate_recipe_reports_only_real_failures(envelope):
    """agents.md's hook feeds an agent the tasks that failed, filtering on
    `select(.ok | not)`. The items envelope is one flat list, and a step row
    carries no `ok` at all — `null | not` is true — so without a
    `select(.task)` guard the recipe reports a task called `null`, on a gate
    that was green. Exit codes cannot catch that one: jq is perfectly happy.
    """
    program = next(p for where, p in RECIPES if where.startswith("agents.md"))
    done = _jq(program, envelope)
    assert not done.stderr
    assert "red" in done.stdout, "the task that really failed went unreported"
    assert "null" not in done.stdout, "a step row was reported as a failed task"
