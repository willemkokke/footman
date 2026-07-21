"""Reading a tool's self-description, and rendering it as a stub.

The help fixtures below are real output, trimmed: one per family footman
has to read (clap, optparse, cobra, git, commander). They are checked in
rather than captured live so the parser is tested on every machine,
including the ones where docker isn't installed — the tasks that talk to
real binaries are exercised separately, against whatever is present.
"""

from __future__ import annotations

import ast
import shutil
from types import SimpleNamespace

import pytest

from footman import _drivers, _stubgen, _toolhelp, _toolspec
from footman._toolspec import Option, ToolSpec, Verb

CLAP = """\
Usage: ruff check [OPTIONS] [FILES]...

Run Ruff on the given files or directories

Arguments:
  [FILES]...
          List of files or directories to check [default: .]

Options:
      --fix
          Apply fixes to resolve lint violations. Use `--no-fix` to disable or
          `--unsafe-fixes` to include unsafe fixes

  -w, --watch
          Run in watch mode by re-running whenever files change

      --fix-only
          Apply fixes, but don't report on leftover violations. Use
          `--no-fix-only` to disable

      --color <WHEN>
          Control when colored output is used

          Possible values:
          - auto:   Display colors if the output goes to an interactive terminal
          - always: Always display colors
          - never:  Never display colors

      --line-length <LINE_LENGTH>
          Set the line-length [default: 88]

Rule selection:
      --select <RULE_CODE>
          Comma-separated list of rule codes to enable

  -h, --help
          Print help
"""

OPTPARSE = """\
Usage: coverage html [options] [modules]

Create an HTML report of coverage results.

Options:
  --contexts=REGEX1,REGEX2,...
                        Only display data from lines covered in the given
                        contexts. Accepts Python regexes, which must be quoted.
  -d DIR, --directory=DIR
                        Write the output files to DIR.
  --fail-under=MIN      Exit with a status of 2 if the total coverage is less
                        than MIN.
  -i, --ignore-errors   Ignore errors while reading source files.
  -h, --help            Get help on this command.
"""

COBRA = """\
Usage:  docker compose up [OPTIONS] [SERVICE...]

Create and start containers

Options:
      --build                  Build images before starting containers
      --no-build               Don't build an image, even if it's missing
  -d, --detach                 Detached mode: Run containers in the background
      --tail string            Number of lines to show (default "all")
      --scale stringArray      Scale SERVICE to NUM instances
      --remove-orphans         Remove containers for services not defined
"""

GIT = """\
usage: git commit [-a | --interactive | --patch] [-s] [-v]
                  [--amend] [--dry-run]

    -q, --[no-]quiet      suppress summary after successful commit
    -F, --[no-]file <file>
                          read message from file

Commit message options
    -m, --[no-]message <message>
                          commit message
    -s, --[no-]signoff    add a Signed-off-by trailer
"""

COMMANDER = """\
Usage: markdownlint-cli2 [options] <glob>

Options:
  -c, --config <file>   configuration file
  -f, --fix             fix violations where possible
  -h, --help            display help for command
"""

SUBCOMMANDS = """\
Usage: mkdocs [OPTIONS] COMMAND [ARGS]...

Commands:
  build      Build the MkDocs documentation
  gh-deploy  Deploy your documentation to GitHub Pages
  serve      Run the builtin development server
"""


def flags(verb: Verb) -> dict[str, Option]:
    return {o.name: o for o in verb.options}


def driver(key: str) -> _drivers.Driver:
    found = _drivers.find(key)
    assert found is not None, key
    return found


# --- reading each family --------------------------------------------------


def test_clap_options_negations_and_choices():
    verb = _toolhelp.parse_help(CLAP, name="check")
    got = flags(verb)
    assert verb.help == "Run Ruff on the given files or directories"
    # Options live under several headings — `Rule selection:` counts too.
    assert {"fix", "watch", "fix_only", "color", "line_length", "select"} <= set(got)
    # ...and the two every tool has are never worth stubbing.
    assert "help" not in got
    # The negation is stated in prose, which is the only place clap says it.
    assert got["fix"].negation == "--no-fix"
    assert got["fix_only"].negation == "--no-fix-only"
    # `--watch` must NOT inherit the negation of the option printed below it.
    assert got["watch"].negation == ""
    assert got["color"].choices == ("auto", "always", "never")
    assert got["color"].type_name == "choice"
    assert got["line_length"].default == "88"
    assert got["fix"].type_name == "bool"
    assert got["line_length"].type_name == "str"


