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

## The `tools.*` bridge

Every tool on your PATH is already wrapped — `tools.<name>` needs no
declaration, attribute access chains subcommands, and keyword arguments
translate into flags *mechanically*:

```python
from footman import task, tools

@task
def ship():
    tools.ruff.check("src", fix=True, select=["E", "F"])
    #  -> ruff check src --fix --select E --select F
    tools.docker.compose.up(detach=True)
    #  -> docker compose up --detach
    tools.bun.add("left-pad", global_=True)   # trailing _ escapes keywords
    #  -> bun add left-pad --global
    tools.terraform("plan", out="tf.plan")    # never declared anywhere; works
```

The rules, all of them: `snake_case` → `--kebab-case`; `True` → bare flag;
`False`/`None` → omitted; a list repeats the flag (an empty one is omitted,
so a task parameter's default flows straight through); a single-letter key
is a short flag (`k="expr"` → `-k expr`); positional strings pass through
untouched.

### Why no per-flag Python parameters?

duty's tools library transcribes every flag of every tool into typed Python
parameters — five thousand careful lines, and genuinely pleasant to
autocomplete. The cost is drift: the wrapper freezes the flag-set its author
copied, while the tool keeps moving. duty's `ruff.check(show_source=True)`
still emits `--show-source` today — and ruff removed that flag; the modern
binary rejects it. There is no version machinery underneath to catch this,
and there realistically can't be: the wrapper *is* the hardcoded version.

footman's bridge sidesteps the whole class of problem: nothing is
transcribed, so nothing goes stale. Your installed tool's `--help` is the
one source of truth, at whatever version is installed. The trade is honest —
you don't get IDE autocompletion of a tool's flags, and a typo'd flag errors
at run time (exactly as it does in duty, whose transcriptions aren't
validated eagerly either).

### Autocomplete without the import bill

The bridge does ship duty-style autocompletion after all — as a **stub
file** (`tools.pyi`), which type checkers and IDEs read but the runtime
never imports. `tools.ruff.check(` completes `fix=`, `select=`,
`output_format=` and friends; `fix="yes"` is a type error before you run
anything. Two rules keep the stub subordinate to the bridge:

- every stubbed verb ends in `**flags: Any`, so the stub *suggests* flags
  but can never forbid one — when a tool grows a flag, the bridge already
  speaks it and the stub merely hasn't heard of it yet;
- unknown verbs fall through to `Tool`, so nothing the runtime accepts is
  ever a type error.

Which means stub drift — the thing that breaks duty's wrappers at run
time — here degrades a *hint* at worst, and fixing it is editing one line
of a `.pyi`. The flag lists were read from the installed tools' `--help`,
not from memory.

For the rare task that must branch on a tool's CLI generation:

```python
if tools.ruff.installed_version() >= (0, 9):
    tools.ruff.check("src", output_format="github")
```

`installed_version()` runs `<tool> --version` once per process (outside the
task context, so `--dry-run` and test recording can't lie to it) and returns
a comparable int tuple.

### In-process where it pays

The bridge composes with in-process execution the same way it does with
flags: no transcription. Every installed Python CLI declares a
`[console_scripts]` entry point; `in_process` resolves it and calls it with
`sys.argv` patched — the tool runs inside footman's process, no interpreter
spawn:

```python
tools.mkdocs.build(strict=True)                    # in-process by default
tools.Tool("griffe", in_process=True)("dump", "footman")   # opt any tool in
tools.coverage.html(in_process=False)              # ...or out, per call
```

`mkdocs`, `zensical`, and `coverage` default to in-process; `tools.pytest`
keeps its dedicated `pytest.main` path. A *preference* (`Tool(...,
in_process=True)`) falls back to a subprocess when no entry point is
installed; a *demand* (`in_process=True` at the call) errors with a taught
message instead. `nofail` and `in_process` are the two reserved keywords —
everything else translates to flags.

Beyond speed, in-process is sometimes the only correct option. On macOS,
SIP strips `DYLD_*` variables from child processes, so a tool that needs
Homebrew's native libraries (mkdocs with cairo, for social cards) can never
see them as a subprocess — but in-process, an env var set before the first
cffi import sticks:

```python
@task
def docs():
    if sys.platform == "darwin":            # SIP strips DYLD_* from children;
        os.environ.setdefault(              # in-process, this survives
            "DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib"
        )
    tools.mkdocs.build(strict=True)
```

And in-process keeps footman's parallelism: capture routes through the
per-task stdout router (thread-confined, no global redirect), and entries
that accept an argument list — click commands, `main(argv=None)`, which is
nearly all of them — are called directly, no `sys.argv` in sight. Only a
legacy zero-argument `main()` that insists on reading `sys.argv` gets the
patched-and-serialised fallback.

`tools.python(...)` targets the current interpreter; `tools.sh("...")`
takes a whole command line as one string.

### Sharing tools between projects

A "tool" is a plain object — publishing one is publishing Python:

```python
# yourorg_tools/__init__.py
from footman.tools import Tool

helmfile = Tool("helmfile", "--environment", "prod")
```

```python
# tasks.py
from yourorg_tools import helmfile
```

We considered a plugin mechanism for tools (entry points, like
[`footman.tasks`](composing.md) for tasks) and rejected it: tasks need
framework machinery — registry mounting, completion, collision policy —
but a tool has no framework surface at all. An import already does
everything an entry point would, with less indirection.

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
