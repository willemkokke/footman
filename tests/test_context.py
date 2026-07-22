"""The run context: run() (subprocess/in-process), ctx injection, tools."""

from __future__ import annotations

import io
import os
import sys
from typing import Annotated, Literal

import pytest

from footman import manifest, tools
from footman.context import Context, RunFailed, parallel, passthrough, run, use_context
from footman.executor import run_chain
from footman.params import ask
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


def test_run_string_command():
    # A command as a single string is `run(...)` (footman splits and runs it,
    # no shell) — there is no `tools.sh`.
    def tasks(reg):
        @reg.task
        def go():
            run("echo tool-ran")

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


def test_tools_python_uses_interpreter():
    # `tools.python` shows the clean name `python` but runs `sys.executable`.
    def tasks(reg):
        @reg.task
        def go():
            tools.python("-V")

    _, _, results = drive(tasks, "go", dry_run=True)
    step = results[0].steps[0]
    assert step.command == "python -V"  # the name is what's shown
    assert sys.executable in step.raw  # sys.executable is what actually runs


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

    def fake_run(
        argv, env, cwd, capture, encoding="utf-8", killable=True, isolate=True
    ):
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

    # And without the request, the calls genuinely overlap — proven by
    # construction, not by racing a sleep against a loaded runner: both
    # thunks must reach the barrier at once, which single-file execution
    # never can (a regression trips the timeout instead).
    import threading

    barrier = threading.Barrier(2, timeout=3)

    def hit():
        barrier.wait()

    with use_context(Context()):
        assert parallel(hit, hit) == [0, 0]

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
    # Padded to len("longer"); the duration digit varies with the machine.
    assert "ok   go      echo hi  (0." in out
    assert "ok   longer  echo ho  (0." in out


def test_progress_and_track_report_to_the_status_line():
    from footman import progress, track
    from footman.context import Context, set_status, use_context

    class FakeStatus:
        def __init__(self):
            self.reports = []
            self.counted = {}

        def unit_counted(self, name, done, total):
            self.reports.append((name, done, total))
            self.counted[name] = (done, total)

        def paint(self):
            pass

    status = FakeStatus()
    set_status(status)
    try:
        with use_context(Context(task="migrate")):
            progress(3, 10)
            assert status.reports[-1] == ("migrate", 3, 10)
            assert list(track(["a", "b"])) == ["a", "b"]
    finally:
        set_status(None)
    # track() reported each step, then cleared on the way out
    assert ("migrate", 1, 2) in status.reports
    assert ("migrate", 2, 2) in status.reports
    assert status.counted == {}


def test_progress_outside_a_run_is_a_noop():
    from footman import progress, track

    progress(1, 2)  # no status line: costs nothing, raises nothing
    assert list(track([1, 2, 3])) == [1, 2, 3]


# --- interactive prompts (prompt / confirm / select) -------------------------


