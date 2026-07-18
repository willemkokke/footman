"""The run context: run() (subprocess/in-process), ctx injection, tools."""

from __future__ import annotations

import io
import sys

import pytest

from footman import manifest, tools
from footman.context import Context, RunFailed, passthrough, run
from footman.executor import run_chain
from footman.registry import Group
from footman.split import split_chain


def drive(build, line, **cfg):
    reg = Group("root")
    build(reg)
    tree = manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return reg, tree, run_chain(reg, segments, ctx_config=cfg)


# --- run() -------------------------------------------------------------------


def test_run_subprocess_records_step():
    def tasks(reg):
        @reg.task
        def build():
            run("echo hi")

    _, _, results = drive(tasks, "build")
    assert results[0].ok
    step = results[0].steps[0]
    assert step.command == "echo hi" and step.code == 0
    assert step.output.strip() == "hi"


def test_run_in_process_callable_captured():
    out = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                print("in-process")
                return 0

            out["code"] = run(tool)

    _, _, results = drive(tasks, "go")
    assert out["code"] == 0
    assert results[0].steps[0].output.strip() == "in-process"


def test_run_failed_raises_and_fails_task():
    def tasks(reg):
        @reg.task
        def build():
            run("false")

    _, _, results = drive(tasks, "build")
    assert results[0].ok is False
    assert isinstance(results[0].error, RunFailed)


def test_run_failure_propagates_command_code():
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", "import sys; sys.exit(3)"])

    _, _, results = drive(tasks, "build")
    assert results[0].ok is False
    assert results[0].code == 3  # the command's own code, not a flat 1
    assert isinstance(results[0].error, RunFailed)


def test_run_nofail_returns_code():
    out = {}

    def tasks(reg):
        @reg.task
        def build():
            out["code"] = run("false", nofail=True)

    _, _, results = drive(tasks, "build")
    assert results[0].ok is True
    assert out["code"] == 1


# --- output routing ----------------------------------------------------------


def test_in_process_stderr_is_captured():
    def tasks(reg):
        @reg.task
        def build():
            def tool():
                print("to stdout")
                print("to stderr", file=sys.stderr)
                return 0

            run(tool)

    _, _, results = drive(tasks, "build")
    step = results[0].steps[0]
    assert "to stdout" in step.output
    assert "to stderr" in step.output  # stderr now merges into the capture


def test_routing_is_reentrant():
    import footman.context as ctxmod

    with ctxmod.routing():
        outer = ctxmod._router
        assert outer is not None
        with ctxmod.routing():
            assert ctxmod._router is not None and ctxmod._router is not outer
        assert ctxmod._router is outer  # nested exit restores, not clears
        assert sys.stdout is outer
    assert ctxmod._router is None


def test_non_ascii_status_survives_cp1252_stdout(monkeypatch):
    wrapper = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", wrapper)

    def tasks(reg):
        @reg.task
        def build():
            run("echo hi")  # run() writes the "→" glyph, absent from cp1252

    _, _, results = drive(tasks, "build")
    assert results[0].ok  # reconfigure(errors='replace') -> no UnicodeEncodeError


def test_subprocess_output_decoded_as_utf8():
    src = "import sys; sys.stdout.buffer.write('résumé ✓\\n'.encode('utf-8'))"

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", src])

    _, _, results = drive(tasks, "build")
    assert "résumé ✓" in results[0].steps[0].output


def test_subprocess_encoding_override():
    src = "import sys; sys.stdout.buffer.write(b'caf\\xe9\\n')"  # latin-1 é

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", src], encoding="latin-1")

    _, _, results = drive(tasks, "build")
    assert "café" in results[0].steps[0].output


def test_dry_run_prints_not_executes(capsys):
    def tasks(reg):
        @reg.task
        def build():
            run("echo SHOULD-NOT-RUN")

    _, _, results = drive(tasks, "build", dry_run=True)
    assert "$ echo SHOULD-NOT-RUN" in capsys.readouterr().out
    # Not executed, but recorded — dry-run steps are the testing surface.
    assert [s.command for s in results[0].steps] == ["echo SHOULD-NOT-RUN"]
    assert results[0].steps[0].code == 0


