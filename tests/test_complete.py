"""The completion hot path: group descent, options, and choice values."""

from __future__ import annotations

from typing import Annotated

from footman import manifest, registry, task
from footman._complete import complete
from footman.params import doc


def _names(result):
    """Candidate names, dropping any `\t`-separated description column (11.2)."""
    return [c.split("\t", 1)[0] for c in result]


def test_top_level_prefix(tree):
    assert _names(complete(tree, ["che"])) == ["check"]


def test_task_names_carry_descriptions(tree):
    # 11.2: a task/group name candidate emits `name\tsummary`; the shell hooks
    # split on the tab to render a description column.
    assert complete(tree, ["che"]) == [
        "check\tRun every check (format, lint, typecheck, test)."
    ]


def test_options_and_choices_have_no_description(tree):
    # Undocumented options and choice values pass through bare (no tab).
    assert complete(tree, ["lint", "--f"]) == ["--fix"]
    assert "\t" not in "".join(complete(tree, ["lint", "--mode", ""]))


def test_doc_marker_becomes_option_description():
    # An option with a doc("...") marker completes with a description column,
    # exactly like task names do.
    with registry.capture() as root:

        @task
        def lint(fix: Annotated[bool, doc("apply fixes in place")] = False): ...

    built = manifest.build_manifest(root)["tree"]
    assert complete(built, ["lint", "--f"]) == ["--fix\tapply fixes in place"]


def test_docstring_doc_becomes_option_description():
    # No marker needed: a documented docstring parameter reaches the column.
    with registry.capture() as root:

        @task
        def sync(force: bool = False):
            """Sync.

            Args:
                force: skip the freshness check
            """

    built = manifest.build_manifest(root)["tree"]
    assert complete(built, ["sync", "--f"]) == ["--force\tskip the freshness check"]


def test_empty_partial_lists_everything(tree):
    out = _names(complete(tree, [""]))
    assert "check" in out
    assert "docs" in out
    assert "workspace" in out


def test_group_descent(tree):
    assert set(_names(complete(tree, ["docs", ""]))) == {"serve", "build"}
    assert _names(complete(tree, ["docs", "ser"])) == ["serve"]


def test_task_options(tree):
    out = _names(complete(tree, ["lint", ""]))
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


def test_attached_opt_value_zsh_fish(tree):
    # F49: shells that don't split on `=` pass one word `--mode=st`; complete it
    # to full `--mode=…` tokens.
    assert complete(tree, ["lint", "--mode=st"]) == ["--mode=strict"]
    assert set(complete(tree, ["lint", "--mode="])) == {"--mode=strict", "--mode=loose"}


def test_split_opt_value_bash(tree):
    # F49: bash splits `--mode=st` into words `--mode`, `=`, `st`. The `=` must
    # not disarm the pending value, and a leading `=` partial is stripped.
    assert complete(tree, ["lint", "--mode", "=", "st"]) == ["strict"]
    assert set(complete(tree, ["lint", "--mode", "="])) == {"strict", "loose"}
    assert set(complete(tree, ["lint", "--mode", "=", ""])) == {"strict", "loose"}


def test_leading_global_value_not_read_as_task(tree):
    # F61: `-C docs <TAB>` — `docs` is -C's value, so completion offers the
    # top-level names, not the `docs` group's tasks.
    top = set(complete(tree, [""]))
    assert set(complete(tree, ["-C", "docs", ""])) == top
    assert set(complete(tree, ["-C", "anydir", ""])) == top


def test_install_completion_completes_shells(tree):
    # F61: --install-completion's optional value is one of the shells.
    assert set(complete(tree, ["--install-completion", ""])) == {
        "bash",
        "zsh",
        "fish",
        "pwsh",
        "nushell",
    }
    assert complete(tree, ["--install-completion", "z"]) == ["zsh"]


def test_setup_completion_completes_shells(tree):
    # --setup-completion mirrors --install-completion: its value is a shell.
    assert set(complete(tree, ["--setup-completion", ""])) == {
        "bash",
        "zsh",
        "fish",
        "pwsh",
        "nushell",
    }
    assert complete(tree, ["--setup-completion", "fi"]) == ["fish"]


def test_leading_flag_global_then_task(tree):
    # A leading flag global (-s) is consumed; the walk still completes tasks.
    assert "check" in _names(complete(tree, ["-s", "che"]))


def test_root_flag_partial_offers_globals(tree):
    # A flag-shaped partial at the root offers fm's own globals.
    dd = complete(tree, ["--"])
    assert {"--help", "--list", "--install-completion", "--config"} <= set(dd)
    assert complete(tree, ["--inst"]) == ["--install-completion"]
    # A single dash reaches the short aliases too.
    assert {"-C", "-h", "-s"} <= set(complete(tree, ["-"]))


def test_root_globals_offered_after_a_leading_global(tree):
    # `fm -s --<TAB>` — -s is consumed, more globals are still on offer.
    assert "--json" in complete(tree, ["-s", "--"])


def test_bare_tab_omits_globals(tree):
    # An empty partial lists tasks only — globals there would be noise.
    out = _names(complete(tree, [""]))
    assert "check" in out
    assert not any(c.startswith("-") for c in out)


def test_globals_not_offered_past_a_group_or_task(tree):
    # Globals bind before the first task; a flag partial inside a group or after
    # a task is not a global position.
    assert "--help" not in complete(tree, ["docs", "--"])
    assert "--help" not in complete(tree, ["lint", "--"])


def test_completion_globals_mirror_split():
    # Drift pin: the hot-path arity mirror must match split.GLOBALS exactly, so
    # renaming or re-typing a global fails CI instead of silently misparsing.
    from footman import _complete, _shellcomp, split

    flag: set[str] = set()
    value: set[str] = set()
    maybe: set[str] = set()
    buckets = {"flag": flag, "option": value, "option?": maybe}
    for name, alias, kind, _hint, _help in split.GLOBALS:
        buckets[kind] |= {name} | ({alias} if alias else set())
    assert flag == _complete._GLOBAL_FLAG
    assert value == _complete._GLOBAL_VALUE
    assert maybe == _complete._GLOBAL_MAYBE
    assert _complete._GLOBAL_CHOICES["--install-completion"] == tuple(_shellcomp.SHELLS)
    assert _complete._GLOBAL_CHOICES["--setup-completion"] == tuple(_shellcomp.SHELLS)


# --- chain-aware completion -----------------------------------------------------


def test_second_segment_options_are_the_second_tasks(tree):
    # `check` has no --mode; the --mo must complete against lint's options.
    out = complete(tree, ["check", "lint", "--mo"])
    assert out == ["--mode"]


def test_next_task_name_completes_after_a_chain(tree):
    assert _names(complete(tree, ["lint", "--fix", "che"])) == ["check"]


def test_option_value_not_confused_with_next_task(tree):
    # "--mode" wants a value: its choices complete, not task names.
    out = complete(tree, ["lint", "--mode", ""])
    assert set(out) == {"strict", "loose"}


def test_group_descent_in_a_later_segment(tree):
    out = _names(complete(tree, ["lint", "--fix", "docs", ""]))
    assert set(out) == {"serve", "build"}


def test_plus_resets_the_segment(tree):
    out = _names(complete(tree, ["lint", "+", ""]))
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
