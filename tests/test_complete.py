"""The completion hot path: group descent, options, and choice values."""

from __future__ import annotations

from footman._complete import complete


def test_top_level_prefix(tree):
    assert complete(tree, ["che"]) == ["check"]


def test_empty_partial_lists_everything(tree):
    out = complete(tree, [""])
    assert "check" in out
    assert "docs" in out
    assert "workspace" in out


def test_group_descent(tree):
    assert set(complete(tree, ["docs", ""])) == {"serve", "build"}
    assert complete(tree, ["docs", "ser"]) == ["serve"]


def test_task_options(tree):
    out = complete(tree, ["lint", ""])
    assert set(out) == {"--fix", "--mode", "--paths"}


def test_option_value_choices(tree):
    assert set(complete(tree, ["lint", "--mode", ""])) == {"strict", "loose"}
    assert complete(tree, ["lint", "--mode", "st"]) == ["strict"]


def test_nested_option_value_choices(tree):
    out = complete(tree, ["workspace", "mount", "--share", ""])
    assert set(out) == {"main", "scratch", "archive"}


def test_positional_choices_offered_alongside_options(tree):
    out = complete(tree, ["deploy", ""])
    assert "--version" in out
    assert {"dev", "staging", "prod"} <= set(out)


def test_required_choice_positional(tree):
    assert set(complete(tree, ["version", ""])) == {"major", "minor", "patch"}


def test_unknown_prefix_completes_to_nothing(tree):
    assert complete(tree, ["zzz"]) == []
