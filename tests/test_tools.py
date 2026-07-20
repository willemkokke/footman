"""The tools bridge: mechanical flag translation, subcommands, versions."""

from __future__ import annotations

import sys

import pytest

from footman import tools
from footman.testing import recording


def _one(call) -> str:
    with recording() as steps:
        call()
    assert len(steps) == 1
    return steps[0].command


def test_mechanical_flag_translation():
    cmd = _one(
        lambda: tools.ruff.check(
            "src", "tests", fix=True, select=["E", "F"], output_format="github"
        )
    )
    assert cmd == (
        "ruff check src tests --fix --select E --select F --output-format github"
    )


def test_off_sentinel_emits_the_negation():
    from footman.tools import off

    # `off` disables a default-on flag; equivalent to naming it directly.
    assert _one(lambda: tools.zensical.build(clean=True, strict=off)) == (
        "zensical build --clean --no-strict"
    )
    assert _one(lambda: tools.mkdocs.build(no_strict=True)) == (
        "mkdocs build --no-strict"
    )


def test_off_can_be_variable_driven():
    from footman.tools import off

    def render(directory_urls: bool):
        return _one(lambda: tools.mkdocs.build(directory_urls=directory_urls or off))

    assert render(True) == "mkdocs build --directory-urls"
    assert render(False) == "mkdocs build --no-directory-urls"


def test_false_none_and_empty_collections_are_omitted():
    # Empty lists/tuples vanish like False/None — so a task parameter's
    # default (`select: list[str] = ()`) passes straight through with no
    # `or None` ceremony at the call site.
    cmd = _one(
        lambda: tools.ruff.check("src", fix=False, config=None, select=[], ignore=())
    )
    assert cmd == "ruff check src"


def test_single_letter_kwargs_are_short_flags():
    cmd = _one(lambda: tools.pytest_bin("-q", k="markers"))
    assert cmd == "pytest-bin -q -k markers"


def test_trailing_underscore_escapes_keywords():
    assert _one(lambda: tools.bun.add("left-pad", global_=True)) == (
        "bun add left-pad --global"
    )


def test_subcommands_chain():
    assert _one(lambda: tools.docker.compose.up(detach=True)) == (
        "docker compose up --detach"
    )


def test_any_executable_is_a_tool():
    # No declaration needed — the module fallback bridges anything on PATH.
    assert _one(lambda: tools.terraform("plan", out="tf.plan")) == (
        "terraform plan --out tf.plan"
    )


def test_shadowing_names_resolve_to_tools_not_imports():
    # F50/F53: `run`, `sys`, `re`, … used to be public module imports, so
    # `tools.run`/`tools.sys` returned the imported object (typechecking as a
    # Tool per the stub, crashing at runtime). Privatized, they now bridge to
    # Tools like any other name.
    for name in ("run", "sys", "re", "subprocess"):
        got = getattr(tools, name)
        assert isinstance(got, tools.Tool) and got._argv0 == name


