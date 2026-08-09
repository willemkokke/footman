"""The chain grammar: separator-free splitting, arity, validation, errors."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from footman import _manifest
from footman._split import ChainError, split_chain
from footman.registry import Group


class Box(NamedTuple):
    width: int
    height: int


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def segs(tree, line):
    _, segments = split_chain(tree, line.split())
    return segments


def globs(tree, line):
    globals_, _ = split_chain(tree, line.split())
    return globals_


def test_single_task(tree):
    (seg,) = segs(tree, "check")
    assert seg.task == "check"
    assert seg.values == {}
    assert seg.variadic == []
    assert seg.passthrough is None


def test_flags_and_options_split_into_segments(tree):
    result = segs(tree, "format --fix lint --fix --mode=strict typecheck test")
    assert [s.task for s in result] == ["format", "lint", "typecheck", "test"]
    assert result[0].values == {"fix": True}
    assert result[1].values == {"fix": True, "mode": "strict"}
    assert result[2].values == {}
    assert result[3].values == {}


def test_repeated_option_collects_a_list(tree):
    (seg,) = segs(
        tree, "test --marker=slow --path=tests/unit --path=tests/e2e --coverage"
    )
    assert seg.values == {
        "marker": "slow",
        "path": ["tests/unit", "tests/e2e"],
        "coverage": True,
    }


def test_leading_globals(tree):
    assert globs(tree, "-k -q format lint test") == ["--keep-going", "--quiet"]
    assert [s.task for s in segs(tree, "-k -q format lint test")] == [
        "format",
        "lint",
        "test",
    ]


def test_interactivity_globals(tree):
    # --yes / --no-input parse as leading globals; -y canonicalises to --yes.
    assert globs(tree, "-y --no-input format") == ["--yes", "--no-input"]
    assert [s.task for s in segs(tree, "-y --no-input format")] == ["format"]


def test_color_global_takes_a_value(tree):
    # --color is a valued global; --no-color stays a bare flag beside it.
    assert globs(tree, "--color=always format") == ["--color=always"]
    assert globs(tree, "--color=never format") == ["--color=never"]
    assert globs(tree, "--no-color format") == ["--no-color"]
    assert [s.task for s in segs(tree, "--color=always format")] == ["format"]


def test_dotted_address_and_typed_option(tree):
    (seg,) = segs(tree, "docs.serve --port=8001")
    assert seg.task == "docs.serve"
    assert seg.path == ["docs", "serve"]
    assert seg.values == {"port": "8001"}


def test_required_positional_then_option(tree):
    a, b = segs(tree, "docs.build --strict deploy staging --version=2026.07.16")
    assert a.task == "docs.build"
    assert a.values == {"strict": True}
    assert b.task == "deploy"
    assert b.values == {"env": "staging", "version": "2026.07.16"}


def test_two_required_positionals_repeated_task(tree):
    a, b = segs(
        tree,
        "render templates/report.j2 out/report.html "
        "render templates/index.j2 out/index.html",
    )
    assert a.values == {"template": "templates/report.j2", "output": "out/report.html"}
    assert b.values == {"template": "templates/index.j2", "output": "out/index.html"}


def test_explicit_plus_boundary_before_variadic(tree):
    a, b = segs(tree, "deps.add requests rich typer + lint --fix")
    assert a.task == "deps.add"
    assert a.variadic == ["requests", "rich", "typer"]
    assert b.task == "lint"
    assert b.values == {"fix": True}


def test_variadic_consumes_rest_of_segment(tree):
    (seg,) = segs(tree, "run ruff check src")
    assert seg.variadic == ["ruff", "check", "src"]


def test_passthrough_is_terminal(tree):
    (seg,) = segs(tree, "test --marker=unit -- -k manifest_or_split -x")
    assert seg.values == {"marker": "unit"}
    assert seg.passthrough == ["-k", "manifest_or_split", "-x"]


def test_no_flag_negation(tree):
    (seg,) = segs(tree, "docs.serve --no-live")
    assert seg.values == {"live": False}


def test_option_equals_form(tree):
    (seg,) = segs(tree, "lint --mode=strict")
    assert seg.values == {"mode": "strict"}


def test_a_bare_mention_records_presence_and_no_value(tree):
    # `--mode` alone is legal wherever absence is: it carries no value, so the
    # binder runs the same ladder it would with no mention at all. What it adds
    # is that someone asked, which `given()` reads.
    (seg,) = segs(tree, "lint --mode")
    assert seg.values == {}
    assert seg.bare == {"mode"}


def test_a_bare_mention_before_a_passthrough(tree):
    (seg,) = segs(tree, "lint --mode -- x")
    assert seg.bare == {"mode"}
    assert seg.passthrough == ["x"]


def test_a_bare_mention_does_not_disturb_the_tokens_around_it(tree):
    # Four independent decisions: the task, a bare option, a positional, then a
    # word with nowhere left to go — which starts the next segment.
    first, second = segs(tree, "deploy --version prod lint")
    assert first.task == "deploy"
    assert first.bare == {"version"}
    assert first.values == {"env": "prod"}
    assert second.task == "lint"


def test_dash_leading_value_attaches(tree):
    # A value that starts with a dash parses trivially in attached form —
    # the case the space form could never express.
    assert globs(tree, "--jobs=-1 check") == ["--jobs=-1"]
    (seg,) = segs(tree, "bench --timeout=-1.5")
    assert seg.values == {"timeout": "-1.5"}


def test_where_global_takes_a_value(tree):
    assert globs(tree, "--where=docker.build") == ["--where=docker.build"]
    assert segs(tree, "--where=docker.build") == []


ERROR_CASES = [
    ("lint --mode=fast", "lint: --mode must be one of strict|loose (got 'fast')"),
    ("docs.serve --port=http", "docs.serve: --port expects an integer (got 'http')"),
    ("bench --timeout=fast", "bench: --timeout expects a number (got 'fast')"),
    # A value is always `=`-attached, so the space form is two tokens: `--mode`
    # binds its default and `strict` is read as the next task. That is the only
    # reading available — but the line fails, so the failure carries the fix.
    ("lint --mode strict", "did you mean --mode=strict?"),
    ("--color always lint", "did you mean --color=always?"),
    # `--jobs` has a default, so a bare mention is legal and means it — but
    # `4` then has nowhere to go, and the failure carries the fix.
    ("--jobs 4 check", "did you mean --jobs=4?"),
    ("-j 4 check", "did you mean -j=4?"),
    # Bare with nothing attachable following: state the shape instead.
    ("--where lint", "--where takes its value attached — did you mean --where=lint?"),
    # A value-optional global is valid bare — but a detached value behind it
    # still teaches the attachment, never "unknown task 'zsh'".
    (
        "--install-completion zsh",
        "--install-completion takes its value attached — "
        "did you mean --install-completion=zsh?",
    ),
    ("version huge", "version: <part> must be one of major|minor|patch (got 'huge')"),
    (
        "deploy check",
        "deploy: <env> must be one of dev|staging|prod — 'check' looks like "
        "the next task; did you forget <env>?",
    ),
    ("lint test --fix", "test: unknown option --fix"),
    # The space form of a nested address is permanently taught, never parsed.
    ("docs serve", "nested tasks use dots: 'docs.serve', not 'docs serve'"),
    (
        "docs serve --port 8001",
        "nested tasks use dots: 'docs.serve', not 'docs serve'",
    ),
    # A bare namespace group is never a segment target; the answer lists
    # its children as addresses.
    (
        "docs",
        "'docs' is a group, not a task — name one of its tasks "
        "(know: docs.serve, docs.build)",
    ),
    (
        "docs deplo",
        "'docs' is a group, not a task — name one of its tasks "
        "(know: docs.serve, docs.build)",
    ),
    # The teach stops at the longest resolvable prefix — the rest of the
    # line is someone else's segment.
    ("docs build lint", "nested tasks use dots: 'docs.build', not 'docs build'"),
    # Strict addresses: empty segments and hanging dots are taught, never
    # silently normalised.
    ("docs.", "'docs.' is an incomplete address (know: docs.serve, docs.build)"),
    ("docs..serve", "'docs..serve' is not a task address"),
    (".docs", "'.docs' is not a task address"),
    ("check.deep", "'check' is a task, not a group — nothing lives beneath it"),
    ("docs.sevre", "no task named 'docs.sevre' — did you mean 'docs.serve'?"),
    # A misplaced global names the real problem — position — not "unknown".
    (
        "--json lint --quiet",
        "lint: --quiet is a global option — it goes before the first task name",
    ),
    (
        "lint -k",
        "-k (--keep-going) is a global option — it goes before the first task name",
    ),
    (
        "check + --json",
        "--json is a global option — it goes before the first task name",
    ),
    ("render only-one", "render: missing required positional(s): <output>"),
    ("--nope check", "unknown global option --nope"),
    ("--sequential=false lint", "--sequential is a flag and takes no value"),
    ("--json=0 lint", "--json is a flag and takes no value"),
    ("chekc", "did you mean 'check'?"),  # unknown task → nearest name
    ("lint --fux", "did you mean '--fix'?"),  # unknown option → nearest option
    ("lint --mode=strikt", "did you mean 'strict'?"),  # unknown choice value
]


@pytest.mark.parametrize("line, message", ERROR_CASES)
def test_teaching_errors(tree, line, message):
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, line.split())
    assert message in str(excinfo.value)


def test_unmatchable_typo_gets_no_suggestion(tree):
    # No false confidence: a word close to nothing known adds no "did you mean".
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["zzzzzzzz"])
    assert "did you mean" not in str(excinfo.value)


def test_a_grouped_shape_is_refused_before_anything_runs():
    """The manifest carries a group's arity and per-slot types, so a wrong
    value never needs a run to be found — the same eager treatment every
    other typed parameter gets."""

    def tasks(reg):
        @reg.task
        def render(size: Box = Box(0, 0)): ...

        @reg.task
        def route(points: list[Box] = ()): ...  # type: ignore[assignment]

    _, tree = build_tree(tasks)

    with pytest.raises(ChainError, match=r"height expects an integer"):
        split_chain(tree, ["render", "--size=800,tall"])
    with pytest.raises(ChainError, match=r"--size takes width,height — got 1"):
        split_chain(tree, ["render", "--size=800"])
    with pytest.raises(ChainError, match=r"groups of 2 \(width,height\)"):
        split_chain(tree, ["route", "--points=1,2,3"])
    # Repetition and commas feed one stream, so the arity is only final at
    # the end of the segment — these are the same three values.
    with pytest.raises(ChainError, match=r"leaves 1 over"):
        split_chain(tree, ["route", "--points=1,2", "--points=3"])
    split_chain(tree, ["route", "--points=1,2", "--points=3,4"])  # whole: fine