def test_prompt_off_a_terminal_uses_default_then_raises(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    # A default makes an unattended run deterministic instead of hung.
    assert context.prompt("name? ", default="Ada") == "Ada"
    # No default: fail loudly rather than block on input that never comes.
    with pytest.raises(RuntimeError, match=r"no terminal is attached"):
        context.prompt("name? ")


def test_prompt_reads_stdin_and_writes_the_prompt_to_stderr(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)
    out = io.StringIO()
    monkeypatch.setattr(context, "real_stdout", lambda: out)

    assert context.prompt("your name? ") == "Ada"
    # The prompt is commentary: it lands on stderr, never on captured stdout.
    assert err.getvalue() == "your name? "
    assert out.getvalue() == ""


def test_prompt_bypasses_the_capture_sink(monkeypatch):
    # Even when a task's stdout is captured (parallel/JSON), the prompt goes
    # to the real terminal, not into the buffer.
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("blue\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)

    sink = io.StringIO()
    with use_context(Context(sink=sink)):
        answer = context.prompt("colour? ")
    assert answer == "blue"
    assert sink.getvalue() == ""  # nothing leaked into the captured buffer
    assert "colour? " in err.getvalue()


def test_prompt_empty_line_falls_back_to_default(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))  # just Enter
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    assert context.prompt("branch? ", default="main") == "main"


def test_confirm_yes_no_and_default(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    def answer(text):
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
        return context.confirm("proceed?", default=False)

    assert answer("y\n") is True
    assert answer("yes\n") is True
    assert answer("n\n") is False
    assert answer("\n") is False  # Enter takes the default

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    assert context.confirm("proceed?", default=True) is True  # unattended → default


def test_interactive_primitives_are_guarded_in_a_plain_task():
    from footman import context

    # Inside a non-interactive task body the prompt would be swallowed by the
    # capture buffer — so it is a loud, taught error naming both fixes. (No
    # stdin/tty mocking needed: the guard raises before any input is read.)
    with use_context(Context(task="deploy", in_task=True, interactive=False)):
        with pytest.raises(RuntimeError, match=r"@task\(interactive=True\)"):
            context.prompt("x? ")
        with pytest.raises(RuntimeError, match=r"not interactive"):
            context.confirm("x?")
        with pytest.raises(RuntimeError, match=r"not interactive"):
            context.select("x?", ["a", "b"])


def test_interactive_primitives_allowed_in_an_interactive_task(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    # A task that owns the terminal may prompt mid-body.
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        assert context.prompt("name? ") == "Ada"


def test_no_input_refuses_to_prompt(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)  # even on a tty
    with use_context(Context(no_input=True)):
        assert context.prompt("x? ", default="d") == "d"  # a default still works
        with pytest.raises(RuntimeError, match=r"no-input"):
            context.prompt("x? ")
        assert context.confirm("ok?", default=True) is True  # answer is the default


def test_assume_yes_auto_confirms():
    from footman import context

    # --yes answers every confirm without reading stdin (none is provided).
    with use_context(Context(assume_yes=True)):
        assert context.confirm("ship it?", default=False) is True


def test_select_single_multiple_and_pairs(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    def pick(line, **kw):
        monkeypatch.setattr(sys, "stdin", io.StringIO(line))
        return context.select("pick", ["core", "cli", "docs"], **kw)

    assert pick("2\n") == "cli"  # single-select, 1-indexed
    assert pick("1,3\n", multiple=True) == ["core", "docs"]
    assert pick("all\n", multiple=True) == ["core", "cli", "docs"]
    assert pick("none\n", multiple=True) == []
    # (label, value) pairs show the label and return the value:
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    assert context.select("p", [("Core pkg", "core"), ("CLI", "cli")]) == "core"


def test_select_rejects_bad_input_and_degrades(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)

    monkeypatch.setattr(sys, "stdin", io.StringIO("x\n"))
    with pytest.raises(RuntimeError, match=r"not a number"):
        context.select("p", ["a", "b"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("9\n"))
    with pytest.raises(RuntimeError, match=r"out of range"):
        context.select("p", ["a", "b"])

    # Off a terminal: default, or a loud error.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    assert context.select("p", ["a", "b"], default="a") == "a"
    with pytest.raises(RuntimeError, match=r"no terminal|no-input"):
        context.select("p", ["a", "b"])


def test_prompt_guard_fires_in_a_real_run():
    from footman import context

    def build(reg):
        @reg.task
        def asks():
            context.prompt("name? ")  # illegal: not an interactive task

    _, _, results = drive(build, "asks")
    assert not results[0].ok
    assert "interactive" in str(results[0].error)


def test_interactive_task_may_prompt(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    captured = {}

    def build(reg):
        @reg.task(interactive=True)
        def wizard():
            captured["name"] = context.prompt("name? ")

    _, _, results = drive(build, "wizard")
    assert results[0].ok
    assert captured["name"] == "Ada"


# --- ask(): typed parameters that prompt -------------------------------------


def test_ask_prompts_a_required_param(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1.2.3\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "1.2.3"


def test_ask_cli_value_wins_over_the_prompt(monkeypatch):
    from footman import context

    # A value on the line means no prompt — the (wrong) stdin is never read.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("WRONG\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]):
            got["v"] = version

    _, _, results = drive(build, "release --version 9.9.9")
    assert results[0].ok
    assert got["v"] == "9.9.9"


def test_ask_default_short_circuits_the_prompt(monkeypatch):
    from footman import context

    # A default is the answer (CLI > env > default > prompt): no prompt fires.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("WRONG\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()] = "patch"):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "patch"


def test_ask_re_asks_on_a_bad_value(monkeypatch):
    from footman import context

    # A typed param re-asks until the answer coerces — "abc" then "5".
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc\n5\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def scale(replicas: Annotated[int, ask()]):
            got["n"] = replicas

    _, _, results = drive(build, "scale")
    assert results[0].ok
    assert got["n"] == 5


def test_ask_validates_a_literal_choice(monkeypatch):
    from footman import context

    # A Literal is a typed choice: "dev" is rejected, "prod" accepted.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("dev\nprod\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def deploy(env: Annotated[Literal["staging", "prod"], ask()]):
            got["e"] = env

    _, _, results = drive(build, "deploy")
    assert results[0].ok
    assert got["e"] == "prod"


def test_ask_off_a_terminal_fails_loudly(monkeypatch):
    from footman import context

    # No tty, no default: the required value can't be prompted, so the task
    # fails naming the flag rather than hanging.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]): ...

    _, _, results = drive(build, "release")
    assert not results[0].ok
    assert "--version is required" in str(results[0].error)


# --- @task(confirm=) gate -----------------------------------------------------


def test_confirm_gate_runs_when_confirmed(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    ran = {}

    def build(reg):
        @reg.task(confirm="deploy to prod?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")
    assert results[0].ok
    assert ran.get("it")


def test_confirm_gate_denied_skips_the_task(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    ran = {}

    def build(reg):
        @reg.task(confirm="deploy to prod?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")
    assert not results[0].ok
    assert "not confirmed" in str(results[0].error)
    assert not ran.get("it")  # the body never ran


def test_confirm_gate_yes_bypasses():
    ran = {}

    def build(reg):
        @reg.task(confirm="sure?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy", assume_yes=True)  # --yes
    assert results[0].ok
    assert ran.get("it")


def test_confirm_gate_off_a_terminal_denies(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    ran = {}

    def build(reg):
        @reg.task(confirm="sure?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")  # no --yes, no terminal → denied
    assert not results[0].ok
    assert not ran.get("it")


def test_select_scrubs_control_characters_in_labels(monkeypatch):
    from footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)

    # A label carrying an ANSI escape is neutralised before it reaches the tty.
    context.select("pick", ["\x1b[31mred\x1b[0m", "green"])
    assert "\x1b" not in err.getvalue()
    assert "red" in err.getvalue()  # the visible text survives
