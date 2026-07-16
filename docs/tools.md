# Running tools

Task bodies run tools through `run()` and the typed `tools.*` wrappers. `run()`
captures output and stays quiet on success, **replaying it only on failure** —
so a green run is calm and a red one shows exactly what broke:

```python
from footman import task, run, tools

@task
def check():
    tools.ruff("check", "src", fix=False)   # subprocess (ruff is a binary)
    tools.pytest("-x")                        # in-process via pytest.main
    run("mkdocs build --strict")              # any command; a callable also works
```

## `run()`

- Takes a command (string or list) or a Python callable.
- Raises on a non-zero exit; `nofail=True` returns the code instead.
- Honours `--dry-run` (prints the command instead of running it).
- Records a step for `--json` (command, code, duration, captured output).
- Defaults the working directory to the task's context cwd — in a
  [cascade](monorepos.md) that is the folder the task was defined in.

## The `tools.*` wrappers

`footman.tools` ships typed, autocompletable wrappers built on `run()`:

- **In-process where possible** — Python-native tools (pytest) skip the process
  spawn; binaries (ruff, basedpyright, uv) run as subprocesses. Either way the
  wrapper gives you typed options and a typo-proof command line.

## No `ctx` needed

`run()` and `passthrough()` read the current task's context implicitly, so a
task body stays boilerplate-free. Declare a first `ctx: Context` parameter only
if you want the object — footman keeps it out of the CLI mapping:

```python
from footman import Context, task, tools

@task
def test(ctx: Context):
    tools.pytest(*ctx.passthrough)          # fm test -- -k mytest -x
```

## Machine-readable output

Under `--json`, every `run()` becomes a structured step inside the task's entry,
and all task output is captured into the payload — so stdout stays pure JSON,
ready for CI or an agent to parse. See
[Chaining & parallelism](orchestration.md#json-for-ci-and-agents).
