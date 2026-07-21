# Completion

Completion answers from a JSON manifest cached per directory under
`~/.cache/footman/` (or `$XDG_CACHE_HOME/footman/` where that's set — and
`$FOOTMAN_CACHE_DIR` overrides both, moving every footman cache in one go),
so each folder of a [monorepo](monorepos.md) caches its own merged cascade. The hot
path is stdlib-only — it reads one file, parses JSON, and walks the tree; it
**never imports footman or your tasks**.

## The latency story

Measured cold-process on an M-series Mac — the row that matters is the last
one, because it's the exact command the installed shell hooks run:

| variant                                    |   mean |
| ------------------------------------------ | -----: |
| interpreter startup (floor)                | 17 ms  |
| standalone resolver (`python -S`)          | 22 ms  |
| `python -m footman --complete`             | 23 ms  |
| `fm --complete` (the installed hook path)  | 24 ms  |

So the honest headline is **~25 ms per <kbd>Tab</kbd>**, of which ~17 ms is
Python starting up at all. footman regenerates the manifest for free on any
execution-path run (it is importing your code anyway) and rewrites it only
when the command surface actually changed. Reproduce with
`uv run python scripts/bench_completion.py`.

## How it stays fast

footman's `main()` checks for `--complete` **before importing the framework or
your tasks**, dispatching straight to the stdlib-only resolver. A bare
`import footman` pays for nothing but the entry module. That is why completion is
~15× faster than runners that re-import your project on every keystroke.

## Keeping dynamic completions fresh

The manifest bakes in the output of your [dynamic completers](typing.md#dynamic-completion)
(git branches, file lists, …), refreshed for free on any real `fm` run. Between
runs those answers can drift — so if the cached manifest for this directory is
older than `max_age` when you press <kbd>Tab</kbd>, footman returns the cached
answer instantly and spawns a **detached** rebuild for next time
(stale-while-revalidate). The <kbd>Tab</kbd> never waits, and concurrent presses
spawn at most one rebuild.

Tune it with `[tool.footman]`:

```toml
[tool.footman]
completion.max_age = "10m"   # default; "30s", "1h", a plain int (seconds)
# completion.max_age = "off" #   or 0 — disable background refresh entirely
```

## Chained and grouped completion

Completion is aware of the whole command line, not just the first word:

```sh
fm workspace mount --share <TAB>   # main  scratch  archive
fm format lint --fix <TAB>         # completes within the chain
```

Group names, task names, flags, options, and both static and
[dynamic](typing.md#dynamic-completion) value sets all complete. Where a shell
can show them — zsh, fish, and nushell render a description column, pwsh a
tooltip — task and group names carry their one-line docstring, so holding
<kbd>Tab</kbd> teaches the whole CLI:

```text
build   — compile and bundle
deploy  — ship to an environment
```

## File paths

A value that takes a filesystem path completes files — footman hands off to
your shell's own path completion rather than reading the disk from its cached
manifest. This covers the path-valued globals (`-f`/`--tasks-file`,
`-C`/`--directory`, `--config`) and any task option annotated `Path`:

```sh
fm -f tasks/<TAB>          # your shell's own file completion
fm build --out dist/<TAB>  # a Path-typed option, the same way
```

A plain `str` or `int` value has no such handoff: it completes nothing, rather
than bluntly offering files where a name was wanted.

## Your shell

One command — footman detects which shell invoked it (by walking the
process tree, the way typer's `shellingham` dependency does, minus the
dependency), or takes the name explicitly:

```console
fm --install-completion         # detected: bash, zsh, fish, pwsh, or nushell
fm --install-completion zsh     # or name it yourself
fm --uninstall-completion       # reverses exactly what install did
```

Each shell has its own page — what gets installed where, a session-only
form, and how to style the completion menu, colours included:

| shell | descriptions shown as | installed via | session-only form |
| ----- | --------------------- | ------------- | ----------------- |
| [bash](completion-bash.md) | — (bash has no description column) | script + rc line | `eval "$(fm --setup-completion bash)"` |
| [zsh](completion-zsh.md) | aligned column (`_describe`) | script + rc line | `eval "$(fm --setup-completion zsh)"` |
| [fish](completion-fish.md) | aligned column, native | one auto-loaded file | `fm --setup-completion fish \| source` |
| [PowerShell](completion-pwsh.md) | tooltip (menu completion) | script + `$PROFILE`(s) | `… \| Out-String \| Invoke-Expression` |
| [nushell](completion-nushell.md) | description column, native | script + config line | — (install only) |

Every installer and uninstaller is idempotent — running one twice changes
nothing. A custom-branded CLI installs completion for *its* name the same
way (`acme --install-completion zsh`), and the generated hook calls that
brand's `--complete`.
