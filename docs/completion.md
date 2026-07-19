# Completion

Completion answers from a JSON manifest cached per directory under
`~/.cache/footman/` (or `$XDG_CACHE_HOME/footman/` where that's set), so each
folder of a [monorepo](monorepos.md) caches its own merged cascade. The hot
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

## Installing the shell hook

One command — footman detects which shell invoked it (by walking the
process tree, the way typer's `shellingham` dependency does, minus the
dependency), or takes the name explicitly:

```console
fm --install-completion         # detected: bash, zsh, fish, pwsh, or nushell
fm --install-completion zsh     # or name it yourself
```

bash and zsh get a script under `$XDG_DATA_HOME/fm/` plus a single guarded
`source` line in your rc file; fish gets
`~/.config/fish/completions/fm.fish`, which fish auto-loads — no rc edit at
all. pwsh (PowerShell 7+, or Windows PowerShell via the `powershell` alias)
gets a `Register-ArgumentCompleter` script dot-sourced from the profile
PowerShell itself reports — and on a Windows machine with *both* PowerShells
installed, from both of their profiles, since each keeps its own `$PROFILE`
and the hook runs on either. nushell (alias `nu`) gets an external-completer
hook sourced from the config nushell itself reports — and the hook *wraps*
whatever external completer you already run (carapace, say), answering for
`fm` and passing every other command through untouched. Running any
installer twice changes nothing. A custom-branded CLI installs completion
for *its* name the same way (`acme --install-completion zsh`), and the
generated hook calls that brand's `--complete`.

## Enabling completion for one session

`--install-completion` edits an rc file, so completion is on in every future
shell. To turn it on for the **current** shell only — no rc edit, nothing left
behind — print the hook to stdout and evaluate it:

```console
eval "$(fm --setup-completion zsh)"                            # bash and zsh
fm --setup-completion fish | source                           # fish
fm --setup-completion pwsh | Out-String | Invoke-Expression   # PowerShell
```

Like the installer, a bare `fm --setup-completion` detects the shell — the
detection note goes to stderr, so it never pollutes what `eval` reads. nushell
is the exception: its hook mutates `$env.config`, which `eval` can't apply, so
nushell users install with `--install-completion`.