def test_tools_stub_declares_every_runtime_binding():
    # Freeze the stub: every module-level runtime binding in tools.py must be
    # declared in tools.pyi, so a privatized import can never silently reappear
    # as a public attribute and stop being a Tool.
    import ast
    from pathlib import Path

    def bindings(source: str) -> set[str]:
        names: set[str] = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.Import):
                names |= {a.asname or a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names |= {a.asname or a.name for a in node.names}
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    src = Path(tools.__file__)
    runtime = bindings(src.read_text())
    declared = bindings(src.with_suffix(".pyi").read_text())
    missing = runtime - declared - {"annotations"}  # __future__ import allowlisted
    assert not missing, f"tools.pyi is missing runtime bindings: {sorted(missing)}"


def test_curated_names_map_to_real_executables():
    assert _one(lambda: tools.markdownlint("docs/index.md")) == (
        "markdownlint-cli2 docs/index.md"
    )
    assert _one(lambda: tools.ruff_format("src", check=True)) == (
        "ruff format src --check"
    )


def test_installed_version_is_cached_and_comparable():
    tools._version_cache.clear()
    version = tools.ruff.installed_version()
    assert version >= (0, 1)
    assert tools._version_cache["ruff"] == version  # second read hits the cache
    assert tools.ruff.installed_version() is not None


def test_installed_version_unreadable_is_taught():
    with pytest.raises((ValueError, FileNotFoundError)):
        tools.Tool("no-such-binary-really").installed_version()


# --- in-process execution ---------------------------------------------------


class _FakeEP:
    """A stand-in console_scripts EntryPoint: `.load()` returns the target
    (and records that the import happened)."""

    def __init__(self, target, loaded: list | None = None) -> None:
        self._target = target
        self._loaded = loaded

    def load(self):
        if self._loaded is not None:
            self._loaded.append(True)
        return self._target


def test_dry_run_does_not_import_the_tool(monkeypatch):
    # The property duty had: a call you don't execute costs no tool import.
    # Under recording (dry-run), the entry point is resolved (metadata) but
    # never loaded — so the tool's module is never imported.
    loaded: list[bool] = []

    def target(argv=None):
        print("ran")
        return 0

    monkeypatch.setattr(
        tools, "_console_entrypoint", lambda name: _FakeEP(target, loaded)
    )
    with recording() as steps:
        tools.Tool("heavy", in_process=True)("build")
    assert loaded == []  # dry-run imported nothing
    assert steps[0].command == "heavy build"

    tools.Tool("heavy", in_process=True)("build")  # a real run does load it
    assert loaded == [True]


def test_in_process_never_spawns(monkeypatch):
    # coverage ships a console_scripts entry and is installed (pytest-cov);
    # if the subprocess layer is touched, this fails loudly.
    from footman import context

    def boom(*a, **k):
        raise AssertionError("subprocess used for an in-process tool")

    monkeypatch.setattr(context, "_run_subprocess", boom)
    saved_argv = list(sys.argv)
    assert tools.coverage("--version", nofail=True) == 0
    assert sys.argv == saved_argv  # patched argv is always restored


def test_in_process_demand_without_entry_is_taught():
    with pytest.raises(ValueError, match="no installed console_scripts entry"):
        tools.Tool("no-such-python-tool")("--version", in_process=True)


def test_in_process_preference_falls_back_to_subprocess():
    # git has no console_scripts entry; a preference (not a demand) must
    # degrade to the normal spawn.
    with recording() as steps:
        tools.Tool("git", in_process=True)("status", s=True)
    assert steps[0].command == "git status -s"


def test_in_process_tools_run_concurrently_with_separate_capture(monkeypatch):
    """Two argument-accepting in-process tools must overlap (the barrier
    times out if they serialise) and must not cross-contaminate captures."""
    import threading

    from footman import manifest, schedule
    from footman.registry import Group
    from footman.split import split_chain

    barrier = threading.Barrier(2, timeout=5)

    def make_entry(marker):
        def entry(argv=None):  # accepts args -> direct, lock-free path
            barrier.wait()
            print(f"{marker}-OUT")
            return 0

        return entry

    entries = {"fake-a": make_entry("A"), "fake-b": make_entry("B")}
    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entries[name]) if name in entries else None,
    )

    reg = Group("root")

    @reg.task
    def a():
        tools.Tool("fake-a", in_process=True)()

    @reg.task
    def b():
        tools.Tool("fake-b", in_process=True)()

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["a", "b"])
    results = {r.task: r for r in schedule.run_plan(reg, segments)}
    assert results["a"].ok and results["b"].ok
    assert "A-OUT" in results["a"].steps[0].output
    assert "B-OUT" not in results["a"].steps[0].output  # no cross-talk
    assert "B-OUT" in results["b"].steps[0].output


def test_in_process_tool_runs_from_context_cwd(monkeypatch, tmp_path):
    # F17: an in-process tool honors the task's context cwd, exactly as the
    # subprocess branch of the same call already does.
    import os

    from footman import manifest, schedule
    from footman.registry import Group
    from footman.split import split_chain

    seen = {}

    def entry(argv=None):
        seen["cwd"] = os.getcwd()
        return 0

    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entry) if name == "cwd-tool" else None,
    )

    reg = Group("root")

    @reg.task
    def go():
        tools.Tool("cwd-tool", in_process=True)()

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["go"])
    schedule.run_plan(reg, segments, ctx_config={"cwd": tmp_path})
    assert seen["cwd"] == str(tmp_path.resolve())  # macOS /tmp is a symlink


def test_zero_arg_entries_fall_back_to_argv_patching(monkeypatch):
    seen = {}

    def zero_arg_entry():  # reads sys.argv like an old argparse main
        seen["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(
        tools, "_console_entrypoint", lambda name: _FakeEP(zero_arg_entry)
    )
    saved = list(sys.argv)
    assert tools.Tool("legacy", in_process=True)("build", fast=True) == 0
    assert seen["argv"] == ["legacy", "build", "--fast"]
    assert sys.argv == saved


