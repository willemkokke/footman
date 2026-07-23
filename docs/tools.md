# Running tools

Task bodies run tools through `run()` and the typed wrappers imported from
`footman.tools`. `run()` captures output and stays quiet on success,
**replaying it only on failure** — so a green run is calm and a red one shows
exactly what broke:

```python
from footman import task, run
from footman.tools import pytest, ruff

@task
def check():
    ruff("check", "src", fix=False)   # subprocess (ruff is a binary)
    pytest("-x")                       # in-process via pytest.main
    run("mkdocs build --strict")       # any command; a callable also works
```

Each tool is imported by name — `from footman.tools import git` gives you a
typed `git` you call as `git.commit(…)`; a tool footman has never heard of
imports just the same and runs as a subprocess. This page covers `run()` and
the task context. The tool wrappers — how the flag translation works,
disabling flags, in-process execution, and why nothing is transcribed per
tool — have their own page: [The tools bridge](tools-bridge.md).

## `run()`

- Takes a command (string or list) or a Python callable.
- Raises on a non-zero exit; `.opts(nofail=True)` returns the code instead.
- Honours `--dry-run` (prints the command instead of running it).
- Records a step for [`--json`](json.md) (command, code, duration, captured
  output); `capture=False` lets output through unbuffered and records an
  empty capture — for serve-style tasks that must not buffer.
- Runs from the task's context cwd — in a [cascade](monorepos.md) the folder
  the task was defined in — with the context env overlay applied. Subprocess
  and in-process tools honour this identically.

## Fetch and cache files: `fetch()`

`fetch(url, sha256=…, into=…)` downloads into footman's own cache — the same
directory `$FOOTMAN_CACHE_DIR` moves and the daily collector tends, so vendored
artifacts for deleted projects clean themselves up:

```python
from footman import fetch, task

@task
def vendor():
    "Fetch the pinned toolchain."
    fetch("https://example.com/protoc-27.tar.gz",
          sha256="9f86d081884c…", into=Path("vendor/protoc"))
```

Like `run()`, a fetch **is a step**: `--dry-run` prints it without touching the
network, `recording()` asserts on it in tests, [`--json`](json.md) carries it,
and its byte counts feed the [progress bar](progress.md). A second run
revalidates with the server (ETag / `If-None-Match`) — a `304` costs one round
trip and keeps "cached" honest — and `sha256=` refuses anything that arrived
wrong. The backend is stdlib `urllib` by default (zero dependencies, and the
only one that can report bytes as they arrive); `curl`, `httpx`, `requests`, or
`auto` are available when named in `[fetch]` config, for a corporate proxy whose
TLS store Python can't see. The full worked example is in the
[cookbook](cookbook.md#fetch-and-cache-a-toolchain).

## No `ctx` needed

`run()` and `passthrough()` read the current task's context implicitly, so a
task body stays boilerplate-free. Declare a first `ctx: Context` parameter only
if you want the object — footman keeps it out of the CLI mapping:

```python
from footman import Context, task
from footman.tools import pytest

@task
def test(ctx: Context):
    pytest(*ctx.passthrough)                # fm test -- -k mytest -x
```

`passthrough()` and `ctx.passthrough` are the same list two ways — the free
function reads the current context so most tasks never declare `ctx` at all.

One boundary to know: the ambient context follows footman's own concurrency
(`parallel()` hands each worker a child context, steps and all) but **not
threads you spawn yourself** — a raw `threading.Thread` starts with an empty
context, so a `run()` inside it would see default state: wrong folder, no
env overlay, no step recording. Fan out through `parallel()`, wrap your
target with `contextvars.copy_context().run(...)`, or declare `ctx` and
pass it in explicitly.

## Machine-readable output

Under `--json`, every `run()` becomes a structured step inside the task's
entry, all task output is captured into the payload, and a task's return
value rides along under `returned`. The full contract — envelopes, refusals,
exit codes — lives in [JSON output](json.md).