def test_clap_flag_indent_varies_within_one_block():
    """`  -w, --watch` and `      --fix-only` are both flag lines.

    The help column, not the flag column, is the boundary — reading it the
    other way glues each long-only option onto the option above it.
    """
    blocks = _toolhelp._blocks(CLAP.splitlines())
    heads = [head for head, _ in blocks]
    assert "-w, --watch" in heads
    assert "--fix-only" in heads


def test_optparse_two_column_and_attached_values():
    verb = _toolhelp.parse_help(OPTPARSE, name="html")
    got = flags(verb)
    assert set(got) == {"contexts", "directory", "fail_under", "ignore_errors"}
    assert got["directory"].flags == ("--directory", "-d")
    assert got["directory"].type_name == "str"
    assert got["ignore_errors"].type_name == "bool"
    assert got["directory"].help == "Write the output files to DIR"


def test_cobra_go_types_are_values_not_flags():
    verb = _toolhelp.parse_help(COBRA, name="up")
    got = flags(verb)
    assert got["tail"].type_name == "str", "`--tail string` takes a value"
    assert got["scale"].type_name == "list[str]", "`stringArray` repeats"
    assert got["detach"].type_name == "bool"
    assert got["tail"].default == "all"
    # `--no-build` folds into `--build` rather than standing on its own.
    assert "no_build" not in got
    assert got["build"].negation == "--no-build"


def test_git_states_both_spellings_inline():
    verb = _toolhelp.parse_help(GIT, name="commit")
    got = flags(verb)
    assert {"quiet", "file", "message", "signoff"} <= set(got)
    assert got["quiet"].negation == "--no-quiet"
    # The tool *prints* `--[no-]quiet`; it *accepts* `--quiet`.
    assert "--quiet" in got["quiet"].flags
    assert not any("[no-]" in f for f in got["quiet"].flags)
    assert got["file"].type_name == "str"
    assert got["signoff"].help == "add a Signed-off-by trailer"


def test_optional_value_option_is_neither_switch_nor_required_value():
    # git glues an optional-value placeholder to the flag with no space.
    # Read as a switch, `--gpg-sign[=<key-id>]` would reject a key; read as
    # a required value, it would reject the bare flag. It is both.
    text = (
        "    -S, --[no-]gpg-sign[=<key-id>]\n"
        "                          GPG-sign the commit\n"
        "    -u, --[no-]untracked-files[=<mode>]\n"
        "                          show untracked files\n"
        "    -m, --[no-]message <message>\n"
        "                          commit message\n"
    )
    got = flags(_toolhelp.parse_help(text, name="commit"))
    assert got["gpg_sign"].type_name == "optvalue"
    assert got["gpg_sign"].negation == "--no-gpg-sign"
    assert got["untracked_files"].type_name == "optvalue"
    # A required value (no brackets) stays a plain value, not optvalue.
    assert got["message"].type_name == "str"


def test_optvalue_stub_type_accepts_bare_and_valued():
    from footman._toolspec import Option

    assert (
        _stubgen._annotation(Option("gpg_sign", type_name="optvalue")) == "_ValuedFlag"
    )
    assert _stubgen._annotation(Option("m", type_name="str")) == "_Value"


def test_commander_and_summary_skips_usage():
    verb = _toolhelp.parse_help(COMMANDER)
    got = flags(verb)
    assert set(got) == {"config", "fix"}
    assert got["config"].type_name == "str"
    assert _toolhelp._summary(COMMANDER) == ""


def test_subcommands_are_read_from_their_own_section():
    found = _toolhelp.subcommands(SUBCOMMANDS)
    assert found["build"] == "Build the MkDocs documentation"
    assert "gh-deploy" in found


def test_help_of_a_missing_tool_is_empty_not_an_error():
    assert _toolhelp.run_help(["definitely-not-a-real-tool-xyz"]) == ""
    spec = _toolhelp.from_help("definitely-not-a-real-tool-xyz")
    assert spec.verbs == ()


# --- reading click, which hands over structure ----------------------------