def test_mixed_tool_output_is_never_interleaved(monkeypatch, capsys, tmp_path):
    """Eight virtual tools — half in-process, half real subprocesses of the
    same script — printing name+counter with overlap-forcing sleeps. The
    aggregate stream must be perfectly block-contiguous per tool, and every
    tool's lines strictly incremental."""
    import re
    import threading
    import time

    from footman import manifest, schedule
    from footman.registry import Group
    from footman.split import split_chain

    lines, tool_count = 20, 8
    script = tmp_path / "vtool.py"
    script.write_text(
        "import sys, time\n"
        "name, count = sys.argv[1], int(sys.argv[2])\n"
        "for i in range(1, count + 1):\n"
        '    print(f"{name} {i}", flush=True)\n'
        "    time.sleep(0.005)\n"
    )

    # Guard against vacuity, structurally rather than by wall clock (which
    # flakes on slow CI runners whose interpreter startup dwarfs the sleeps):
    # all four in-process entries must be running at once to pass this
    # barrier. If the lock-free path ever regresses to serialised, the first
    # entry blocks here holding the serialiser, the rest can never arrive,
    # and the barrier breaks — failing the run on any hardware.
    overlap = threading.Barrier(tool_count // 2, timeout=10)

    def make_entry(name):
        def entry(argv):  # accepts args -> the parallel, lock-free path
            overlap.wait()
            for i in range(1, int(argv[0]) + 1):
                print(f"{name} {i}", flush=True)
                time.sleep(0.005)
            return 0

        return entry

    names = [f"vtool-{i}" for i in range(tool_count)]
    entries = {n: make_entry(n) for i, n in enumerate(names) if i % 2 == 0}
    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entries[name]) if name in entries else None,
    )

    reg = Group("root")
    for i, name in enumerate(names):
        if i % 2 == 0:

            def body(n=name):
                tools.Tool(n, in_process=True)(str(lines))

        else:

            def body(n=name):
                tools.python(str(script), n, str(lines))

        reg.task(name=name)(body)

    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, names)
    results = schedule.run_plan(reg, segments, ctx_config={"verbose": True})

    # Level 1: each step captured only its own tool, in strict order.
    by_task = {r.task: r for r in results}
    assert len(by_task) == tool_count and all(r.ok for r in results)
    for name in names:
        got = by_task[name].steps[0].output.strip().splitlines()
        assert got == [f"{name} {i}" for i in range(1, lines + 1)]

    # Level 2: the aggregate stream is block-contiguous — 100% un-interleaved.
    counted = [
        m.groups()
        for line in capsys.readouterr().out.splitlines()
        if (m := re.fullmatch(r"(vtool-\d+) (\d+)", line))
    ]
    assert len(counted) == tool_count * lines
    seen_blocks = [name for name, _ in counted[::lines]]
    assert sorted(seen_blocks) == sorted(names)  # eight blocks, one per tool
    for start in range(0, len(counted), lines):
        block = counted[start : start + lines]
        block_name = block[0][0]
        assert all(name == block_name for name, _ in block)
        assert [int(i) for _, i in block] == list(range(1, lines + 1))


def test_in_process_preference_survives_subcommand_chaining():
    # Chained subcommands keep the mode (checked without executing: real
    # coverage mid-test-session would read the live .coverage data and the
    # project's own fail_under). Plain Tool instances, so the probe goes
    # through __getattr__ like any un-stubbed verb.
    assert tools.Tool("coverage", in_process=True).report._prefer_in_process is True
    assert tools.Tool("mkdocs", in_process=True).build._prefer_in_process is True
    assert tools.Tool("git").status._prefer_in_process is False


# --- how a tool spells "off" ---------------------------------------------------


def test_off_uses_the_tools_own_negation():
    """`off` assumed `--no-<name>`, which is wrong often enough to break
    real commands: `mkdocs build --no-clean` is rejected outright — the
    flag is `--dirty`. The exceptions are extracted from the tools, not
    guessed."""
    from footman.tools import _flags, off

    assert _flags({"clean": off}, "mkdocs") == ["--dirty"]
    assert _flags({"use_directory_urls": off}, "mkdocs") == ["--no-directory-urls"]
    assert _flags({"strict": off}, "mkdocs") == ["--no-strict"]  # convention holds
    assert _flags({"fix": off}, "ruff") == ["--no-fix"]  # other tools unaffected
    assert _flags({"clean": off}) == ["--no-clean"]  # no tool named: the default


def test_click_extraction_reads_the_real_negations():
    """click states a negatable flag as opts + secondary_opts — the fact
    `off` needs and cannot infer. This is the extractor that fills the
    table, run against the real mkdocs."""
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry  # type: ignore[import-not-found]

    from footman._toolspec import from_click

    spec = from_click(entry.cli, name="mkdocs")
    assert spec.name == "mkdocs" and spec.in_process is True
    assert {"build", "serve", "gh_deploy"} <= {v.name for v in spec.verbs}
    assert spec.negations() == {
        "clean": "--dirty",
        "use_directory_urls": "--no-directory-urls",
    }
    build = next(v for v in spec.verbs if v.name == "build")
    clean = next(o for o in build.options if o.name == "clean")
    assert clean.type_name == "bool" and clean.negation == "--dirty"
    assert clean.help  # the tool's own words, for the stub's docstring


