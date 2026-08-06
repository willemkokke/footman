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


# --- the restructure's guards -------------------------------------------------
# Added with the docs restructure (notes/20260805-docs-restructure.md, ruling
# 23): each one fences a class of drift the 2026-08-05 review actually found.


def test_documented_envelope_walk_matches_reality(envelope):
    """The docs teach one Python walk over the envelope (testing.md's golden
    test): task rows discriminated by `"task" in row`, command rows by
    `"command" in row`. Run that walk against a real envelope — and assert
    the flat-model invariant whose violation shipped twice: no items row
    nests a `steps` key."""
    import json

    payload = json.loads(envelope)
    items = payload["items"]
    tasks = [(t["task"], t["ok"]) for t in items if "task" in t]
    commands = [s["command"] for s in items if "command" in s]
    assert ("green", True) in tasks and ("red", False) in tasks
    assert any("echo" in c for c in commands)
    nested = [row for row in items if "steps" in row]
    assert not nested, f"an items row nests 'steps' — the pre-0.28 shape: {nested}"
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _handwritten_docs())
    assert 't["steps"]' not in blob, "a docs example walks the dead nested key"


def test_every_docs_page_is_in_the_nav():
    """A page in docs/ that the nav never mentions is published to nobody —
    color-support.md sat orphaned for a release before anyone noticed."""
    import tomllib

    config = tomllib.loads((ROOT / "zensical.toml").read_text(encoding="utf-8"))

    def walk(node) -> set[str]:
        found: set[str] = set()
        if isinstance(node, str) and node.endswith(".md"):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                found |= walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                found |= walk(value)
        return found

    nav = walk(config["project"]["nav"])
    orphans = [
        p.name
        for p in DOCS.glob("*.md")
        if p.name not in nav and not any(p.name in target for target in nav)
    ]
    assert not orphans, f"docs pages missing from the zensical.toml nav: {orphans}"


def _slug(heading: str) -> str:
    """The site's heading ids, closely enough: lowercase, keep word
    characters (underscores survive — `#around-every-task-pre_task-and-
    post_task` is real), collapse everything else to single hyphens."""
    text = re.sub(r"[^a-z0-9_]+", "-", heading.lower())
    return text.strip("-")


def test_internal_links_and_anchors_resolve():
    """Every `[text](page.md#anchor)` in the hand-written docs points at a
    page that exists and a heading that page has. Clean when this guard
    landed; unguarded links rot silently."""
    link = re.compile(r"\]\(([\w.-]+\.md)(#[\w-]+)?\)")
    problems: list[str] = []
    for path in _handwritten_docs():
        text = path.read_text(encoding="utf-8")
        for match in link.finditer(text):
            target, anchor = match.group(1), match.group(2)
            target_path = path.parent / target
            if not target_path.exists():
                # api.md and the task pages are generated on a fresh
                # checkout; existence is the docs build's assertion.
                if target in ("api.md",) or target.startswith("tasks/"):
                    continue
                problems.append(f"{path.name}: {target} does not exist")
                continue
            if anchor:
                headings = re.findall(
                    r"^#+ (.+)$", target_path.read_text(encoding="utf-8"), re.M
                )
                slugs = {_slug(h) for h in headings}
                if _slug(anchor[1:]) not in slugs:
                    problems.append(f"{path.name}: {target}{anchor} has no heading")
    assert not problems, "dead internal links:\n" + "\n".join(problems)


def test_every_config_key_in_the_source_is_documented():
    """Every key `_config.py`'s own docstring lists has a row in
    configuration.md's Keys table — the `cascade` key was documented
    nowhere while the page promised 'every key'."""
    source = (ROOT / "src" / "footman" / "_config.py").read_text(encoding="utf-8")
    keys = re.findall(r"^\* `(\w+)`", source, re.M)
    assert keys, "the _config.py docstring stopped listing keys — update me"
    table = (DOCS / "configuration.md").read_text(encoding="utf-8")
    missing = [k for k in keys if f"| `{k}`" not in table]
    assert not missing, f"config keys undocumented in configuration.md: {missing}"


def test_refusal_exit_code_claims_track_the_constant():
    """Hand-written claims about the refusal exit code follow `EX_USAGE`.
    The pre-0.21 '2' survived on three pages for eleven releases because
    nothing tied the prose to the constant."""
    from footman._executor import EX_USAGE

    blob = " ".join(
        " ".join(p.read_text(encoding="utf-8").split()) for p in _handwritten_docs()
    )
    assert "2 always means footman refused" not in blob
    assert "Exit code 2, nothing executed" not in blob
    assert f"{EX_USAGE} always means footman refused" in blob, (
        "troubleshooting.md no longer states the refusal code plainly"
    )


def test_glossary_inflections_share_their_definition():
    """The abbreviations glossary carries inflected keys (chain/chains/
    chained/…) so tooltips fire on every spelling; the copies must stay
    textually identical to their stem's definition."""
    lines = (DOCS / "includes" / "abbreviations.md").read_text(encoding="utf-8")
    entries = re.findall(r"^\*\[(.+?)\]: (.+)$", lines, re.M)
    assert entries, "the glossary went empty or changed format — update me"
    by_stem: dict[str, set[str]] = {}
    for key, definition in entries:
        by_stem.setdefault(key.lower()[:4], set()).add(definition)
    diverged = {stem: defs for stem, defs in by_stem.items() if len(defs) > 1}
    assert not diverged, f"glossary inflections diverged: {sorted(diverged)}"
    defs = dict(entries)
    assert defs["shared"] == defs["unshared"], "shared/unshared drifted apart"


