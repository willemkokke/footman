"""The chain grammar: separator-free splitting, arity, validation, errors."""

from __future__ import annotations

import pytest

from footman.split import ChainError, split_chain


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
    result = segs(tree, "format --fix lint --fix --mode strict typecheck test")
    assert [s.task for s in result] == ["format", "lint", "typecheck", "test"]
    assert result[0].values == {"fix": True}
    assert result[1].values == {"fix": True, "mode": "strict"}
    assert result[2].values == {}
    assert result[3].values == {}


def test_repeated_option_collects_a_list(tree):
    (seg,) = segs(
        tree, "test --marker slow --path tests/unit --path tests/e2e --coverage"
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


def test_group_descent_and_typed_option(tree):
    (seg,) = segs(tree, "docs serve --port 8001")
    assert seg.task == "docs.serve"
    assert seg.path == ["docs", "serve"]
    assert seg.values == {"port": "8001"}


def test_required_positional_then_option(tree):
    a, b = segs(tree, "docs build --strict deploy staging --version 2026.07.16")
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
    a, b = segs(tree, "deps add requests rich typer + lint --fix")
    assert a.task == "deps.add"
    assert a.variadic == ["requests", "rich", "typer"]
    assert b.task == "lint"
    assert b.values == {"fix": True}


def test_variadic_consumes_rest_of_segment(tree):
    (seg,) = segs(tree, "run ruff check src")
    assert seg.variadic == ["ruff", "check", "src"]


def test_passthrough_is_terminal(tree):
    (seg,) = segs(tree, "test --marker unit -- -k manifest_or_split -x")
    assert seg.values == {"marker": "unit"}
    assert seg.passthrough == ["-k", "manifest_or_split", "-x"]


def test_no_flag_negation(tree):
    (seg,) = segs(tree, "docs serve --no-live")
    assert seg.values == {"live": False}


def test_option_equals_form(tree):
    (seg,) = segs(tree, "lint --mode=strict")
    assert seg.values == {"mode": "strict"}


def test_where_global_takes_a_value(tree):
    assert globs(tree, "--where docker.build") == ["--where", "docker.build"]
    assert segs(tree, "--where docker.build") == []


ERROR_CASES = [
    ("lint --mode fast", "lint: --mode must be one of strict|loose (got 'fast')"),
    ("docs serve --port http", "docs.serve: --port expects an integer (got 'http')"),
    ("bench --timeout fast", "bench: --timeout expects a number (got 'fast')"),
    ("version huge", "version: <part> must be one of major|minor|patch (got 'huge')"),
    (
        "deploy check",
        "deploy: <env> must be one of dev|staging|prod — 'check' looks like "
        "the next task; did you forget <env>?",
    ),
    ("lint test --fix", "test: unknown option --fix"),
    ("docs deplo", "docs: expected a task name, got 'deplo'"),
    ("--json lint --quiet", "lint: unknown option --quiet"),
    ("render only-one", "render: missing required argument(s): <output>"),
    ("--nope check", "unknown global option --nope"),
    ("--sequential=false lint", "--sequential is a flag and takes no value"),
    ("--json=0 lint", "--json is a flag and takes no value"),
    ("lint --mode -- x", "--mode expects a value, but found '--'"),
]


@pytest.mark.parametrize("line, message", ERROR_CASES)
def test_teaching_errors(tree, line, message):
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, line.split())
    assert message in str(excinfo.value)
