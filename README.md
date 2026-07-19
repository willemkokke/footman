# footman

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and
the UX of [typer](https://typer.tiangolo.com/): typed function signatures
become real flags and positionals, modules become nested command groups,
independent tasks run in parallel by default, and shell completion answers
from a cached manifest in ~25 ms — **without importing your code**.

Ships two console scripts, `footman` and the two-letter `fm`. Zero runtime
dependencies. Python 3.11+.

> [!WARNING]
> **Very early code.** footman is alpha and moving fast — the public API,
> the decorator surface, the manifest format, and the CLI grammar can all
> change without notice or a deprecation cycle. Pin an exact version if you
> build on it.

## Why

`duty` got a lot right — the `run()` capture model, the decorator
ergonomics — and footman keeps those ideas. Where it pushes is the parts
that compound: completion served from a cache instead of re-importing your
project on every TAB (~15× faster, measured), eager type and choice
validation with errors that teach, a DAG scheduler that runs independent
tasks concurrently (the same four-step `check` lands ~4× sooner than duty
or invoke, measured), a monorepo task cascade that merges a `tasks.py` per
folder, and a first-party story for testing your tasks. The receipts live
in the [comparison](https://willemkokke.github.io/footman/comparison/) —
every number reproducible from [`comparison/`](comparison/).

## Taste

```console
uv add --dev footman        # or: pip install footman
```

```python
# tasks.py
from footman import task, group, run, tools

@task
def lint(fix: bool = False):
    "Run ruff over the project."
    tools.ruff.check("src", fix=fix)

@task(pre=[lint])
def test(*pytest_args):
    "Run the test suite (extra args after --)."
    tools.pytest(*pytest_args)

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    "Serve the docs locally."
    run(f"mkdocs serve -a localhost:{port}")
```

```console
$ fm lint --fix
$ fm lint test docs serve --port 8001   # one chain; independent tasks run in parallel
$ fm test -- -k grammar -x              # everything after -- goes to pytest
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
$ fm --install-completion               # detects your shell; TAB answers in ~25 ms
```

## Learn more

**[Documentation](https://willemkokke.github.io/footman/)** — start with
[Getting started](https://willemkokke.github.io/footman/getting-started/),
then the good parts:
[typed signatures](https://willemkokke.github.io/footman/typing/) ·
[chaining & parallelism](https://willemkokke.github.io/footman/orchestration/) ·
[monorepos](https://willemkokke.github.io/footman/monorepos/) ·
[composing tasks](https://willemkokke.github.io/footman/composing/) ·
[running tools](https://willemkokke.github.io/footman/tools/) ·
[testing your tasks](https://willemkokke.github.io/footman/testing/) ·
[completion](https://willemkokke.github.io/footman/completion/) ·
[CI & automation](https://willemkokke.github.io/footman/ci/) ·
[comparison with duty / invoke / poe / typer](https://willemkokke.github.io/footman/comparison/)

MIT licensed. The road to 1.0 lives in [ROADMAP.md](ROADMAP.md).