def _param(name, opts, **kw):
    """A duck-typed click parameter — the extractor never imports click."""
    choices = kw.pop("choices", None)
    return SimpleNamespace(
        param_type_name="option",
        name=name,
        opts=opts,
        secondary_opts=kw.pop("secondary_opts", []),
        is_flag=kw.pop("is_flag", False),
        multiple=kw.pop("multiple", False),
        default=kw.pop("default", None),
        help=kw.pop("help", ""),
        type=SimpleNamespace(name="choice" if choices else "text", choices=choices),
    )


def _command(help_text, params):
    return SimpleNamespace(help=help_text, params=params, name="build")


def test_click_names_options_after_the_flag_not_the_variable():
    """click calls a group of exclusive flags after one internal variable.

    mkdocs' `--dirty`, `--clean` and `--dirtyreload` are all `build_type`;
    naming the stub after that would emit three parameters with one name,
    and the bridge would translate a keyword no tool accepts.
    """
    command = _command(
        "Serve the docs.",
        [
            _param("build_type", ["--dirty"], is_flag=True),
            _param("build_type", ["--dirtyreload"], is_flag=True),
            _param("build_type", ["--clean"], is_flag=True),
        ],
    )
    verb = _toolspec._verb_from_click("serve", command)
    assert sorted(flags(verb)) == ["clean", "dirty", "dirtyreload"]


def test_click_secondary_opts_are_the_true_negation():
    command = _command(
        "Build it.",
        [
            _param("clean", ["--clean"], secondary_opts=["--dirty"], is_flag=True),
            _param(
                "strict", ["--strict"], secondary_opts=["--no-strict"], is_flag=True
            ),
            _param("theme", ["--theme"], choices=["material", "readthedocs"]),
        ],
    )
    spec = ToolSpec(
        name="mkdocs", verbs=(_toolspec._verb_from_click("build", command),)
    )
    got = flags(spec.verbs[0])
    assert got["clean"].negation == "--dirty"
    assert got["theme"].choices == ("material", "readthedocs")
    # Only the exceptions are tabled: a table of things that already work
    # would be noise, and would need regenerating far more often.
    assert spec.negations() == {"clean": "--dirty"}


def test_click_group_becomes_one_verb_per_command():
    sub = _command("Build it.", [_param("strict", ["--strict"], is_flag=True)])
    group = SimpleNamespace(
        help="Docs.", name="mkdocs", params=[], commands={"gh-deploy": sub}
    )
    spec = _toolspec.from_click(group, name="mkdocs", version="1.6.1")
    assert [v.name for v in spec.verbs] == ["gh_deploy"]
    assert spec.in_process is True
    assert spec.version == "1.6.1"


# --- rendering the stub ---------------------------------------------------


def _spec(*options: Option, name: str = "demo", verb: str = "build") -> ToolSpec:
    return ToolSpec(
        name=name,
        help="A demo tool.",
        version="1.0",
        verbs=(Verb(name=verb, help="Build it.", options=options),),
    )


def test_rendered_stub_is_valid_python():
    spec = _spec(
        Option(
            "clean",
            ("--clean",),
            negation="--dirty",
            help="Wipe first",
            type_name="bool",
            default=True,
        ),
        Option("select", ("--select",), help="Rules", type_name="list[str]"),
        Option(
            "color",
            ("--color",),
            help="When",
            type_name="choice",
            choices=("auto", "never"),
        ),
    )
    text = _stubgen.render(spec, platform="Linux")
    ast.parse(text)  # a stub that doesn't parse is worse than no stub
    assert "class Demo(Tool):" in text
    assert "def build(" in text
    assert "**flags: Any" in text, "the stub must never be able to forbid"


def test_rendered_stub_teaches_the_off_spelling():
    spec = _spec(
        Option(
            "clean",
            ("--clean",),
            negation="--dirty",
            help="Wipe first",
            type_name="bool",
            default=True,
        ),
        Option(
            "strict",
            ("--strict",),
            negation="--no-strict",
            help="Be strict",
            type_name="bool",
        ),
    )
    text = _stubgen.render(spec)
    assert "`clean=off` emits `--dirty`" in text
    assert "Defaults on" in text, "a flag that is on by default says so"
    assert "`strict=off` emits `--no-strict`" in text


def test_rendered_stub_imports_only_what_it_uses():
    plain = _stubgen.render(_spec(Option("quiet", ("--quiet",), type_name="bool")))
    assert "Literal" not in plain
    assert "_Value" not in plain, "no value option, so no value alias"
    assert "from footman.tools import Tool, _Flag" in plain

    choosy = _stubgen.render(
        _spec(Option("color", ("--color",), type_name="choice", choices=("a", "b")))
    )
    assert "from typing import Any, Literal" in choosy
    assert "Sequence" in choosy


