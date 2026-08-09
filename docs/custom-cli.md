# Custom CLI

Footman is a library first: `fm` and `footman` are the default-branded
instance of a public `App`. Point your own console script at an `App` carrying
your project's names and version, and every message the user sees — errors,
`--version`, hints — uses *your* branding instead of footman's.

This is how you ship an internal tool under its own name (say `acme`) that is
still footman underneath.

## Build a branded entry point

```python
# acme/cli.py
from footman import App

app = App(
    name="Acme",        # long / display name  → the --version banner
    prog="acme",        # short / command name → "acme: ..." errors and hints
    version="1.4.0",   # YOUR version, not footman's
)

def main() -> None:
    raise SystemExit(app.run())
```

A brand can also rename the tasks file its users write:
`App(..., tasks_file="acmetasks.py")`. Per-project config (`tasks` in
`[tool.footman]`) still overrides it, and background completion
refreshes honour it — the filename rides inside the cached manifest.

Register it as a console script in your package:

```toml
# acme/pyproject.toml
[project.scripts]
acme = "acme.cli:main"
```

Now your tool is fully rebranded:

```console
$ acme --version
Acme 1.4.0

$ acme nonexistent-task
acme: no task named 'nonexistent-task' (know: build, test, deploy)
```

## Where the two names show up

| Setting     | Used for                                                    |
| ----------- | ----------------------------------------------------------- |
| `name`      | the `--version` banner and any display heading (long name)  |
| `prog`      | the error prefix (`acme: …`) and the completion hint (short) |
| `version`   | the `--version` output — your project's version             |

`version` is optional; omit it and footman's own version is used.

## Tasks and completion are unchanged

Your branded CLI discovers tasks exactly like `fm`: the
[`tasks.py` cascade](monorepos.md) from the repo root down to the current
directory. Completion works through your binary too —
`acme --complete …` — and stays on the same stdlib-only fast path, because
`App.run()` handles `--complete` before importing the framework.

!!! tip "Keep completion fast"

    If your entry-point module imports heavy code at the top (your task
    definitions, third-party libraries), you pay that cost on every
    <kbd>Tab</kbd>. Keep `acme/cli.py` lean — build the `App` and nothing else —
    and let the `tasks.py` cascade carry the tasks.

## The uv handoff follows the brand

A globally installed branded CLI hands off exactly as `fm` does: when the
project's `uv.lock` pins footman and you aren't inside its interpreter environment,
the handoff re-execs the *(branded)* footman you invoked — `acme` hands
off to the project's own `acme`, never to `fm`. The prog you typed is the
prog that runs; only the version moves.

The second rule — a tasks file that declares its own dependencies — needs
one thing more, because it has to know which distribution ships *your*
runner:

```python
app = App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli")
```

With `dist` set, a tasks file carrying a
[PEP 723](https://peps.python.org/pep-0723/) header runs in its own
script environment under `acme` too — and must list `acme-cli` among its
dependencies, the way a footman one lists `footman`. Left unset, footman
never guesses a distribution into a script environment: the rule stays out of
your way, and your users' tasks files run exactly where they already did.

## A home of your own

Your CLI is a product; footman is a dependency inside it. Give it a `home` and
everything it keeps goes there — completion manifests, timing history, the
collector's stamp, the user-level config file, and the user tasks file:

```python
from pathlib import Path

app = App(name="Acme", prog="acme", home=Path.home() / ".acme")
```

**You** compute the path, so footman never has to guess at your product's
layout. `ACME_HOME` overrides it at run time, which is what lets two
installations run side by side under different identities.

Left unset, with that variable unset too, locations fall back to `~/.cache` and
`~/.config` as they always have.

## Environment variables follow the brand

`acme` reads `ACME_HOME`, `ACME_CACHE_DIR`, `ACME_CONFIG`, `ACME_CASCADE`,
`ACME_NO_GC` and `ACME_NO_UV` — never footman's. That isolation is the point:
someone debugging `fm` with `FOOTMAN_CACHE_DIR` set must not silently relocate
your product's cache. Error messages name your spelling too, so a user is never
taught a variable that does nothing for them.

The prefix is `prog` uppercased — the command is `acme`, so the variables are
`ACME_*`. Set `env_prefix` when that isn't what you want, for either of two
reasons.

**A terse command makes a poor variable.** footman's own command is `fm`, but
its variables are `FOOTMAN_*`: `FOOTMAN_HOME` tells a reader who has never
heard of the tool what it belongs to, and is something they can search for.
`FM_HOME` is neither. If your command is two or three letters, pin a longer
prefix.

!!! warning "The namespace is yours to keep clear"

    Because `ACME_HOME` is read like every other variable, a product that
    already uses `ACME_HOME` to mean something broader should give the runner
    its own namespace and compute `home` from the product's variable:

    ```python
    import os
    from pathlib import Path

    app = App(
        name="Acme",
        prog="acme",
        env_prefix="ACME_RUNNER",
        home=Path(os.environ.get("ACME_HOME", Path.home() / ".acme")) / "runner",
    )
    ```

    footman never guesses which of your variables is which — one prefix, and
    where it points is your decision.

## Config files follow the brand too

Your users write `acme.toml`, or a `[tool.acme]` table in `pyproject.toml` —
not footman's. Both come from one field, so they cannot drift apart:

```python
app = App(name="Acme", prog="acme", config_name="acme")   # the default is `name`
```

Two branded CLIs can then live in one repository, each reading its own
settings instead of fighting over a shared table.

## Your users' own tasks

With a home set, `<home>/tasks.py` (or whatever your `tasks_file` is called)
holds a user's personal tasks, available wherever they have no project.

It is a *fallback*, not a rung: the moment a project's cascade finds a tasks
file, that cascade wins outright. There is one way to get tasks into a project
tree — pull them in a tasks file — and this does not become a second one.