def test_our_docstrings_stay_google():
    """footman's own docstrings never use Sphinx fields or RST directives —
    the parser (docstrings.py) legitimately contains them as data, and is
    the one exemption. The rendered API page parses Google style, so a
    violation would render as broken prose."""
    rst = re.compile(r":param |:rtype:|:returns:|^\.\. \w+::", re.M)
    offenders = [
        p.name
        for p in sorted((ROOT / "src" / "footman").glob("*.py"))
        if p.name != "docstrings.py" and rst.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Sphinx/RST markup in our own source: {offenders}"


def _attached_value_globals() -> list[str]:
    """The globals whose value is mandatory, from GLOBALS itself — so the
    guard below tracks the grammar rather than a list someone maintains.
    Optional-value globals (`--describe [ADDR]`) are excluded: a bare word
    after one of those can legitimately be the next token."""
    from footman._split import GLOBALS

    names: list[str] = []
    for long, short, kind, metavar, _help in GLOBALS:
        if kind == "option" and metavar and not metavar.startswith("["):
            names.append(long)
            if short:
                names.append(short)
    return names


ATTACHED = _attached_value_globals()
_OPT = "|".join(map(re.escape, ATTACHED))
# A CAPS value is metavar notation — `--jobs N`, `--where TASK` — which is
# how footman's own --help prints it, so it stays legal everywhere.
_NOT_METAVAR = r"(?![A-Z]+[`\s.,)])"
# Inside an `fm …` line, whatever follows the option is its value, full
# stop: a diagnostic never opens with `fm`.
_ANY_VALUE = rf"{_NOT_METAVAR}[^\s=`|]"
# A bare option span is judged conservatively, because docs also *quote*
# footman's own error messages (`--where expects a value, attached: …`).
# Only shapes no diagnostic uses count: a number, an <angled> placeholder,
# or a dotted filename.
_CLEAR_VALUE = r"(?:\d|<[^>\n]+>|[\w-]+\.[\w]+)"
_SPACE_FORM = re.compile(
    rf"(?:\$ |`)(?:fm|footman)\b[^`\n]*?\s(?:{_OPT})\s+{_ANY_VALUE}"
    rf"|`(?:{_OPT})\s+{_CLEAR_VALUE}"
)


def test_the_attached_value_detector_works():
    """The regex is load-bearing: if it stops matching, the guard below
    passes by finding nothing."""
    assert ATTACHED, "GLOBALS stopped declaring mandatory-value options"
    assert _SPACE_FORM.search("`fm -j 2 check`"), "detector missed a known-bad line"
    assert _SPACE_FORM.search("$ fm --where mytask"), "detector missed a console line"
    # The two that actually shipped, in the shapes they shipped in:
    assert _SPACE_FORM.search("failures, `-j 2` caps the width."), (
        "detector missed the bare backticked option span"
    )
    assert _SPACE_FORM.search("- source: `fm --where <task>` prints file:line."), (
        "detector missed an <angled> placeholder value"
    )
    assert _SPACE_FORM.search("<kbd>Tab</kbd> after `-f <file>` completes"), (
        "detector missed an <angled> value in a bare span"
    )
    assert _SPACE_FORM.search("a typo like `--config prod.tmol` is reported"), (
        "detector missed a dotted-filename value"
    )
    # Metavar notation, which mirrors footman's own --help, stays legal:
    assert not _SPACE_FORM.search("- `-j/--jobs N` caps the width")
    assert not _SPACE_FORM.search("`--where TASK` prints a bare file:line")
    assert not _SPACE_FORM.search("    `-f/--tasks-file PATH` loads a single file")
    # Quoted diagnostics are prose about an option, not invocations of it:
    assert not _SPACE_FORM.search(
        "| `--where expects a value, attached: --where=TASK` | given bare |"
    )
    assert not _SPACE_FORM.search("`--jobs (from $JOBS) must be between 1 and 32`")
    assert not _SPACE_FORM.search("`fm -j=2 check`"), "detector flags the good form"
    assert not _SPACE_FORM.search("- `-j/--jobs N` caps the width"), (
        "detector flags metavar notation, which mirrors footman's own --help"
    )


def test_documented_invocations_attach_their_values():
    """No documented `fm …` line passes a mandatory value across a space.

    footman refuses that form — exit 64, `-j takes its value attached — did
    you mean -j=2?` — so an example spelling it teaches a command line the
    reader cannot run. Four shipped this way (`-j 2` in the cookbook,
    `fm --where <task>` in agents.md, `-f <file>` in monorepos.md,
    `--config prod.tmol` in troubleshooting.md); nothing tied the examples
    to the grammar until this guard. Deliberately conservative on bare
    option spans, because the docs also quote footman's own diagnostics:
    only a number, an <angled> placeholder or a dotted filename counts
    there, so a lowercase prose word after an option is left alone.
    """
    problems: list[str] = []
    for path in sorted(_handwritten_docs()):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _SPACE_FORM.search(line)
            if match:
                problems.append(f"{path.name}:{number}: {match.group(0).strip()}")
    assert not problems, (
        "documented invocations using the refused space form "
        "(values are always `=`-attached):\n" + "\n".join(problems)
    )