def test_rendered_stub_never_repeats_a_keyword():
    """A duplicate parameter is a syntax error, so the renderer is the last
    line of defence whatever a spec happens to contain."""
    spec = _spec(
        Option("dirty", ("--dirty",), type_name="bool"),
        Option("dirty", ("--dirty",), type_name="bool"),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    assert text.count("dirty: _Flag") == 1


def test_value_options_accept_a_sequence():
    """`select=["E", "F"]` works at run time, so it must type-check.

    The bridge repeats a flag once per item; whether the tool accepts the
    repetition is the tool's business, not the stub's.
    """
    option = Option("select", ("--select",), type_name="str")
    assert _stubgen._annotation(option) == "_Value"
    assert _stubgen._annotation(Option("f", ("--f",), type_name="bool")) == "_Flag"


def test_a_tool_with_no_verbs_still_renders():
    spec = ToolSpec(name="lonely", verbs=(Verb(name="", help="Do it."),))
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "def __call__(" in text
    assert "type: ignore[override]" in text


def test_nested_verbs_become_nested_classes():
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(name="compose.up", help="Up.", options=()),
            Verb(name="build", help="Build.", options=()),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "class DockerCompose(Tool):" in text
    assert "compose: DockerCompose" in text


def test_keyword_named_flags_take_the_trailing_underscore():
    spec = _spec(Option("global", ("--global",), type_name="bool"))
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "global_: _Flag" in text


def test_long_help_wraps_inside_the_line_limit():
    spec = _spec(
        Option(
            "explain",
            ("--explain",),
            help="A very long line of help text " * 6,
            type_name="bool",
        )
    )
    text = _stubgen.render(spec)
    assert max(len(line) for line in text.splitlines()) <= 88


# --- the driver table -----------------------------------------------------


def test_every_driver_maps_to_a_bridge_attribute():
    from footman import tools

    for driver in _drivers.DRIVERS:
        assert isinstance(getattr(tools, driver.key), tools.Tool)


def test_driver_lookup_and_pre_bound_verbs():
    assert driver("ruff_format").base == ("format",)
    assert driver("ruff_format").wanted == ("format",)
    assert driver("ruff").wanted == ("check", "format", "clean")
    assert driver("markdownlint").name == "markdownlint-cli2"
    assert _drivers.find("nope") is None


def test_a_pre_bound_tool_stubs_its_verb_as_call():
    spec = ToolSpec(
        name="ruff",
        verbs=(
            Verb(name="check", options=(Option("fix", ("--fix",), type_name="bool"),)),
            Verb(
                name="format", options=(Option("diff", ("--diff",), type_name="bool"),)
            ),
        ),
    )
    rebased = _drivers._rebase(spec, ("format",))
    assert [v.name for v in rebased.verbs] == [""]
    assert flags(rebased.verbs[0])["diff"].name == "diff"


def test_selecting_verbs_keeps_the_tools_own_options():
    spec = ToolSpec(
        name="uv",
        verbs=(Verb(name=""), Verb(name="sync"), Verb(name="publish")),
    )
    kept = _drivers._select(spec, ("sync",))
    assert [v.name for v in kept.verbs] == ["", "sync"]


def test_version_of_a_missing_tool_is_empty():
    assert _drivers.version("definitely-not-a-real-tool-xyz") == ""


def test_in_process_capability_is_the_entry_point():
    # coverage publishes a console script; a shell builtin never will.
    assert _drivers.in_process_capable("coverage") is True
    assert _drivers.in_process_capable("definitely-not-a-real-tool-xyz") is False


@pytest.mark.parametrize("key", [d.key for d in _drivers.DRIVERS])
def test_every_curated_tool_has_a_checked_in_stub(key):
    from footman.tasks import tools as tools_tasks

    assert tools_tasks._stub_path(key).exists(), f"no stub for {key}"


# --- the tasks that talk to real binaries ---------------------------------


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """Point the tasks at a scratch stub directory, not the package's."""
    from footman.tasks import tools as tools_tasks

    monkeypatch.setattr(tools_tasks, "_STUBS", tmp_path)
    return tmp_path


needs_ruff = pytest.mark.skipif(
    shutil.which("ruff") is None, reason="ruff is not on PATH"
)
needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")


def test_list_names_every_curated_tool(capsys):
    from footman.tasks import tools as tools_tasks

    tools_tasks.list_()
    out = capsys.readouterr().out
    for expected in ("ruff", "mkdocs", "markdownlint"):
        assert expected in out
    assert "in-process" in out


def test_list_missing_only_shows_what_is_absent(capsys):
    from footman.tasks import tools as tools_tasks

    tools_tasks.list_(missing=True)
    out = capsys.readouterr().out
    for line in out.splitlines()[1:]:
        assert "not installed" in line


@needs_ruff
def test_spec_prints_what_the_tool_says(capsys):
    from footman.tasks import tools as tools_tasks

    tools_tasks.spec("ruff", verb="check")
    out = capsys.readouterr().out
    assert "ruff" in out
    assert "check" in out
    assert "--fix" in out or "fix" in out


def test_spec_refuses_an_unknown_or_absent_tool():
    from footman.tasks import tools as tools_tasks

    with pytest.raises(SystemExit, match="no driver"):
        tools_tasks.spec("not-a-curated-tool")


@needs_ruff
def test_sync_writes_a_stub_and_audit_then_agrees(stubs, capsys):
    from footman.tasks import tools as tools_tasks

    tools_tasks.sync(only="ruff")
    written = stubs / "ruff.pyi"
    assert written.exists()
    ast.parse(written.read_text())
    assert "class Ruff(Tool):" in written.read_text()
    capsys.readouterr()

    tools_tasks.audit(only="ruff")
    assert "match their installed tool" in capsys.readouterr().out


@needs_ruff
def test_audit_fails_when_a_stub_drifts(stubs, capsys):
    from footman.tasks import tools as tools_tasks

    tools_tasks.sync(only="ruff")
    (stubs / "ruff.pyi").write_text("class Ruff(Tool): ...\n")
    capsys.readouterr()
    with pytest.raises(SystemExit, match="differ from the installed tool"):
        tools_tasks.audit(only="ruff")

    # ...and --fix writes the difference instead of complaining.
    tools_tasks.audit(only="ruff", fix=True)
    assert "updated 1 stub" in capsys.readouterr().out
    assert "class Ruff(Tool):\n    def __call__(" in (stubs / "ruff.pyi").read_text()


@needs_ruff
def test_audit_reports_a_runtime_table_that_disagrees(stubs, monkeypatch):
    from footman import tools as bridge
    from footman.tasks import tools as tools_tasks

    monkeypatch.setitem(bridge._NEGATIONS, "ruff", {"fix": "--never-fix"})
    with pytest.raises(SystemExit, match=r"_NEGATIONS\['ruff'\]"):
        tools_tasks.audit(only="ruff")


@needs_uv
def test_audit_reports_a_wrappers_table_that_disagrees(stubs, monkeypatch):
    from footman import tools as bridge
    from footman.tasks import tools as tools_tasks

    monkeypatch.setitem(bridge._WRAPPERS, "uv", frozenset({"run"}))  # missing tool.run
    with pytest.raises(SystemExit, match=r"_WRAPPERS\['uv'\]"):
        tools_tasks.audit(only="uv")


def test_sync_skips_and_names_the_tools_it_cannot_ask(stubs, capsys):
    """A check that quietly covered three of thirteen would be worse than
    no check, so what was skipped is printed."""
    from footman.tasks import tools as tools_tasks

    tools_tasks.sync(only="definitely-not-installed")
    out = capsys.readouterr().out
    assert "wrote 0 stub(s)" in out


def test_formatting_falls_back_when_ruff_cannot_run(monkeypatch):
    from footman.tasks import tools as tools_tasks

    def boom(*args, **kwargs):
        raise OSError("no ruff here")

    monkeypatch.setattr("subprocess.run", boom)
    assert tools_tasks._formatted("class _X: ...\n") == "class _X: ...\n"


# --- the extraction ladder ------------------------------------------------


def test_click_is_preferred_over_help_text():
    """mkdocs is a click tool, so `--dirty` is known structurally rather
    than hoped for in prose."""
    found = _drivers.find("mkdocs")
    assert found is not None
    if not _drivers.installed(found):
        pytest.skip("mkdocs is not installed")
    spec = _drivers.extract(found)
    assert spec.negations()["clean"] == "--dirty"
    assert spec.in_process is True


def test_a_tool_with_no_entry_point_is_not_a_click_tool():
    assert _drivers._from_click(_drivers.Driver("definitely-not-real")) is None


def test_an_entry_point_that_is_not_click_falls_through(monkeypatch):
    from footman import tools as bridge

    monkeypatch.setattr(
        bridge, "_console_entrypoint", lambda name: SimpleNamespace(load=lambda: len)
    )
    assert _drivers._from_click(_drivers.Driver("pretend")) is None


def test_an_entry_point_that_will_not_import_is_not_a_spec(monkeypatch):
    from footman import tools as bridge

    def explode():
        raise ImportError("that tool is broken")

    monkeypatch.setattr(
        bridge, "_console_entrypoint", lambda name: SimpleNamespace(load=explode)
    )
    assert _drivers._from_click(_drivers.Driver("pretend")) is None


def test_extracting_an_absent_tool_yields_an_empty_spec():
    spec = _drivers.extract(_drivers.Driver("definitely-not-a-real-tool-xyz"))
    assert spec.verbs == ()
    assert (
        _drivers.installed(_drivers.Driver("definitely-not-a-real-tool-xyz")) is False
    )


def test_rebasing_a_verb_that_is_not_there():
    spec = ToolSpec(name="x", verbs=(Verb(name="check"),))
    assert _drivers._rebase(spec, ("format",)).verbs == ()


# --- the reference pages --------------------------------------------------


def test_pages_writes_one_per_tool_plus_an_index(tmp_path):
    from footman.tasks import tools as tools_tasks

    tools_tasks.pages(tmp_path)
    index = (tmp_path / "index.md").read_text()
    for driver in _drivers.DRIVERS:
        page = tmp_path / f"{driver.key}.md"
        assert page.exists(), driver.key
        body = page.read_text()
        # mkdocstrings renders the class out of the stub, so the page is a
        # pointer rather than a copy — nothing to drift.
        assert f"::: footman._stubs.{driver.key}." in body
        assert f"({driver.key}.md)" in index
        if driver.url:
            assert driver.url in index, "the table links out to the tool itself"


def test_the_index_states_the_version_each_stub_was_read_from(tmp_path):
    from footman.tasks import tools as tools_tasks

    tools_tasks.pages(tmp_path)
    index = (tmp_path / "index.md").read_text()
    assert "| Tool | Read from | In-process | Verbs |" in index
    # mkdocs is a click tool footman prefers to run in-process.
    row = next(line for line in index.splitlines() if "`mkdocs`" in line)
    assert "default" in row
    assert "`build`" in row


def test_a_hand_written_stub_says_so_rather_than_inventing_a_version(tmp_path):
    from footman.tasks import tools as tools_tasks

    stub = tmp_path / "x.pyi"
    stub.write_text("# Hand-written, not generated: x is not installed\n")
    assert tools_tasks._header(stub) == ("hand-written", "unknown")

    stub.write_text(
        "# Generated by `fm footman tools sync`\n"
        "#\n"
        "# Read from ruff 0.15.0 on Linux. In-process: no.\n"
    )
    assert tools_tasks._header(stub) == ("0.15.0 (Linux)", "no")


def test_in_process_mode_is_detected_not_listed():
    from footman.tasks import tools as tools_tasks

    capable = ToolSpec(name="x", in_process=True)
    plain = ToolSpec(name="x", in_process=False)
    assert (
        tools_tasks._mode(_drivers.Driver("x", in_process=True), capable) == "default"
    )
    assert tools_tasks._mode(_drivers.Driver("x"), capable) == "available"
    assert tools_tasks._mode(_drivers.Driver("x"), plain) == "no"


# --- positional shape from the usage line ---------------------------------
#
# A wrong shape *forbids a valid call*, so these pin the exact boundary
# between the confident answers (none / required) and the permissive default.


def shape(usage: str) -> tuple[str, str]:
    return _toolhelp._usage_shape(f"Usage: tool {usage}\n\nDo a thing.\n")


def test_shape_none_only_when_the_grammar_is_options_only():
    assert shape("[OPTIONS]") == ("none", "")
    assert shape("[options]") == ("none", "")
    # A positional anywhere means not-none, even alongside options.
    assert shape("[OPTIONS] NAME[:TAG|@DIGEST]") == ("required", "name")


def test_shape_required_for_a_clean_leading_metavar():
    assert shape("[OPTIONS] IMAGE [COMMAND] [ARG...]") == ("required", "image")
    assert shape("[<options>] [--] <repo> [<dir>]") == ("required", "repo")
    assert shape("[options] <pyfile> [program options]") == ("required", "pyfile")


def test_shape_stays_any_where_a_wrong_guess_would_forbid_a_call():
    # An option woven into an alternation — packages OR --requirements.
    assert shape("[OPTIONS] <PACKAGES|--requirements <REQS>>") == ("any", "")
    # A bracketed-optional or variadic leading argument.
    assert shape("[OPTIONS] [COMMAND]") == ("any", "")
    assert shape("[options] [FILES]...") == ("any", "")
    # A numbered metavar is a list written long-hand, not one required arg.
    assert shape("[options] <path1> <path2> ... <pathN>") == ("any", "")


def test_shape_ignores_option_values_scattered_by_whitespace():
    # `<git-dir>` is the value of `--separate-git-dir`, not a positional —
    # depth tracking keeps it out.
    usage = "[-q | --quiet] [--separate-git-dir <git-dir>] [<directory>]"
    assert shape(usage) == ("any", "")  # only [<directory>], which is optional


def test_shape_reads_only_the_first_of_gits_or_forms():
    text = (
        "usage: git branch [<options>] [--list] [<pattern>...]\n"
        "   or: git branch [<options>] [-f] <branchname> [<start-point>]\n"
    )
    # The first form is all-optional; the `or:` create-form is not stitched in.
    assert _toolhelp._usage_shape(text) == ("any", "")


def test_click_arguments_give_the_shape_exactly():
    # click hands arguments over as data — no usage parsing needed.
    none = SimpleNamespace(help="Build.", name="build", params=[])
    assert _toolspec._verb_from_click("build", none).positional == "none"

    arg = SimpleNamespace(
        param_type_name="argument", name="image", required=True, nargs=1
    )
    one = SimpleNamespace(help="Run.", name="run", params=[arg])
    verb = _toolspec._verb_from_click("run", one)
    assert (verb.positional, verb.lead) == ("required", "image")

    variadic = SimpleNamespace(
        param_type_name="argument", name="paths", required=True, nargs=-1
    )
    many = SimpleNamespace(help="Add.", name="add", params=[variadic])
    assert _toolspec._verb_from_click("add", many).positional == "any"


def test_stub_renders_positional_only_and_keyword_only():
    none = ToolSpec(name="x", verbs=(Verb(name="build", positional="none"),))
    text = _stubgen.render(none)
    ast.parse(text)
    assert "*,\n" in text and "*args" not in text  # keyword-only

    req = ToolSpec(
        name="x", verbs=(Verb(name="run", positional="required", lead="image"),)
    )
    text = _stubgen.render(req)
    ast.parse(text)
    assert "image: str,\n" in text and "/,\n" in text


def test_stub_falls_back_when_the_lead_collides_with_an_option():
    from footman._toolspec import Option

    verb = Verb(
        name="pip_install",
        positional="required",
        lead="group",
        options=(Option("group", ("--group",), type_name="str"),),
    )
    text = _stubgen.render(ToolSpec(name="uv", verbs=(verb,)))
    ast.parse(text)  # a duplicate `group` parameter would be a syntax error
    assert "*args: str," in text


def test_wraps_detected_from_a_trailing_command_metavar():
    run = _toolhelp.parse_help(
        "Usage: uv run [OPTIONS] [COMMAND]\n\nRun.\n", name="run"
    )
    assert run.wraps is True
    cov = _toolhelp.parse_help(
        "Usage: coverage run [options] <pyfile> [program options]\n\nRun.\n", name="run"
    )
    assert cov.wraps is True
    # A verb that merely takes files is not a wrapper.
    check = _toolhelp.parse_help(
        "Usage: ruff check [OPTIONS] [FILES]...\n\nCheck.\n", name="check"
    )
    assert check.wraps is False


def test_spec_wrappers_lists_the_dotted_wrapper_paths():
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(name="run", wraps=True),
            Verb(name="build", wraps=False),
            Verb(name="compose.run", wraps=True),
        ),
    )
    assert spec.wrappers() == frozenset({"run", "compose.run"})