def test_passthrough_accessor():
    seen = {}

    def tasks(reg):
        @reg.task
        def test():
            seen["pt"] = passthrough()

    drive(tasks, "test -- -k foo -x")
    assert seen["pt"] == ["-k", "foo", "-x"]


# --- opt-in ctx injection ----------------------------------------------------


def test_ctx_injected_and_not_a_cli_param():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(ctx: Context, target: str = "prod"):
            seen["ctx"] = ctx
            seen["target"] = target

    _, tree, _ = drive(tasks, "deploy --target staging")
    assert [p["name"] for p in tree["tasks"]["deploy"]["params"]] == ["target"]
    assert isinstance(seen["ctx"], Context)
    assert seen["target"] == "staging"


def test_ctx_by_bare_name():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(ctx):
            seen["ctx"] = ctx

    _, tree, _ = drive(tasks, "go")
    assert tree["tasks"]["go"]["params"] == []  # ctx skipped entirely
    assert isinstance(seen["ctx"], Context)


# --- tools -------------------------------------------------------------------


def test_tools_sh_runs():
    def tasks(reg):
        @reg.task
        def go():
            tools.sh("echo tool-ran")

    _, _, results = drive(tasks, "go")
    assert results[0].steps[0].output.strip() == "tool-ran"


@pytest.mark.parametrize(
    "make, expected",
    [
        (lambda: tools.ruff("check", "src", fix=True), "ruff check src --fix"),
        (lambda: tools.ruff_format("src", check=True), "ruff format src --check"),
        (lambda: tools.basedpyright("src"), "basedpyright src"),
        (lambda: tools.uv("build"), "uv build"),
        (lambda: tools.pytest("-x", in_process=False), "pytest -x"),
        (lambda: tools.pytest("-x"), "pytest -x"),  # in-process, via title
    ],
)
def test_tools_build_commands(make, expected, capsys):
    def tasks(reg):
        @reg.task
        def go():
            make()

    drive(tasks, "go", dry_run=True)
    assert expected in capsys.readouterr().out


def test_tools_python_uses_interpreter(capsys):
    def tasks(reg):
        @reg.task
        def go():
            tools.python("-V")

    drive(tasks, "go", dry_run=True)
    out = capsys.readouterr().out
    assert "-V" in out and sys.executable in out


# --- robustness edges ---------------------------------------------------------


def test_non_utf8_subprocess_output_does_not_crash():
    def tasks(reg):
        @reg.task
        def emit():
            run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'\\xff ok\\n')",
                ]
            )

    _, _, results = drive(tasks, "emit")
    assert results[0].ok
    assert "ok" in results[0].steps[0].output  # decoded with replacement, not a crash


def test_windows_string_commands_are_not_shlex_split(monkeypatch):
    from footman import context as context_mod

    calls = {}

    def fake_run(argv, env, cwd, capture, encoding="utf-8"):
        calls["argv"] = argv
        return 0, ""

    monkeypatch.setattr(context_mod, "_run_subprocess", fake_run)
    monkeypatch.setattr(sys, "platform", "win32")

    def tasks(reg):
        @reg.task
        def copy():
            run(r"copy C:\tools\a.txt dest")

    drive(tasks, "copy")
    # On Windows the command line is one string (CreateProcess); shlex would
    # have eaten the backslashes.
    assert calls["argv"] == r"copy C:\tools\a.txt dest"


def test_dry_run_quiet_is_silent_capture(capsys):
    def tasks(reg):
        @reg.task
        def build():
            run("echo NOPE")

    _, _, results = drive(tasks, "build", dry_run=True, quiet=True)
    assert capsys.readouterr().out == ""
    assert [s.command for s in results[0].steps] == ["echo NOPE"]