def test_negation_table_matches_what_the_tools_say():
    """The committed table is a cache of what the tools state; if a tool
    changes its spelling, this fails rather than emitting a flag the tool
    will reject."""
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry  # type: ignore[import-not-found]

    from footman._toolspec import from_click
    from footman.tools import _NEGATIONS

    assert from_click(entry.cli, name="mkdocs").negations() == _NEGATIONS["mkdocs"]


# --- structured invocation rendering -----------------------------------------
#
# The command line footman *shows* is built from the same translation it
# *executes*, but spelled for a human: separated flags, shell-quoted values,
# role-tagged for colour. `recording()` sees that shown form (via
# StepResult.command), which is why these assertions read naturally and stay
# stable even when execution tokenises differently.


def test_shown_values_are_shell_quoted_so_the_line_pastes():
    cmd = _one(lambda: tools.git.commit(message="release: cut it now", signoff=True))
    assert cmd == "git commit --message 'release: cut it now' --signoff"


def test_shown_line_uses_the_off_negation_not_the_keyword():
    cmd = _one(lambda: tools.mkdocs.build(strict=True, clean=tools.off))
    assert cmd == "mkdocs build --strict --dirty"


def test_in_process_call_shows_the_command_not_the_flattened_title():
    # The in-process path used to display `" ".join(argv)`; it now shows the
    # same normalised line as any other call.
    import io
    from contextlib import redirect_stdout

    from footman.context import Context, use_context

    buf = io.StringIO()
    with redirect_stdout(buf), use_context(Context(dry_run=True)):
        tools.coverage.html(directory="htmlcov", skip_covered=True)
    assert "$ coverage html --directory htmlcov --skip-covered" in buf.getvalue()


def test_show_parts_tag_each_token_with_its_role():
    from footman.tools import _show_parts

    parts = _show_parts("ruff", ["check"], ("src",), {"fix": True, "select": ["E"]})
    assert parts == (
        ("prog", "ruff"),
        ("group", "check"),
        ("req", "src"),
        ("opt", "--fix"),
        ("opt", "--select"),
        ("value", "E"),
    )


def test_the_shown_form_is_separated_the_executed_form_is_attached():
    # `_emit` is the single source both draw from. `_flags` (executed)
    # attaches long values; `_show_parts` (shown) keeps them separated.
    from footman.tools import _emit, _flags, _show_parts

    kwargs = {"select": ["E", "F"], "fix": True}
    assert list(_emit(kwargs, "ruff")) == [
        ("--select", "E"),
        ("--select", "F"),
        ("--fix", None),
    ]
    assert _flags(kwargs, "ruff") == ["--select=E", "--select=F", "--fix"]
    shown = " ".join(t for _, t in _show_parts("ruff", ["check"], (), kwargs))
    assert shown == "ruff check --select E --select F --fix"


def test_execution_attaches_only_where_a_space_would_break():
    from footman.tools import _flags, _show_parts

    def shown(**kw):
        return " ".join(t for _, t in _show_parts("git", ["log"], (), kw))

    # A dash-leading value would be read as the next option if separated, so
    # both forms attach — the shown line has to stay a valid paste.
    assert _flags({"format": "-%h"}, "git") == ["--format=-%h"]
    assert shown(format="-%h") == "git log --format=-%h"

    # An optional-value option (git spells `--abbrev[=<n>]`) can't tell its
    # value from a positional across a space; execution attaches, the shown
    # line reads it plainly.
    assert _flags({"abbrev": 4}, "git") == ["--abbrev=4"]
    assert shown(abbrev=4) == "git log --abbrev 4"

    # A short option keeps the space unless the value leads with a dash.
    assert _flags({"k": "expr"}, "git") == ["-k", "expr"]
    assert _flags({"k": "-x"}, "git") == ["-k-x"]


def test_step_result_carries_both_the_shown_and_the_raw_command():
    # `.command` reads well (separated); `.raw` is the exact executed line
    # (attached). Both are valid, copy-pasteable commands.
    with recording() as steps:
        tools.git.commit(message="a b c", signoff=True)
    step = steps[0]
    assert step.command == "git commit --message 'a b c' --signoff"
    assert step.raw == "git commit '--message=a b c' --signoff"


def test_raw_of_a_plain_run_shell_quotes_a_list():
    # A direct `run([...])` (not through the bridge) still gets a raw form:
    # the list, shell-quoted so it pastes, while `.command` reads plainly.
    from footman.context import Context, run, use_context

    ctx = Context(dry_run=True)
    with use_context(ctx):
        run(["echo", "a b"])
    assert ctx.steps[-1].raw == "echo 'a b'"
    assert ctx.steps[-1].command == "echo a b"
