# Cookbook

Recipes, not reference — each one is a real shape you can paste and bend.
They assume the [getting started](getting-started.md) basics: tasks are
typed functions in `tasks.py`, `run()` executes commands, and the CLI is
derived from the signatures.

## The gate

Every repo deserves one command that answers "is this fine?". Give the
independent checks to `parallel()` and let the machine use its cores:

```python
import functools
from footman import task, parallel, tools

@task
def lint(fix: bool = False):
    "Lint with ruff."
    tools.ruff.check("src", "tests", fix=fix)

@task
def typecheck():
    "Type-check with basedpyright."
    tools.basedpyright()

@task
def test(*pytest_args: str):
    "Run the test suite."
    tools.pytest(*pytest_args)

@task
def check():
    "Lint, typecheck, and test — in parallel."
    # partial, not a lambda: it keeps the callee's name, so the live
    # status line and the step column say "lint" instead of "…".
    parallel(functools.partial(lint, fix=False), typecheck, test)
```

`fm check` fans out across cores, keeps every task's output in one
uninterleaved block, and — once it has seen a few runs — shows a progress
bar that actually knows how long your gate takes. Wire it into CI as-is:
the same command, the same exit codes.

## Hand a tool its own flags

A `*args` parameter receives everything after `--`, verbatim — no
quoting gymnastics, no flag collisions with footman's own:

```python
@task
def test(*pytest_args: str):
    "Run the test suite."
    tools.pytest(*pytest_args)
```

```console
$ fm test -- -k "grammar and not slow" -x --lf
```

Anything before `--` still belongs to footman (`fm -q test -- -x`), so
both grammars stay whole. A task can also read the raw list itself with
`footman.passthrough()`.

## One chain, each task with its own flags

Options bind to the task named just before them — chains need no
separators:

```console
$ fm format lint --fix test
```

`--fix` is lint's, because it follows `lint`. Independent tasks in a
chain run in parallel by default; `-s` serialises the whole run (and
reaches `parallel()` calls inside task bodies too), `-k` keeps going past
failures, `-j 2` caps the width.

## Choices that teach

A `Literal` is a validated choice list, a completion menu, and a
did-you-mean in one annotation:

```python
from typing import Literal

@task
def deploy(target: Literal["dev", "staging", "prod"]):
    "Ship to an environment."
```

```console
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
```

Exit code 2, nothing executed, and the fix is in the message.

## The belt-and-braces deploy

Markers stack. Each one validates eagerly — before anything runs — and
each failure is a taught error, not a traceback:

```python
from pathlib import Path
from typing import Annotated
from footman import task, run
from footman.params import between, check, env, isfile

def semver(value: str) -> None:
    import re
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {value!r}")

@task
def deploy(
    config: Annotated[Path, isfile],
    version: Annotated[str, check(semver)],
    workers: Annotated[int, between(1, 32)] = 4,
    target: Annotated[str, env("DEPLOY_ENV")] = "staging",
):
    "Roll out."
    run(f"./rollout.sh {target} {version} --config {config} -j {workers}")
```

`config` must name an existing file; `version` goes through your own
validator (raise `ValueError` with a message written for the person at
the prompt); `workers` is bounds-checked; and `target` falls back to
`$DEPLOY_ENV` before its default — CI sets the variable, humans say
`--target prod`, and both flow through the same validation.

## TAB completes your git branches

`suggest()` attaches a completer that runs on the execution path — its
results are cached into the manifest, so <kbd>Tab</kbd> stays instant
while offering live values:

```python
import subprocess
from typing import Annotated
from footman import task, run
from footman.params import suggest

def branches() -> list[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True, text=True,
    )
    return out.stdout.split()

@task
def review(branch: Annotated[str, suggest(branches)]):
    "Check out and gate a branch."
    run(f"git switch {branch}")
    run("fm check")
```

