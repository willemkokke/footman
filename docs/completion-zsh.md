# Completion on zsh

This is a recording of a real zsh session — the hook installed the way the
next section describes, <kbd>Tab</kbd> pressed for the menu, a prefix
completed, the task run. It is regenerated from a live shell on every docs
build, so it cannot drift from what your terminal will do:

![Animated: fm TAB shows the task menu with descriptions, che TAB completes to check, and fm check runs to green receipts](_generated/shots/zsh-cast.svg)

## Install

```console
fm --install-completion zsh
```

This writes the hook to `$XDG_DATA_HOME/fm/completion.zsh` (default
`~/.local/share/fm/completion.zsh`) and appends one guarded `source` line to
the `.zshrc` zsh actually reads — under `$ZDOTDIR` when you've set one.
Running it twice changes nothing. If completion has never been initialised in
your setup (a fresh machine, a minimal rc), the hook runs `compinit` itself,
so there's nothing to arrange first.

For the **current session only** — no rc file touched:

```console
eval "$(fm --setup-completion zsh)"
```

## What you get

footman's zsh hook feeds candidates through `_describe`, the same completion
builtin `_git` and `_npm` use. Task and group names carry their one-line
docstring, right-aligned into a column:

```text
$ fm <TAB>
build    -- compile and bundle
deploy   -- ship to an environment
docs     -- Documentation
```

Because it's plain `compsys`, everything you already configure for zsh
completion — menu selection, colours, group formats — applies to `fm` with no
special cases.

## Colours and appearance

All styling goes through `zstyle`. The completion context for footman's
candidates ends in the command name, so use `:completion:*:*:fm:*` to scope a
setting to `fm` alone, or `:completion:*` to style everything at once. Some
useful recipes for your `.zshrc`:

```sh
# Dim the description column (everything after the " -- " separator).
zstyle ':completion:*:*:fm:*' list-colors '=(#b)*( -- *)=0=2'

# Or colour it — 38;5;N is a 256-colour index (244 = mid grey).
zstyle ':completion:*:*:fm:*' list-colors '=(#b)*( -- *)=0=38;5;244'

# A heading above the list, in colour.
zstyle ':completion:*:descriptions' format '%F{yellow}— %d —%f'

# Arrow-key menu selection, with a visible highlight on the current row.
zstyle ':completion:*' menu select
zstyle ':completion:*:*:fm:*' list-colors 'ma=48;5;24;38;5;255'
```

The `=(#b)pattern=default=capture` syntax is zsh's `list-colors` matching:
the parenthesised group gets the second colour spec (standard ANSI SGR
codes — `2` dim, `31`-`37` foreground, `48;5;N` background). The `--`
separator itself is a compsys default; change it per command with
`zstyle ':completion:*:*:fm:*' list-separator '·'` if you prefer.

Colours here are your shell's to decide — footman emits plain
`value<TAB>description` pairs and the hook hands them to `compsys`, so any
theme (or framework like oh-my-zsh) that styles completion styles `fm` too.

## Uninstall

```console
fm --uninstall-completion zsh
```

Removes the script and the `source` line from your `.zshrc`. (Everything the
installer did, undone; run it twice and the second run reports there's
nothing left to remove.)
