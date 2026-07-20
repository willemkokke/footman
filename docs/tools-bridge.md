# The tools bridge

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
`False`/`None` → omitted; `off` → that tool's own negation (see below); a
list repeats the flag (an empty one is omitted, so a task parameter's
default flows straight through); a single-letter key is a short flag
(`k="expr"` → `-k expr`); positional strings pass through untouched.

## Disabling a flag that defaults on

`False` and `None` mean *omit* — that's what lets a task parameter's default
flow through (`fix: bool = False` → `fix=fix` → nothing) — so they can't
*also* mean the negation. To turn off a flag a tool enables by default, use
the `off` sentinel, or name the negation directly:

```python
from footman.tools import off

tools.mkdocs.build(strict=off)              # → mkdocs build --no-strict
tools.mkdocs.build(no_strict=True)          # exactly the same, by name
```

`off` emits **the spelling that tool actually uses**, which is not always
`--no-<name>`. `mkdocs build --no-clean` is rejected outright — the flag is
`--dirty` — and five of mkdocs' eight negatable options disagree with the
convention:

```python
tools.mkdocs.build(clean=off)               # → mkdocs build --dirty
tools.mkdocs.build(use_directory_urls=off)  # → --no-directory-urls
tools.mkdocs.build(strict=off)              # → --no-strict, convention holds
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
    tools.mkdocs.build(directory_urls=pretty_urls or off)
    # pretty_urls=True  → --directory-urls
    # pretty_urls=False → --no-directory-urls
```

And a *conditional* flag often needs no negation at all — when the flag is
off by default, `flag=condition` already does the right thing:

```python
tools.zensical.build(clean=True, strict=check)   # --strict only when check
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
`tools.ruff.check(` completes `fix=`, `select=`, `output_format=` and
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
tools.ruff.check(*paths, "--exit-zero")
```

For the rare task that must branch on a tool's CLI generation:

```python
if tools.ruff.installed_version() >= (0, 9):
    tools.ruff.check("src", output_format="github")
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
tools.mkdocs.build(strict=True)                    # in-process by default
tools.Tool("griffe", in_process=True)("dump", "footman")   # opt any tool in
tools.coverage.html(in_process=False)              # ...or out, per call
```

`mkdocs`, `zensical`, and `coverage` default to in-process. `tools.pytest`
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
installed; a *demand* (`in_process=True` at the call) errors with a taught
message instead. `nofail` and `in_process` are the two reserved keywords —
everything else translates to flags.

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
