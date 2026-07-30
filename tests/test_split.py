"""The chain grammar: separator-free splitting, arity, validation, errors."""

from __future__ import annotations

import pytest

from footman._split import ChainError, split_chain


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
    # A value is always `=`-attached. The space form is permanently taught —
    # with the user's own value in the fix, never "unknown task 'strict'".
    (
        "lint --mode strict",
        "lint: --mode takes its value attached — did you mean --mode=strict?",
    ),
    (
        "--color always lint",
        "--color takes its value attached — did you mean --color=always?",
    ),
    (
        "--jobs 4 check",
        "--jobs takes its value attached — did you mean --jobs=4?",
    ),
    ("-j 4 check", "-j takes its value attached — did you mean -j=4?"),
    # Bare with nothing attachable following: state the shape instead.
    ("lint --mode", "lint: --mode expects a value, attached: --mode=VALUE"),
    ("--jobs check --fix", "--jobs takes its value attached"),
    ("--jobs", "--jobs expects a value, attached: --jobs=N"),
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
    ("docs.sevre", "no task at 'docs.sevre' — did you mean 'docs.serve'?"),
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
    ("render only-one", "render: missing required argument(s): <output>"),
    ("--nope check", "unknown global option --nope"),
    ("--sequential=false lint", "--sequential is a flag and takes no value"),
    ("--json=0 lint", "--json is a flag and takes no value"),
    ("lint --mode -- x", "lint: --mode expects a value, attached: --mode=VALUE"),
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
