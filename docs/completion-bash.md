# Completion on bash

This is a recording of a real bash session — the hook loaded the way
the next section describes, <kbd>Tab</kbd> listing the candidates,
a prefix completing. Regenerated from a live shell on every docs
build, so it cannot drift from what your terminal will do:

![Animated: fm TAB lists the tasks in bash, che TAB completes to check](_generated/shots/bash-cast.svg)

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
