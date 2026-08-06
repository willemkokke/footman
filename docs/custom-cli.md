# Custom CLI

Footman is a library first: `fm` and `footman` are just the default-branded
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
acme: expected a task name, got 'nonexistent-task' (know: build, test, deploy)
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
