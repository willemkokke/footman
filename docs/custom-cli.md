# Custom CLI

footman is a library first: `fm` and `footman` are just the default-branded
instance of a public `App`. Point your own console script at an `App` carrying
your project's names and version, and every message the user sees — errors,
`--version`, hints — uses *your* branding instead of footman's.

This is how you ship an internal tool under its own name (say `hse`) that is
still footman underneath.

## Build a branded entry point

```python
# hse/cli.py
from footman import App

app = App(
    name="HSE",        # long / display name  → the --version banner
    prog="hse",        # short / command name → "hse: ..." errors and hints
    version="1.4.0",   # YOUR version, not footman's
)

def main() -> None:
    raise SystemExit(app.run())
```

Register it as a console script in your package:

```toml
# hse/pyproject.toml
[project.scripts]
hse = "hse.cli:main"
```

Now your tool is fully rebranded:

```console
$ hse --version
HSE 1.4.0

$ hse nonexistent-task
hse: expected a task name, got 'nonexistent-task' (know: build, test, deploy)
```

## Where the two names show up

| Setting     | Used for                                                    |
| ----------- | ----------------------------------------------------------- |
| `name`      | the `--version` banner and any display heading (long name)  |
| `prog`      | the error prefix (`hse: …`) and the completion hint (short) |
| `version`   | the `--version` output — your project's version             |

`version` is optional; omit it and footman's own version is used.

## Tasks and completion are unchanged

Your branded CLI discovers tasks exactly like `fm`: the
[`tasks.py` cascade](monorepos.md) from the repo root down to the current
directory. Completion works through your binary too —
`hse --complete …` — and stays on the same stdlib-only fast path, because
`App.run()` handles `--complete` before importing the framework.

!!! tip "Keep completion fast"

    If your entry-point module imports heavy code at the top (your task
    definitions, third-party libraries), you pay that cost on every
    <kbd>Tab</kbd>. Keep `hse/cli.py` lean — build the `App` and nothing else —
    and let the `tasks.py` cascade carry the tasks.
