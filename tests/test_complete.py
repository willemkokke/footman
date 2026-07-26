"""The completion hot path: group descent, options, and choice values."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest

from footman import _complete, manifest, registry, task
from footman._complete import _tasks_file_from, complete, complete_cli
from footman.params import doc, suggest


@pytest.fixture(autouse=True)
def _generous_cold_budget(monkeypatch):
    """The product's cold-TAB budget is tight on purpose (a keystroke must
    never hang); the *test* budget is generous, because a loaded CI runner —
    Windows especially, free-threaded builds worst — can take many seconds
    to spawn and import the detached builder. Every cold-path test in this
    file asserts builds-and-serves, never how fast the runner's disk is;
    the flake kept hopping between whichever cold test ran on the slow box.
    The dynamic budget gets the same treatment: the fresh completer spawns a
    framework-importing subprocess, and on timeout serves *nothing* on
    purpose — so a slow spawn reads as an empty candidate list.
    """
    monkeypatch.setattr(_complete, "_COLD_TIMEOUT", 30.0)
    monkeypatch.setattr(_complete, "_DYNAMIC_TIMEOUT", 30.0)


def _cold_evidence(cache_dir) -> str:
    """What the cold build left behind, for a failure message.

    These tests fail on loaded CI runners and the assertion alone can't say
    why: an empty candidate list means the detached builder never landed,
    but not whether it was slow, dead, or blocked. The cache directory
    answers that — a manifest present means "landed too late", a stray
    `.pid.tmp` means the write couldn't replace its destination, and an
    empty directory means the child never got that far.
    """
    from pathlib import Path

    root = Path(cache_dir)
    if not root.exists():
        return f"cache dir {root} does not exist"
    files = sorted(f"{p.name} ({p.stat().st_size}b)" for p in root.rglob("*"))
    return f"cache dir {root} holds: {files or 'nothing'}"


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
    # Path-style: candidates sit one segment beyond the typed prefix, and a
    # namespace group carries its trailing dot, `ls -F` style.
    out = _names(complete(tree, [""]))
    assert "check" in out
    assert "docs." in out
    assert "workspace." in out


def test_dotted_descent(tree):
    assert set(_names(complete(tree, ["docs."]))) == {"docs.serve", "docs.build"}
    assert _names(complete(tree, ["docs.ser"])) == ["docs.serve"]


def test_unique_namespace_match_skips_ahead(tree):
    # `rel<TAB>` matches only the `release` group: completion descends
    # straight through it, so no shell ever forces a space after `release.`.
    out = _names(complete(tree, ["rel"]))
    assert set(out) == {"release.prepare", "release.publish"}


def test_a_bare_group_word_is_a_dead_end(tree):
    # `fm docs <TAB>` — the space form has no valid continuation; silence
    # mirrors the splitter's refusal.
    assert complete(tree, ["docs", ""]) == []


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
    out = complete(tree, ["workspace.mount", "--share", ""])
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


# --- -f/--tasks-file completion (keyed by cwd + file) -------------------------


def test_tasks_file_from_leading_globals():
    assert _tasks_file_from(["-f", "x.py", ""]) == "x.py"
    assert _tasks_file_from(["--tasks-file", "x.py", "build"]) == "x.py"
    assert _tasks_file_from(["--tasks-file=x.py"]) == "x.py"
    assert _tasks_file_from(["-C", "sub", "-f", "x.py"]) == "x.py"  # skip -C + value
    assert _tasks_file_from(["-k", "-f", "x.py"]) == "x.py"  # skip a flag
    assert _tasks_file_from(["build", "-f", "x.py"]) is None  # after a task: not global
    assert _tasks_file_from(["build"]) is None
    assert _tasks_file_from([""]) is None


def test_source_manifest_path_keys_by_cwd_and_file():
    from pathlib import Path

    from footman import _paths

    cwd = Path("/proj/a")
    a = _paths.source_manifest_path(cwd, Path("x.py"))
    assert a == _paths.source_manifest_path(cwd, Path("x.py"))  # stable
    assert a != _paths.source_manifest_path(cwd, Path("y.py"))  # the file matters
    assert a != _paths.source_manifest_path(
        Path("/proj/b"), Path("x.py")
    )  # cwd matters
    assert a != _paths.manifest_path(cwd)  # distinct from the plain-cwd cache


def test_f_completion_reads_the_source_key(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    tf = proj / "custom.py"

    g = registry.Group("root")

    @g.task
    def alpha(): ...

    @g.task
    def beta(): ...

    # Cache the manifest under the (cwd, file) key, exactly as a `-f` run does.
    manifest.sync_manifest(
        g,
        Path.cwd(),
        completion_max_age=0,
        tasks_file=str(tf),
        path=_paths.source_manifest_path(Path.cwd(), tf),
    )
    complete_cli(["--", "-f", str(tf), ""])
    out = capsys.readouterr().out.split()
    assert "alpha" in out and "beta" in out


def test_f_partial_value_defers_to_file_completion(tmp_path, monkeypatch, capsys):
    from footman._complete import _EXIT_FILES

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    g = registry.Group("root")

    @g.task
    def alpha(): ...

    # `-f cust` here is a *partial* being typed, not a finished override, so
    # completion must land on the cwd tree and signal files (exit 100) rather
    # than hunt for a "(cwd, 'cust')" manifest that never existed.
    manifest.sync_manifest(g, Path.cwd(), completion_max_age=0)
    assert complete_cli(["--", "-f", "cust"]) == _EXIT_FILES
    assert capsys.readouterr().out == ""


# --- file-path completion for Path values ------------------------------------


def test_path_value_globals_signal_file_completion(tree):
    from footman._complete import _FILES, _GLOBAL_FILES, _GLOBAL_VALUE

    assert _GLOBAL_FILES <= _GLOBAL_VALUE  # every file-global consumes its value
    assert complete(tree, ["-f", ""]) == [_FILES]
    assert complete(tree, ["--config", ""]) == [_FILES]
    assert complete(tree, ["-C", ""]) == [_FILES]
    assert complete(tree, ["--where", ""]) != [_FILES]  # --where takes a task


def test_complete_cli_exits_files_for_a_path_value(tmp_path, capsys):
    from footman._complete import _EXIT_FILES

    m = tmp_path / "m.json"
    m.write_text('{"schema": 1, "tree": {"tasks": {}, "groups": {}}}')
    rc = complete_cli(["--manifest", str(m), "--", "-f", ""])
    assert rc == _EXIT_FILES
    assert capsys.readouterr().out == ""


def test_path_typed_option_value_signals_file_completion():
    from footman._complete import _FILES

    with registry.capture() as root:

        @task
        def fetch(out: Path = Path(".")):
            "Fetch."

    built = manifest.build_manifest(root)["tree"]
    assert complete(built, ["fetch", "--out", ""]) == [_FILES]
    # a plain str option value has no such signal — it stays empty, so the
    # shell never bluntly offers files where a name was wanted.
    with registry.capture() as root2:

        @task
        def greet(name: str = "world"):
            "Greet."

    built2 = manifest.build_manifest(root2)["tree"]
    assert complete(built2, ["greet", "--name", ""]) == []


def test_path_positional_signals_file_completion():
    from footman._complete import _FILES

    with registry.capture() as root:

        @task
        def deploy(target: Path, *extra: Path):
            "Deploy."

    built = manifest.build_manifest(root)["tree"]
    assert complete(built, ["deploy", ""]) == [_FILES]  # the Path positional
    assert complete(built, ["deploy", "a", ""]) == [_FILES]  # the Path variadic
    assert complete(built, ["deploy", "-"]) != [_FILES]  # a dash reaches options

    # a plain str positional is not a file
    with registry.capture() as root2:

        @task
        def greet(name: str):
            "Greet."

    built2 = manifest.build_manifest(root2)["tree"]
    assert complete(built2, ["greet", ""]) != [_FILES]


# --- dynamic completers: recomputed fresh, never the baked snapshot -----------


def _demo_suggest():
    return ["alpha", "beta"]


def test_dynamic_option_signals_recompute():
    from footman._complete import _DYNAMIC

    with registry.capture() as root:

        @task
        def deploy(target: Annotated[str, suggest(_demo_suggest)] = ""):
            "Deploy."

    built = manifest.build_manifest(root)["tree"]
    # the value is dynamic → defer to a fresh recompute, carrying the partial,
    # the param name, and the task path
    assert complete(built, ["deploy", "--target", ""]) == [
        _DYNAMIC,
        "",
        "target",
        "deploy",
    ]


def _dynamic_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from pathlib import Path\n"
        "from typing import Annotated\n"
        "from footman import task\n"
        "from footman.params import suggest\n\n"
        "def _targets():\n"
        "    return Path('targets.txt').read_text().split()\n\n"
        "@task\n"
        "def deploy(target: Annotated[str, suggest(_targets)] = ''):\n"
        "    'Deploy.'\n"
    )
    return proj


def test_suggest_values_runs_the_completer_fresh(tmp_path, monkeypatch):
    from footman import _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("gamma\ndelta\n")
    assert _suggest._values("target", ["deploy"], {}) == ["gamma", "delta"]
    # a miss — unknown param or task — is empty, never an error
    assert _suggest._values("nope", ["deploy"], {}) == []
    assert _suggest._values("target", ["ghost"], {}) == []


def test_suggest_main_swallows_a_failing_completer(tmp_path, monkeypatch, capsys):
    from footman import _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    # no targets.txt → the completer raises → no candidates, exit 0
    assert _suggest.main(["--param", "target", "--path", "deploy"]) == 0
    assert capsys.readouterr().out == ""


def test_dynamic_completion_is_fresh_not_baked(tmp_path, monkeypatch, capsys):
    from footman import _app

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)

    (proj / "targets.txt").write_text("alpha\nbeta\n")
    assert _app.run(["--list"]) == 0  # bake the manifest with alpha/beta
    capsys.readouterr()

    (proj / "targets.txt").write_text("gamma\ndelta\n")  # the world moved on
    complete_cli(["--", "deploy", "--target", ""])
    assert capsys.readouterr().out.split() == ["gamma", "delta"]  # fresh, not baked


def test_fresh_dynamic_passes_context_and_falls_back(monkeypatch):
    import subprocess

    from footman import _complete

    captured: dict[str, list[str]] = {}

    def ok(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "gamma\ndelta\n", "")

    monkeypatch.setattr(_complete.subprocess, "run", ok)
    args = ["-f", "x.py", "--config", "c.toml", "deploy", "--target", ""]
    assert _complete._fresh_dynamic("target", ["deploy"], args) == ["gamma", "delta"]
    cmd = captured["cmd"]  # the subprocess carries the target and the context
    assert cmd[cmd.index("--param") + 1] == "target"
    assert cmd[cmd.index("--path") + 1] == "deploy"
    assert cmd[cmd.index("--tasks-file") + 1] == "x.py"
    assert cmd[cmd.index("--config") + 1] == "c.toml"

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=2)

    monkeypatch.setattr(_complete.subprocess, "run", timeout)
    assert _complete._fresh_dynamic("target", ["deploy"], ["a", ""]) is None

    def nonzero(*a, **k):
        return subprocess.CompletedProcess("x", 1, "", "")

    monkeypatch.setattr(_complete.subprocess, "run", nonzero)
    assert _complete._fresh_dynamic("target", ["deploy"], ["a", ""]) is None


# --- cold cache: build once, don't answer empty ------------------------------


def test_cold_cache_builds_and_serves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from footman import task\n\n@task\ndef lint(): ...\n@task\ndef check(): ...\n"
    )
    monkeypatch.chdir(proj)
    # nothing cached: the first completion builds the manifest and serves it,
    # rather than answering empty until the first real run
    complete_cli(["--", ""])
    out = capsys.readouterr().out.split()
    assert "lint" in out and "check" in out, _cold_evidence(tmp_path / "cache")


def test_cold_f_cache_builds_and_serves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "other.py").write_text(
        "from footman import task\n\n@task\ndef ship(): ...\n"
    )
    monkeypatch.chdir(proj)
    # a finished `-f <file>` with a cold cache builds that file's (cwd, file)
    # manifest and serves it, the same as a plain cold TAB — not empty
    complete_cli(["--", "-f", "other.py", ""])
    out = capsys.readouterr().out.split()
    assert "ship" in out, _cold_evidence(tmp_path / "cache")


def test_cold_build_times_out_to_none(tmp_path, monkeypatch):
    from footman import _complete

    # accept the override arg; still no build ever lands
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda override=None: None)
    monkeypatch.setattr(_complete, "_COLD_TIMEOUT", 0.1)
    assert _complete._cold_build(str(tmp_path / "never.json"), None) is None


def test_cold_build_skips_missing_f_file(tmp_path, monkeypatch):
    from footman import _complete

    # a still-being-typed or missing -f value has no file to build: return None
    # at once, never spawning a builder that would only stall for the timeout
    spawned: list[str | None] = []

    def _spawn(override: str | None = None) -> None:
        spawned.append(override)

    monkeypatch.setattr(_complete, "_spawn_refresh", _spawn)
    got = _complete._cold_build(str(tmp_path / "m.json"), str(tmp_path / "missing.py"))
    assert got is None and spawned == []


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


def test_dotted_descent_in_a_later_segment(tree):
    out = _names(complete(tree, ["lint", "--fix", "docs."]))
    assert set(out) == {"docs.serve", "docs.build"}


def test_plus_resets_the_segment(tree):
    out = _names(complete(tree, ["lint", "+", ""]))
    assert "check" in out and "docs." in out


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


def test_completion_output_is_lf_only(tree, tmp_path, monkeypatch, capsysbinary):
    """The completion protocol is LF, on every platform.

    Windows text-mode stdout would translate to CRLF, and a shell that
    reads lines literally (git-bash's `read`) keeps the carriage return
    and completes `--fix\r` — a stray CR at the user's cursor. Found by
    driving the real git-bash on a Windows runner, pinned here so no
    platform can reintroduce it.
    """
    import json

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"schema": 1, "tree": tree}), encoding="utf-8")
    complete_cli(["--manifest", str(manifest), "--", ""])
    out = capsysbinary.readouterr().out
    assert out, "the fixture tree should complete to something"
    assert b"\r" not in out
    assert out.endswith(b"\n")


def test_segment_wise_abbreviation_expands_uniquely(tree):
    # The other half of the `cd` idiom: each typed segment prefix-matches its
    # tree level, and a whole word that resolves uniquely emits the one
    # expanded candidate — `fm w.m⇥` → `workspace.mount`.
    assert _names(complete(tree, ["w.m"])) == ["workspace.mount"]
    assert _names(complete(tree, ["w.mo"])) == ["workspace.mount"]


def test_abbreviation_is_completion_only(tree):
    # The runtime resolver stays strict, so an abbreviation that runs today
    # cannot change meaning when a new task lands.
    import pytest as _pytest

    from footman.split import ChainError, split_chain

    with _pytest.raises(ChainError):
        split_chain(tree, ["w.mount"])


def test_ambiguous_segment_expands_up_to_it_and_lists_matches(tree):
    # `d.` could open db, deps, dns, docker, or docs: expand up to the
    # ambiguous level and list its matches, dot-marked.
    out = set(_names(complete(tree, ["d.serve"])))
    assert out == {"db.", "deps.", "dns.", "docker.", "docs."}


def test_exact_segment_name_beats_abbreviation(tree):
    # `docs.` names a group exactly; deps/dns/… sharing the `d` prefix must
    # not turn it ambiguous.
    assert set(_names(complete(tree, ["docs."]))) == {"docs.serve", "docs.build"}


def test_leaf_name_fallback_rescues_a_known_task_name(tree):
    # Zero top-level matches: complete against last segments over the flat
    # index — the "I know the task, not where it lives" rescue.
    assert _names(complete(tree, ["serve"])) == ["docs.serve"]
    assert set(_names(complete(tree, ["mig"]))) == {"db.migrate"}


def test_leaf_fallback_never_fires_on_a_valid_descent(tree):
    # `bu` matches the top-level `build` task, so the fallback must not add
    # docs.build alongside it.
    assert _names(complete(tree, ["bu"])) == ["build"]


def test_completion_schema_mirrors_manifest():
    # Drift pin: the hot path's schema literal must match the manifest's, so
    # bumping SCHEMA_VERSION without teaching the completer fails CI.
    from footman import _complete as hot
    from footman import manifest as cold

    assert hot._SCHEMA == cold.SCHEMA_VERSION


def test_complete_cli_rejects_a_mismatched_schema(tree, tmp_path, capsys):
    # A cache baked by a different footman is never walked: the --manifest
    # path (no rebuild machinery) stays silent instead of tracebacking.
    import json as _json

    path = tmp_path / "m.json"
    path.write_text(_json.dumps({"schema": 999, "tree": tree}))
    assert complete_cli(["--manifest", str(path), "--", "che"]) == 0
    assert capsys.readouterr().out == ""


def test_stale_schema_cache_rebuilds_instead_of_walking(tmp_path, monkeypatch, capsys):
    # The transition TAB: a cwd cache written by a pre-upgrade footman (wrong
    # schema) routes into the cold build, so the first post-upgrade TAB serves
    # correct candidates instead of tracebacking on a reshaped tree.
    import json as _json

    from footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from footman import task\n\n@task\ndef lint(): ...\n"
    )
    monkeypatch.chdir(proj)
    stale = _paths.cwd_manifest_path()
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(_json.dumps({"schema": 0, "tree": {"bogus": True}}))
    complete_cli(["--", "li"])
    out = capsys.readouterr().out.split()
    assert "lint" in out, _cold_evidence(tmp_path / "cache")
