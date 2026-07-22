# Testing your tasks

Tasks are code, so they deserve tests — and a task runner that makes you
choose between "run it for real" and "don't test it" hasn't finished its job.
footman gives you three altitudes, each a thin layer over the previous one.
Everything on this page is stdlib-only footman; the pytest fixtures at the
end auto-load when footman and pytest share an environment.

## Tasks are just functions

`@task` returns your function untouched — no wrapper, no argparse object. The
first altitude of testing is therefore plain Python:

```python
from tasks import lint

def test_lint_accepts_the_flag():
    lint(fix=True)   # a normal call, normal semantics
```

That covers logic, but any `run()` inside the body **really executes**. For
commands, you usually want the next altitude instead.

## Assert commands, don't run them

`recording()` captures every command a block would run — silently, executing
nothing — and hands you the steps to assert on:

```python
from footman.testing import recording
from tasks import release

def test_release_tags_and_pushes():
    with recording() as steps:
        release("1.2.0", push=True)
    assert [s.command for s in steps] == [
        "git tag v1.2.0",
        "git push --tags",
    ]
```

This works for the tool wrappers too — every one funnels through `run()`. One
caveat, stated out loud: a Python *callable* passed to `run(fn)` is also
skipped under recording — that is the point, but remember it when a task
mixes subprocesses with in-process work.

Under the hood this is `Context(dry_run=True, quiet=True)` installed with
`use_context()` — both public, so you can compose your own variants:

```python
from footman import Context, use_context

with use_context(Context(env={"CI": "1"})) as ctx:
    deploy()                      # runs for real, with CI=1 in its env
assert ctx.steps[-1].code == 0
```

## Drive the CLI

`Runner.invoke` runs a full command line in-process — globals, chaining,
taught errors, exit codes — and captures everything:

```python
from footman.testing import Runner

def test_the_check_pipeline(tmp_path):
    result = Runner().invoke("--dry-run format lint --fix test", cwd=tmp_path)
    assert result.ok
    assert "lint" in result.stdout
```

`Result` carries `exit_code`, `stdout`, `stderr`, the structured
`results: list[TaskResult]` (one per executed task, dependency order), and an
`ok` shorthand. Each `TaskResult` exposes the task's return value as
`.returned` — the same value `--json` publishes — so asserting on a task's
data needs no JSON parsing at all. Taught errors land in `result.stderr` with exit code 2 —
assert on them like any other product surface. The completion cache is
isolated per invocation automatically, so tests never touch your real one.

Point it at a task surface three ways:

```python
Runner().invoke("build", cwd=project_dir)          # normal cascade discovery
Runner().invoke("build", tasks=Path("ci/tasks.py"))  # one file (--tasks-file)
Runner().invoke("build", tasks=my_group)           # an in-memory Group, no files
```

## The pytest fixtures

Installing footman next to pytest auto-loads three fixtures (`pytest11`
entry point — nothing to enable, and pytest is never a footman dependency):

```python
def test_release_dry(fm_project):
    fm = fm_project("""
        from footman import task, run

        @task
        def release(version: str, push: bool = False):
            "Tag and optionally push."
            run(f"git tag v{version}")
            if push:
                run("git push --tags")
    """)
    result = fm.invoke("--dry-run release 1.2.0 --push")
    assert result.ok

def test_release_records_the_tag(fm_record):
    from tasks import release
    release("1.2.0")
    assert fm_record[0].command == "git tag v1.2.0"
    assert len(fm_record) == 1     # --push not given: no push
```

- **`fm`** — a `Runner` for the project the tests run in.
- **`fm_project(source, name="tasks.py")`** — scaffold an isolated project
  in `tmp_path` from a tasks-file string and return its `Runner`.
- **`fm_record`** — a recording context for the whole test; steps append as
  task code runs.

footman's own suite uses these fixtures and `Runner` — the harness tests the
framework that ships it, which is the strongest claim a testing story can
make about itself.

## Golden tests: the `--json` surface

`--json` is the blessed machine surface: `{"schema": 1, "results": [...]}`,
documented in full on [JSON output](json.md) and additive-only after 1.0.
Filter the volatile fields and snapshot the shape:

```python
import json

def test_check_pipeline_shape(fm):
    payload = json.loads(fm.invoke("--json check").stdout)
    shape = [
        (t["task"], t["ok"], [s["command"] for s in t["steps"]])
        for t in payload["results"]
    ]
    assert shape == [
        ("lint", True, ["ruff check ."]),
        ("test", True, ["pytest -q"]),
    ]
```

`--dry-run` output stays human-oriented — snapshot it within a pinned version
if you like, but there is no cross-version promise there.

## Testing a branded CLI

A custom `App` tests exactly like `fm` — hand it to the `Runner` and every
user-facing string carries your brand, including the error prefix:

```python
from footman import App
from footman.testing import Runner

def test_acme_teaches_with_its_own_name(tmp_path):
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("nope", cwd=tmp_path)
    assert result.stderr.startswith("acme:")
```

## CI notes

- Cache isolation is automatic — parallel test runs can't fight over the
  completion manifest.
- `Runner.invoke` never raises on task failure; the code is in the `Result`.
  `KeyboardInterrupt` passes through, as it should.
- Chained/parallel semantics (`-s`, `-k`) work through `invoke` exactly as on
  the real command line — test the orchestration you actually run in CI.
