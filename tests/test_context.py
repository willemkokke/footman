"""The run context: run() (subprocess/in-process), ctx injection, tools."""

from __future__ import annotations

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


def test_run_nofail_returns_code():
    out = {}

    def tasks(reg):
        @reg.task
        def build():
            out["code"] = run("false", nofail=True)

    _, _, results = drive(tasks, "build")
    assert results[0].ok is True
    assert out["code"] == 1


def test_dry_run_prints_not_executes(capsys):
    def tasks(reg):
        @reg.task
        def build():
            run("echo SHOULD-NOT-RUN")

    _, _, results = drive(tasks, "build", dry_run=True)
    assert "$ echo SHOULD-NOT-RUN" in capsys.readouterr().out
    assert results[0].steps == []


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