`fm review <TAB>` offers real branches. `suggest` is strict by default:
a typo'd branch is refused against a *fresh* call — pass
`suggest(branches, strict=False)` when the values are hints, not law.

## KEY=VALUE options

A `dict` parameter speaks the `--name KEY=VALUE` dialect, repeatable,
with taught errors for malformed pairs:

```python
@task
def image(tag: str, build_args: dict[str, str] | None = None):
    "Build the container image."
    tools.docker.build(".", tag=tag, build_arg=[
        f"{k}={v}" for k, v in (build_args or {}).items()
    ])
```

```console
$ fm image v3 --build-args PYTHON=3.13 --build-args DEBIAN=trixie
```

## Variadic in front, required option behind

A keyword-only parameter (after `*`) is an option — and without a
default, a *required* one. So a task can take an open list of inputs
positionally and still demand a named output:

```python
from pathlib import Path

@task
def bundle(*entries: str, out: Path):
    "Bundle entry points into one artifact."
    run(f"./bundle.sh {' '.join(entries)} -o {out}")
```

```console
$ fm bundle web api worker --out dist/app.tar
$ fm bundle web
fm: bundle: missing required option --out
```

## Dependencies that dedup

`pre` and `post` build a DAG; a dependency shared by several tasks runs
once per invocation:

```python
@task
def proto():
    "Generate protobuf stubs."
    run("buf generate")

@task(pre=[proto])
def build():
    "Compile the service."
    run("cargo build --release")

@task(pre=[proto])
def docs():
    "Render the API docs."
    run("./render-docs.sh")

@task(pre=[build, docs], post=[lambda: run("./notify.sh done")])
def release():
    "The whole train."
```

`fm build docs` runs `proto` exactly once, then both dependents in
parallel. A failed dependency skips its dependents loudly — never
silently, because a `check` that quietly dropped `lint` is how CI learns
to lie.

## A build matrix

Thunks let you fan the same task over arguments; `keep_going` collects
every failure instead of stopping at the first:

```python
TARGETS = ("linux-x86_64", "linux-arm64", "darwin-arm64")

@task
def build(target: str):
    "Compile one target."
    run(f"cargo zigbuild --target {target}")

@task
def matrix():
    "Compile every target."
    codes = parallel(
        *(functools.partial(build, t) for t in TARGETS),
        keep_going=True,
    )
    if any(codes):
        raise SystemExit(1)
```

`-j` caps the fan-out's width from the command line; the timing history
keys on it, so `-j2` runs learn their own duration.

## An endless dev server

Some tasks end when you say so, not when they finish. Opt them out of
timing so the progress machinery never waits for a duration that isn't
coming:

```python
@task(progress=False)
def serve(port: int = 8000):
    "Run the dev server until Ctrl-C."
    run(f"uvicorn app:api --reload --port {port}")
```

Ctrl-C cancels cleanly: the run reports `interrupted` and exits 130 —
no traceback.

## Tools you never declared

Every executable on PATH is already a tool. Attribute access chains
subcommands; keyword arguments translate mechanically (`detach=True` →
`--detach`, lists repeat, trailing `_` escapes Python keywords):

```python
@task
def up(detach: bool = True):
    "Start the stack."
    tools.docker.compose.up(detach=detach)

@task
def plan(out: str = "tf.plan"):
    "Terraform plan, saved."
    tools.terraform.plan(out=out, input_=False)

@task
def site():
    "Build the docs."
    tools.mkdocs.build(strict=True)   # in-process: no interpreter spawn
```

Two extras worth knowing: `tools.<name>.installed_version()` for the
rare version-dependent branch, and the `off` sentinel
(`strict=tools.off` → `--no-strict`) for negating a flag a tool turns on
by default. And on macOS, in-process is sometimes the only *correct*
option — SIP strips `DYLD_*` from subprocesses, so a tool needing
Homebrew's native libraries only works inside the process.

## Monorepo: root gate, leaf overrides

