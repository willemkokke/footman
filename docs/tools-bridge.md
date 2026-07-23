# The tools bridge

Every tool on your PATH is already there — import it by name from
`footman.tools`, no declaration needed; attribute access chains subcommands,
and keyword arguments translate into flags *mechanically*:

```python
from footman import task
from footman.tools import bun, docker, ruff, terraform

@task
def ship():
    ruff.check("src", fix=True, select=["E", "F"])
    #  -> ruff check src --fix --select E --select F
    docker.compose.up(detach=True)
    #  -> docker compose up --detach
    bun.add("left-pad", global_=True)   # trailing _ escapes keywords
    #  -> bun add left-pad --global
    terraform("plan", out="tf.plan")    # never declared anywhere; works
```

The rules, all of them: `snake_case` → `--kebab-case`; `True` → bare flag;
`False`/`None` → omitted; `off` → that tool's own negation (see below); a
list repeats the flag (an empty one is omitted, so a task parameter's
default flows straight through); a single-letter key is a short flag
(`k="expr"` → `-k expr`); positional strings pass through untouched.

A valued long option is executed **attached** — `select="E"` runs
`--select=E`, not `--select E`. The two are equivalent for a plain value,
but attaching is the only spelling that always works: an optional-value
option can't tell its value from the next word across a space
(`--abbrev 4` is ambiguous to git, `--abbrev=4` is not), and a value that
starts with a dash would be read as another option (`--format -%h` fails,
`--format=-%h` works). You never have to think about it — the same rule
covers every tool, including ones footman has never heard of.

A **wrapper verb** — one that runs another command, like `uv run`,
`coverage run`, or `docker exec` — takes its flags *before* the wrapped
command, or they would land on the child instead of the tool:

```python
uv.run("pytest", "-q", frozen=True)   # → uv run --frozen pytest -q
```

