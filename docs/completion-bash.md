# Completion on bash

This is a recording of a real bash session — the hook loaded the way the
next section describes, <kbd>Tab</kbd> <kbd>Tab</kbd> listing the
candidates (bash's default reveals the list on the second press; one
press completes as far as the match reaches), a prefix completing.
Regenerated from a live shell on every docs build, so it cannot drift
from what your terminal will do:

![Animated: fm TAB TAB lists the tasks in bash, che TAB completes to check](_generated/shots/bash-cast.svg)

bash's list is names only — readline has no description column, so the
one-line docstrings that zsh, fish, PowerShell, and nushell render next
to each candidate don't appear here. If you want the list on a single
press, that's readline's `show-all-if-ambiguous` in your `~/.inputrc`.

## Install

```console
fm --install-completion bash
```

This writes the hook to `$XDG_DATA_HOME/fm/completion.bash` (default
`~/.local/share/fm/completion.bash`) and appends one guarded `source` line to
`~/.bashrc`. On macOS — where Terminal opens *login* shells that never read
`.bashrc` — the line also lands in your login profile (`.bash_profile`,
`.bash_login`, or `.profile`, whichever exists). Running it twice changes
nothing. The hook works on bash 3.2, so the ancient `/bin/bash` macOS ships
is fine.

For the **current session only** — no rc file touched:

```console
eval "$(fm --setup-completion bash)"
```

## Windows (git-bash)

git-bash is a first-class target: `fm --install-completion` with no
argument detects it (via the `MSYSTEM` variable it exports — PowerShell's
`PSModulePath` is machine-level and set inside git-bash too, so it can't
be the tell), and the `source` line written into `~/.bashrc` uses the
MSYS spelling `/c/Users/…` rather than a backslashed Windows path, which
bash would read as escapes and silently source nothing.

Detection works from a git-bash *session*, which is where you'd be
typing: the launcher exports `MSYSTEM`, and that's the tell. A bare
`bash.exe` spawned by some other Windows program doesn't have it and is
indistinguishable from any other process, so footman falls back to the
PowerShell answer there — name the shell explicitly
(`fm --install-completion bash`) if you're in that unusual spot.

## What you get

Task names, group names, flags, and choice values all complete, chain-aware:

```text
$ fm dep<TAB>          →  fm deploy
$ fm deploy <TAB>      →  dev  staging  prod
```

The honest limitation: **bash's completion protocol has no description
column.** Where zsh, fish, and nushell show each task's one-line docstring
next to its name, bash can only display bare words — so footman strips the
descriptions before handing candidates over. If the described column is the
part you love, the [zsh](completion-zsh.md) and [fish](completion-fish.md)
pages show the same completions with descriptions intact.

## Colours and appearance

What little bash offers here belongs to readline, configured in
`~/.inputrc` — these apply to all completion, not just `fm`:

```text
# Colour the part of each candidate you've already typed.
set colored-completion-prefix on

# Colour candidates by file type (uses $LS_COLORS) — mostly matters for
# path completion inside task arguments.
set colored-stats on

# Show all candidates immediately instead of beeping first…
set show-all-if-ambiguous on

# …or cycle through them in place with repeated TABs.
TAB: menu-complete
```

Start a new shell (or `bind -f ~/.inputrc`) to pick changes up. There is no
way to colour a description column, because there isn't one — that's bash,
not footman.

## Uninstall

```console
fm --uninstall-completion bash
```

Removes the script and the `source` line from every rc file the installer
touched.
