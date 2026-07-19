"""The run context: run() (subprocess/in-process), ctx injection, tools."""

from __future__ import annotations

import io
import os
import sys

import pytest

from footman import manifest, tools
from footman.context import Context, RunFailed, parallel, passthrough, run, use_context
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


def test_run_callable_capture_false_is_live_not_buffered(capsys):
    # F60: capture=False streams the callable's output live instead of buffering
    # it into the step — serve-style tasks must not buffer unboundedly.
    def tasks(reg):
        @reg.task
        def serve():
            def tool():
                print("live-line")
                return 0

            run(tool, capture=False)

    _, _, results = drive(tasks, "serve")
    assert "live-line" in capsys.readouterr().out  # went live to stdout
    assert results[0].steps[0].output == ""  # nothing captured into the step


def test_run_callable_honors_cwd(tmp_path):
    # F17: an in-process callable runs from the given cwd, like the subprocess
    # branch of the same call already does.
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["cwd"] = os.getcwd()
                return 0

            run(tool, cwd=tmp_path)

    drive(tasks, "go")
    assert seen["cwd"] == str(tmp_path.resolve())  # macOS /tmp is a symlink


def test_run_callable_honors_env_overlay(monkeypatch):
    # F17: os.environ is visible plus the call's env overlay, for callables too.
    monkeypatch.setenv("BASE", "base")
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["env"] = (os.environ.get("BASE"), os.environ.get("EXTRA"))
                return 0

            run(tool, env={"EXTRA": "extra"})

    drive(tasks, "go")
    assert seen["env"] == ("base", "extra")


def test_run_callable_restores_cwd_and_env(tmp_path, monkeypatch):
    # The process-global patch is undone on exit — no leak into the next task.
    monkeypatch.delenv("EXTRA", raising=False)
    before = os.getcwd()

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                return 0

            run(tool, cwd=tmp_path, env={"EXTRA": "x"})

    drive(tasks, "go")
    assert os.getcwd() == before
    assert "EXTRA" not in os.environ


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


# --- env / cwd propagation (subprocess) --------------------------------------
# F40: the env merge and cwd threading are load-bearing but were completely
# unasserted — dropping `ctx.env` entirely left the suite green. Observe them
# through a real subprocess.

_PRINT_CWD = "import os; print(os.getcwd())"
_PRINT_PREC = "import os; print(os.environ['PREC'])"


def test_subprocess_ctx_env_beats_os_environ(monkeypatch):
    monkeypatch.setenv("PREC", "from-os")

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_PREC])

    _, _, results = drive(tasks, "build", env={"PREC": "from-ctx"})
    assert results[0].steps[0].output.strip() == "from-ctx"


def test_subprocess_call_env_beats_ctx_env(monkeypatch):
    monkeypatch.setenv("PREC", "from-os")

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_PREC], env={"PREC": "from-kwarg"})

    # kwarg > ctx.env > os.environ, top to bottom.
    _, _, results = drive(tasks, "build", env={"PREC": "from-ctx"})
    assert results[0].steps[0].output.strip() == "from-kwarg"


def test_subprocess_cwd_via_kwarg(tmp_path):
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_CWD], cwd=tmp_path)

    _, _, results = drive(tasks, "build")
    assert results[0].steps[0].output.strip() == str(tmp_path.resolve())


def test_subprocess_cwd_via_ctx(tmp_path):
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_CWD])

    _, _, results = drive(tasks, "build", cwd=tmp_path)
    assert results[0].steps[0].output.strip() == str(tmp_path.resolve())


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


def test_parallel_honours_the_sequential_request():
    # -s reaches inside tasks: under a sequential context, parallel() runs
    # its calls one at a time, in submission order — no overlap at all.
    import time as _time

    order: list[str] = []

    def slow():
        order.append("slow-start")
        _time.sleep(0.05)
        order.append("slow-end")

    def fast():
        order.append("fast-start")

    with use_context(Context(sequential=True)):
        assert parallel(slow, fast) == [0, 0]
    assert order == ["slow-start", "slow-end", "fast-start"]

    # And without the request, the calls genuinely overlap.
    order.clear()
    with use_context(Context()):
        parallel(slow, fast)
    assert order.index("fast-start") < order.index("slow-end")

    # -j caps the pool the same way: width one behaves like sequential.
    order.clear()
    with use_context(Context(jobs=1)):
        parallel(slow, fast)
    assert order == ["slow-start", "slow-end", "fast-start"]


def test_step_lines_carry_an_aligned_name_column(capsys):
    # mark · task name (padded to the widest sibling) · command · (time).
    def tasks(reg):
        @reg.task
        def go():
            run("echo hi")

        @reg.task
        def longer():
            run("echo ho")

    drive(tasks, "go longer")
    out = capsys.readouterr().out
    assert "ok   go      echo hi  (0.0s)" in out  # padded to len("longer")
    assert "ok   longer  echo ho  (0.0s)" in out