# --- git via its manual (`git help <verb>`) ----------------------------------

GIT_MAN = """\
GIT-CLONE(1)                      Git Manual                      GIT-CLONE(1)

NAME
       git-clone - Clone a repository into a new directory

SYNOPSIS
       git clone [--template=<template-directory>] [-l] [-s] [--no-hardlinks]
                 [-q] [-n] [--bare] [-o <name>] [--depth <depth>]
                 [--filter=<filter-spec>] [--] <repository> [<directory>]

DESCRIPTION
       Clones a repository into a newly created directory.

OPTIONS
       -l, --local
           When the repository to clone from is on a local machine, this
           flag bypasses the normal "Git aware" transport mechanism. Don't
           use it unless you know what you're doing.

       --bare
           Make a bare Git repository. That is, instead of creating
           <directory> and placing the administrative files in
           <directory>/.git, make the <directory> itself the $GIT_DIR.

       --depth <depth>
           Create a shallow clone with a history truncated to the specified
           number of commits.
"""


def test_git_manual_options_and_single_form_synopsis():
    verb = _toolhelp.parse_help(GIT_MAN, name="clone", man=True)
    got = flags(verb)
    assert {"local", "bare", "depth"} <= set(got)
    assert got["depth"].type_name == "str"  # `--depth <depth>` takes a value
    # A single-form SYNOPSIS with a required trailing metavar → required.
    assert (verb.positional, verb.lead) == ("required", "repository")


