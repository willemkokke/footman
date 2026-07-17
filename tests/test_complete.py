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
    assert {"--fix", "--mode", "--paths"} <= set(out)
    assert "check" in out  # separator-free chains: the next task completes too
    assert complete(tree, ["lint", "--"]) != []  # option-shaped partial: options only
    assert all(c.startswith("--") for c in complete(tree, ["lint", "--"]))


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


# --- chain-aware completion -----------------------------------------------------


def test_second_segment_options_are_the_second_tasks(tree):
    # `check` has no --mode; the --mo must complete against lint's options.
    out = complete(tree, ["check", "lint", "--mo"])
    assert out == ["--mode"]


def test_next_task_name_completes_after_a_chain(tree):
    assert "check" in complete(tree, ["lint", "--fix", "che"])
    assert complete(tree, ["lint", "--fix", "che"]) == ["check"]


def test_option_value_not_confused_with_next_task(tree):
    # "--mode" wants a value: its choices complete, not task names.
    out = complete(tree, ["lint", "--mode", ""])
    assert set(out) == {"strict", "loose"}


def test_group_descent_in_a_later_segment(tree):
    out = complete(tree, ["lint", "--fix", "docs", ""])
    assert set(out) == {"serve", "build"}


def test_plus_resets_the_segment(tree):
    out = complete(tree, ["lint", "+", ""])
    assert "check" in out and "docs" in out


def test_nothing_after_passthrough(tree):
    assert complete(tree, ["check", "--", "anything", ""]) == []


def test_given_options_are_not_reoffered(tree):
    # `fm lint --fix <TAB>` must not suggest --fix again — a flag binds once.
    out = complete(tree, ["lint", "--fix", ""])
    assert "--fix" not in out
    assert "--mode" in out  # the unused ones remain
    assert complete(tree, ["lint", "--fix", "--f"]) == []


def test_negated_flag_counts_as_used(tree):
    out = complete(tree, ["lint", "--no-fix", ""])
    assert "--fix" not in out


def test_repeatable_options_stay_offered(tree):
    # --paths is list-valued: repeating it is the grammar, keep offering it.
    out = complete(tree, ["lint", "--paths", "a", ""])
    assert "--paths" in out


def test_used_options_reset_per_segment(tree):
    # --fix bound to the first lint segment; a second task starts fresh.
    out = complete(tree, ["lint", "--fix", "check", "lint", ""])
    assert "--fix" in out