`tasks.py` files cascade from the repo root down to wherever you stand;
nearer definitions win, and every task runs from the folder that defined
it:

```text
repo/
  tasks.py            # check, format, release — the shared surface
  svc/api/tasks.py    # serve, plus its own `check` override
  tools/legacy/footman.toml   # `uv = false`: run in the parent's env
```

From `svc/api`, `fm check` is the override; `fm -C ../.. check` is the
root's. A deep folder can adjust behaviour with a two-line
`footman.toml` — the [configuration ladder](configuration.md) reaches
everywhere the cascade does.

## Tasks that return data

Return a dict and `--json` carries it verbatim under `returned` — your
task's own machine surface, no printing-and-parsing:

```python
@task
def coverage() -> dict:
    "Measure test coverage."
    run("pytest --cov=app --cov-report=json -q")
    import json
    percent = json.load(open("coverage.json"))["totals"]["percent_covered"]
    return {"percent": round(percent, 2)}
```

```console
$ fm --json coverage | jq '.results[0].returned.percent'
94.2
$ fm --json coverage | jq -e '.results[0].returned.percent >= 90' > /dev/null \
    || echo "coverage regression"
```

`Path`, `Enum`, `datetime`, `UUID`, `Decimal`, dataclasses, and sets all
serialise symmetrically with what footman coerces in; an `int` return
stays what it always was — an exit code.

## The coding-agent loop

footman treats agents as first-class users, and the loop is the same one
you'd teach a new colleague — discover, validate, run, read the receipt:

```console
fm --json --list                 # the full catalog: tasks, params, types, docs
fm --json --dry-run deploy prod  # parse-check a line without running it
fm --json deploy prod            # one envelope: results, output, returned
```

A refusal is machine-readable too — `{"error": {"code": 2, "message":
"…did you mean 'prod'?"}}` — so an agent can read the fix out of the
message the same way a human does. Two hooks close the loop for Claude
Code (this repo runs both on the agent that builds footman itself): an
edit-time hook running `fm format lint` after every Python edit, and a
stop-gate refusing to let the session end until `fm check` passes. The
paste-ready versions live on the [AI agents](agents.md) page, next to
the `CLAUDE.md` snippet that teaches the grammar in six lines.

## Test your tasks like code

Tasks are plain functions, so plain calls already work. `recording()`
asserts *which commands would run* without running them, and the pytest
fixtures scaffold whole projects:

```python
from footman import recording
from tasks import deploy

def test_deploy_passes_the_workers_flag():
    with recording() as steps:
        deploy(config="app.toml", version="1.2.3", workers=8)
    assert steps[0].command.endswith("-j 8")

def test_release_refuses_bad_versions(fm_project):
    fm = fm_project('''
        from typing import Annotated
        from footman import task
        from footman.params import check

        def semver(v):
            import re
            if not re.fullmatch(r"\\d+\\.\\d+\\.\\d+", v):
                raise ValueError("expected MAJOR.MINOR.PATCH")

        @task
        def release(version: Annotated[str, check(semver)]): ...
    ''')
    result = fm.invoke("release not-a-version")
    assert result.exit_code == 2
    assert "MAJOR.MINOR.PATCH" in result.stderr
```

The `fm`, `fm_project`, and `fm_record` fixtures auto-load — pytest is
never a footman dependency; only pytest itself imports the plugin. The
whole story: [Testing your tasks](testing.md).

## Ship your own CLI

A branded tool is footman with your name on it — same grammar, same
completion, same docs machinery, answering as itself:

```python
# acme_cli.py
from footman import App

app = App(name="Acme", prog="acme", version="1.4.0")

def main() -> None:
    raise SystemExit(app.run())
```

```toml
[project.scripts]
acme = "acme_cli:main"
```

`acme --install-completion` installs for `acme`; errors say `acme:`; and
`acme footman docs page` documents *your* task surface under *your*
prog. The details: [Custom CLI](custom-cli.md).