def test_git_manual_help_is_the_first_sentence_ascii_folded():
    verb = _toolhelp.parse_help(GIT_MAN, name="clone", man=True)
    got = flags(verb)
    # The manual's paragraph is cut to one sentence, curly quotes folded.
    assert got["local"].help == (
        "When the repository to clone from is on a local machine, this flag "
        'bypasses the normal "Git aware" transport mechanism'
    )
    assert got["local"].help.isascii()  # curly quotes were folded


def test_multi_form_synopsis_stays_any():
    text = (
        "SYNOPSIS\n"
        "       git checkout [<options>] <branch>\n"
        "       git checkout [<options>] [--] <pathspec>...\n"
        "\nDESCRIPTION\n       x.\n\n"
        "OPTIONS\n       -q, --quiet\n           Be quiet.\n"
    )
    verb = _toolhelp.parse_help(text, name="checkout", man=True)
    assert verb.positional == "any"  # two forms → no single shape


def test_first_sentence_skips_abbreviations():
    assert _toolhelp._first_sentence("Use e.g. a value. Then stop.") == (
        "Use e.g. a value"
    )
    assert _toolhelp._first_sentence("No period here") == "No period here"


def test_reserved_flag_name_falls_through_to_the_catchall():
    # git rev-parse has a `--flags` option; it can't be a typed parameter
    # (it would duplicate `**flags`), so it is dropped to the catch-all.
    spec = ToolSpec(
        name="git",
        verbs=(
            Verb(
                name="rev_parse",
                options=(
                    Option("flags", ("--flags",), type_name="bool"),
                    Option("quiet", ("--quiet",), type_name="bool"),
                ),
            ),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)  # a duplicate `flags` parameter would be a syntax error
    assert "quiet: _Flag" in text
    assert "flags: _Flag" not in text  # the `--flags` option isn't a typed param
    assert "**flags: Any" in text  # it falls through to the catch-all


def test_arg_help_escapes_a_markdown_header_at_a_wrapped_line_start():
    # git's merge-stage notation (`#2 (ours)`) would render as an H1 in the
    # reference page if a wrap dropped it to the start of a docstring line.
    from footman._toolspec import Option

    option = Option(
        "ours",
        ("--ours",),
        type_name="bool",
        help=(
            "When restoring files in the working tree from the index, use stage "
            "#2 (ours) or #3 (theirs) for unmerged paths"
        ),
    )
    lines = _stubgen._arg_lines(option)
    for line in lines:
        assert not line.lstrip().startswith("#"), line  # never a bare header
    # The escape is a *double* backslash: this is docstring source, where a
    # lone `\#` is an invalid Python escape sequence.
    assert any("\\\\#" in line for line in lines)
    # And the whole thing still parses as Python without a SyntaxWarning.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        ast.parse('def f():\n    """\n' + "\n".join(lines) + '\n    """\n')


def test_md_safe_touches_only_leading_header_and_quote():
    safe = _stubgen._md_safe(
        ["            #2 heading", "            > quote", "            mid # hash"]
    )
    assert safe[0].endswith("\\\\#2 heading")
    assert safe[1].endswith("\\\\> quote")
    assert safe[2].endswith("mid # hash")  # a mid-line hash is not a block