footman knows which verbs wrap (it reads each verb's usage line), so
`--frozen` reaches uv while `pytest -q` passes through untouched. And a
tool's own **global** options — the ones that must precede the subcommand —
go through `flags()`:

```python
docker.flags(host="tcp://x").compose.up(detach=True)
# → docker --host=tcp://x compose up --detach
```

`docker --host … ps` works and `docker ps --host …` does not, so `flags()`
places a global where the tool expects it and returns the tool, keeping the
chain typed.

footman's own **run-control** is separate — it rides `opts()`, a closed set
(`nofail`, `in_process`, `capture`, `title`) that never becomes a tool flag,
the same policy-vs-work split a task's `.opts()` has:

```python
git.opts(nofail=True).push()        # tolerate a non-zero exit
pytest.opts(capture=False)("-s")    # stream this run live
```

Because it is a fixed set, `capture` here is unambiguously footman's — a tool's
own `--capture` (pytest's) still goes in the call, `pytest(capture="no")`.

!!! note "Captured output and colour (no pty)"

    footman captures a subprocess through a plain pipe, not a pseudo-terminal.
    A tool that only colourises when it sees a terminal (`isatty()`) therefore
    prints plain text when its output is *captured* in a parallel run — footman
    does not allocate a pty to fake a terminal, deliberately: a cross-platform
    pty needs a Unix-only stdlib module, ctypes ConPTY on Windows, or a
    third-party dependency, and footman is zero-dependency and cross-platform.

    Two escape hatches, both of which hand the tool the *real* terminal (so
    `isatty()` is genuinely true): `.opts(capture=False)` streams a run live,
    and `@task(interactive=True)` gives a task sole stdio. Otherwise, ask the
    tool to colour unconditionally — most have a flag, `ruff(color="always")`.

What footman *shows* you is spelled for reading, not for the parser: the
`--dry-run` line, the live progress line, and `recording()`'s
`step.command` all use the separated form (`--select E`), quoted so it
still pastes. The exact executed bytes are on `step.raw`, and `--verbose`
prints them. So a `recording()` assertion reads naturally and doesn't
change when the executed spelling does:

```python
with recording() as steps:
    ruff.check("src", select=["E", "F"])
assert steps[0].command == "ruff check src --select E --select F"  # reads plainly
assert steps[0].raw == "ruff check src --select=E --select=F"      # the real argv
```

## Disabling a flag that defaults on

`False` and `None` mean *omit* — that's what lets a task parameter's default
flow through (`fix: bool = False` → `fix=fix` → nothing) — so they can't
*also* mean the negation. To turn off a flag a tool enables by default, use
the `off` sentinel, or name the negation directly:

```python
from footman.tools import off

mkdocs.build(strict=off)              # → mkdocs build --no-strict
mkdocs.build(no_strict=True)          # exactly the same, by name
```

`off` emits **the spelling that tool actually uses**, which is not always
`--no-<name>`. `mkdocs build --no-clean` is rejected outright — the flag is
`--dirty` — and five of mkdocs' eight negatable options disagree with the
convention:

```python
mkdocs.build(clean=off)               # → mkdocs build --dirty
mkdocs.build(use_directory_urls=off)  # → --no-directory-urls
mkdocs.build(strict=off)              # → --no-strict, convention holds
```

Only the tool knows, so footman asks it: the spellings are extracted from
each tool's own description of itself (click states a negatable flag as
`secondary_opts`; git prints `--[no-]verify`; clap says so in prose) and
the exceptions are cached. `fm footman tools audit` compares that cache
against the installed tools, so one that changes its mind fails a check
rather than quietly producing a command it refuses.

The generated stubs carry the same fact where you'll actually see it — in
the flag's own documentation:

```python
clean: Remove old files from the site_dir before building (the default).
    Defaults on — `clean=off` emits `--dirty`.
```

`off` shines when a variable drives it, because it completes the boolean
story — `True` → `--flag`, `off` → the tool's negation:

```python
@task
def build(pretty_urls: bool = True):
    mkdocs.build(directory_urls=pretty_urls or off)
    # pretty_urls=True  → --directory-urls
    # pretty_urls=False → --no-directory-urls
```

And a *conditional* flag often needs no negation at all — when the flag is
off by default, `flag=condition` already does the right thing:

```python
zensical.build(clean=True, strict=check)   # --strict only when check
```

## Why no per-flag Python parameters?

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

## Autocomplete without the import bill

The bridge does ship duty-style autocompletion after all — as **stub
files**, which type checkers and IDEs read but the runtime never imports.
`ruff.check(` completes `fix=`, `select=`, `output_format=` and
friends, each with that tool's own help text on hover; `fix="yes"` is a
type error before you run anything. Two rules keep the stub subordinate to
the bridge:

- every stubbed verb ends in `**flags: Any`, so the stub *suggests* flags
  but can never forbid one — when a tool grows a flag, the bridge already
  speaks it and the stub merely hasn't heard of it yet;
- unknown verbs fall through to `Tool`, so nothing the runtime accepts is
  ever a type error.

Which means stub drift — the thing that breaks duty's wrappers at run
time — here degrades a *hint* at worst.

And the stubs are not written by hand. `fm footman tools sync` reads the
tools installed on your machine and writes one file per tool, so what your
editor suggests is what your binary accepts:

```console
$ fm footman tools sync
wrote 9 stub(s): ruff, ruff_format, basedpyright, uv, git, docker, mkdocs, zensical, coverage
skipped (not installed): bun, cspell, prek, markdownlint
```

Reading a tool means reading whatever it offers. click and argparse hand
over their parameters as data — including the negation, which click states
as `secondary_opts`. Everyone else gets their `--help` parsed, and the
five families footman meets are more alike than they look: an option is a
line starting with a dash, and its help is either past a run of spaces or
on the lines below. The dialects are small — `[default: 3]` (clap),
`(default true)` (cobra), `--file string` (cobra's Go types), "Use
`--no-fix` to disable" (clap's prose), and git's `--[no-]quiet`, which
states both spellings at once.

`fm footman tools audit` regenerates and compares, so a tool that moves
fails a check rather than quietly leaving your editor a version behind. A
tool that isn't installed is skipped *and named* — a check that quietly
covered nine of thirteen would be worse than no check at all:

```console
$ fm footman tools audit
skipped (not installed): bun, cspell, prek, markdownlint
9 stub(s) match their installed tool
```

The flip side of "never forbid" is that the stub can't reject a flag name
it doesn't know — `**flags: Any` accepts anything. So a mistyped flag isn't
a type error; what happens is decided at run time by the translation rules:

- a truthy value produces the flag and the **tool** rejects it loudly —
  `ruff.check(exitzero=True)` → `ruff check --exitzero` → *unknown flag*;
- a `False`/`None` value is omitted (the very rule that lets a task
  parameter's default flow through), so it silently does nothing —
  `ruff.check(exit=False)` runs a plain `ruff check`, not what you meant.

That second case is the one to know: when a flag isn't autocompleting, you
guessed a name that doesn't exist. Reach for the real one (`exit_zero`,
here), or side-step the whole question by passing the literal flag as a
positional — always unambiguous, never translated:

```python
ruff.check(*paths, "--exit-zero")
```

For the rare task that must branch on a tool's CLI generation:

```python
if ruff.installed_version() >= (0, 9):
    ruff.check("src", output_format="github")
```

`installed_version()` runs `<tool> --version` once per process (outside the
task context, so `--dry-run` and test recording can't lie to it) and returns
a comparable int tuple.

## In-process where it pays

The bridge composes with in-process execution the same way it does with
flags: no transcription. Every installed Python CLI declares a
`[console_scripts]` entry point; `in_process` resolves it and hands it the
arguments — the tool runs inside footman's process, no interpreter spawn.
Nearly every entry point accepts arguments directly (click commands like
mkdocs's and zensical's `cli`, or coverage's `main(argv=None)`) and is
simply called; only a legacy zero-arg entry falls back to running under a
patched `sys.argv`, and only those calls serialise.

```python
mkdocs.build(strict=True)                          # in-process by default
Tool("griffe", in_process=True)("dump", "footman")     # opt any tool in (construction)
coverage.opts(in_process=False).html()             # ...or out, per call via .opts()
```

`mkdocs`, `zensical`, and `coverage` default to in-process. `pytest`
keeps its dedicated `pytest.main` path for a concrete reason: pytest's
console entry point takes *no* arguments — the generic path could only
drive it through the patched-`sys.argv` fallback, serialised — while
`pytest.main(args)` is pytest's own argument-accepting API. Same
no-transcription contract, direct call, parallel-safe.

The tool's own module is imported only when the call actually *executes* —
resolving the entry point is pure metadata, but the `.load()` that imports
it happens inside the callable footman runs. So a branch you never take, or
a `--dry-run`, or a `recording()` test, costs zero tool imports; you pay
only for the in-process tools you really invoke. A *preference* (`Tool(...,
in_process=True)`) falls back to a subprocess when no entry point is
installed; a *demand* (`.opts(in_process=True)`) errors with a taught message
instead. `nofail`, `in_process`, `capture`, and `title` are footman
run-control — set through `.opts()`, never translated to flags — so everything
you pass to the call itself is a tool flag.

Beyond speed, in-process is sometimes the only correct option. On macOS,
SIP (System Integrity Protection) strips `DYLD_*` library-path variables
from child processes, so a tool that needs Homebrew's native libraries
(mkdocs with cairo, for social cards) can never see them as a subprocess —
but in-process, an env var set before the first native-library import
sticks:

```python
@task
def docs():
    if sys.platform == "darwin":            # SIP strips DYLD_* from children;
        os.environ.setdefault(              # in-process, this survives
            "DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib"
        )
    mkdocs.build(strict=True)
```

And in-process keeps footman's parallelism: capture routes through the
per-task stdout router (thread-confined, no global redirect), and entries
that accept an argument list — click commands, `main(argv=None)`, which is
nearly all of them — are called directly, no `sys.argv` in sight. Only a
legacy zero-argument `main()` that insists on reading `sys.argv` gets the
patched-and-serialised fallback.

`python(...)` targets the current interpreter, whatever is (or isn't) on your
PATH. There is no `sh`: a command as one string is `run("…")` — footman splits
and runs it with **no shell**, so `|`, `>`, `&&`, `$VAR` are literal, not
interpreted. When you *want* a shell, ask for one explicitly:

```python
run("tar cf - . | ssh host tar xf -", shell=True)   # a real pipe
run("echo $HOME/logs/*.gz", shell="bash")           # a specific interpreter
```

`shell=True` follows the project's shell policy (`[shell] default`, POSIX
everywhere by default — bash, then plain sh, git bash on Windows); a string
names a concrete shell (`bash`/`zsh`/`sh`/`fish`/`nu`/`pwsh`/`cmd`) or a
strategy (`posix`/`native`). A missing shell or a wrong-platform one (`cmd` off
Windows) is a taught error, never a silent wrong shell. And a shell-free
`run("a | b")` doesn't misfire quietly — footman spots the operator and points
you at `shell=`.

Two flags harden a shell run:

```python
run(script, shell=True, strict=True)   # set -eo pipefail
run(script, shell=True, clean=True)    # no user startup files
```

`strict=True` fails on the first error **and** on a failing pipe stage
(`set -eo pipefail` for bash/zsh; `$ErrorActionPreference='Stop'` for pwsh).
Plain `sh` has no `pipefail`, so it degrades to `set -e` with a one-time note;
`fish`/`nu`/`cmd` have no errexit at all, so `strict` there is a taught error,
not a silent no-op. `clean=True` runs the interpreter without the user's
startup files (`--norc --noprofile` and no `$BASH_ENV` for bash, `-NoProfile`
for pwsh, `/d` for cmd), so a task's shell behaves the same on every machine.

## Parallelism

Independent tasks run **concurrently as threads** (a `ThreadPoolExecutor`),
not as separate processes — a task runner mostly waits on subprocesses and
I/O, where the GIL doesn't bite, and threads share the already-loaded manifest
and imports. That one choice explains the rest:

- A tool call is usually a **subprocess** — its own process, its own
  `sys.argv`, trivially parallel.
- An **in-process** tool runs in the calling thread. footman calls its entry
  point *directly* when the entry accepts an argument list (`cli(argv)`,
  `main(argv=None)`, `pytest.main(args)`), so it stays parallel. The *only*
  thing that serialises is a legacy zero-argument `main()` that reads
  `sys.argv` — because `sys.argv` is process-global, those calls take a lock.
- Concurrent output can't interleave: each task writes through a **per-task
  stdout router** (thread-confined, no global redirect), so two tools running
  at once keep their lines apart.

So the defaults line up rather than fight: the tools marked `default`
in-process — mkdocs, zensical, coverage — all take an argument list and run in
parallel, and `pytest` is a function calling the arg-accepting `pytest.main`
for exactly this reason. The single serialised case, a zero-arg `main()`, is
rare and clearly bounded.

## Sharing tools between projects

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
