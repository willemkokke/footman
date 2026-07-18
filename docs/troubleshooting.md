# Troubleshooting

footman treats error messages as product surface: every one names the
culprit, states the expectation, and proposes the fix. This page is the
catalogue — each example below is real output, not paraphrase. If you ever
hit a raw Python traceback instead of one of these, that's a footman bug;
please report it.

## Reading an error

```console
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
```

The shape is always `prog: task: what — hint`. The prefix is the brand
(`fm`, or your own CLI's name), so in a chain of tools you know who's
talking.

## Parse errors — exit 2, nothing has run

The splitter validates the whole command line against the manifest before
executing anything, so a typo never half-runs a chain.

| You'll see | It means | The fix |
| ---------- | -------- | ------- |
| `expected a task name, got 'linnt' (know: deploy, lint, up)` | no task by that name here | the `know:` list is your menu; `fm --list` for help text |
| `lint: unknown option --fx (task options come right after their task; globals go before the first task)` | the option isn't lint's | `fm --help lint` shows its real options |
| `deploy: missing required argument(s): <target>` | a positional with no default wasn't given | required params have no default — pass a value |
| `deploy: <target> must be one of dev\|staging\|prod (got 'produ') — did you mean 'prod'?` | eager choice validation (`Literal`, `Enum`, strict `suggest`) | take the hint |
| `deploy: <target> must be one of dev\|staging\|prod — 'check' looks like the next task; did you forget <target>?` | a chain word landed where a required value belongs | give the value, then the next task |
| `test: --jobs expects an integer (got 'many')` | eager type validation from the annotation | typed params parse before anything runs |
| `test: --jobs must be between 1 and 32 (got '99')` | a `between(...)`/`range` bound | bounds are inclusive; the message quotes them |
| `render: <template> must be an existing file (got 'missing.toml')` | an `exists`/`isfile`/`isdir` marker | the path is checked before the task runs |
| `deploy: --env expects KEY=VALUE (got 'DEBUG')` | a `dict[K, V]` param needs pairs | `--env DEBUG=1`, comma-split or repeated |
| `lint: --fix is a flag and takes no value` | `--fix=yes` on a `bool` param | flags are bare: `--fix`, or `--no-fix` |
| `--where expects a value` | a value-bearing global given bare | `--where TASK` |
| `unknown global option --bogus (global options go before the first task)` | not one of fm's globals | `fm --help` lists them all |

One asymmetry worth knowing: constraints on **env-supplied** values
(`env("VAR")` fallbacks) are enforced at binding time rather than parse
time — the parser never sees your environment — so those surface as a
failed task result with the same wording, plus the source:
`--jobs (from $JOBS) must be between 1 and 32 (got 99)`.

## Tasks-file errors — your `tasks.py` needs attention

| You'll see | It means | The fix |
| ---------- | -------- | ------- |
| `/repo/tasks.py: the root already has a task named 'build'` | two tasks claimed one name | rename one, or `@task(name=...)` |
| `failed to import /repo/tasks.py: SyntaxError: ...` | the file doesn't parse | the named file is the culprit, cascade or not |
| `failed to import /repo/tasks.py: ImportError: ...` | an import inside the tasks file failed | footman shows the type and message, never a traceback |
| `<target>: env('DEPLOY_ENV') needs a default — an env fallback makes the parameter optional, so it needs somewhere to fall` | `env()` on a required param | give it a default |
| `<opts>: env() is not supported on dict parameters` | `env()` on a `dict[K, V]` | read the variable inside the task instead |
| `dynamic choices from projects() failed: FileNotFoundError: ... — fix the completer, or pass suggest(fn, strict=False) if this data is best-effort` | a strict completer raised | strict promises validation, so it fails loudly rather than validating nothing |
| `include('shared_tasks'): the module was already imported outside include(), so its tasks were never captured — ...` | a bare `import` beat your `include()` | `include()` first, or expose an explicit `Group` |
| `include(): 'shared_tasks' has no task or group named 'lnt' (has: fmt, lint)` | a typo in `only=`/`exclude=` | the message lists what the provider has |
| `plugin 'mkdocs': no 'footman.tasks' entry point found (installed: none)` | a configured plugin isn't installed | install it, or drop it from `[tool.footman] plugins` |
| `plugin 'mkdocs': failed to import (ModuleNotFoundError: ...)` | the plugin is installed but its own import failed (a missing optional dep) | install what the plugin needs, or drop it — footman names the cause, never a traceback |

A parameter whose annotation footman can't use (an unresolved name, a
value) emits a `UserWarning` — values pass through as plain text until you
fix the annotation.

## Run-time errors — a task went wrong

| You'll see | It means |
| ---------- | -------- |
| ``test: RunFailed: `pytest -q` exited with code 1`` (plus the replayed output) | a `run()` command failed; its captured output is shown only now |
| `release: ValueError: 'nope' is not MAJOR.MINOR.PATCH` | the task (or a `check(fn)` validator) raised; type and message, no traceback |
| `build: exited with code 3` | the task returned a non-zero int |
| `up: Unavailable: requires docker on PATH` | a `when=`-disabled task was asked to run; the reason is live, not cached |
| `dependency cycle: b -> a -> b (check the pre/post declarations of these tasks)` | your `pre`/`post` graph loops |
| `interrupted` (exit 130) | Ctrl-C — pending tasks were cancelled |

In a chain, a failed task's dependents are skipped; `-k/--keep-going` runs
every independent branch anyway. Output from parallel tasks never
interleaves — each task's buffer is flushed as one block.

## Config errors

A malformed **discovered** config (a `pyproject.toml` or `footman.toml` in
the cascade) warns and is skipped — one broken file between the repo root
and your cwd must not brick every invocation:

```console
fm: ignoring malformed config: /repo/footman.toml: Expected '=' after a key in a key/value pair (at line 1, column 5)
```

A file you named **explicitly** with `--config` is a hard error (exit 2) when
it's malformed, unreadable, or missing — you asked for that file on purpose, so
a typo like `--config prod.tmol` is reported (`--config: prod.tmol: no such
file`), never silently ignored.

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | everything ran and succeeded |
| 1 | a task raised an exception |
| N | a task returned N / its `run()` command exited N (first failure wins) |
| 2 | footman refused before or while binding: parse, tasks-file, config, availability |
| 130 | interrupted (Ctrl-C) |

`--json` consumers: the same story is in the envelope — `ok`, `code`, and
`error` per task. See [CI & automation](ci.md).
