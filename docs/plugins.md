# Writing plugins

A footman plugin is a Python module that contributes to the task tree: tasks
and groups, lifecycle hooks, global options — any of them, in any mix. There
is no plugin API beside the one tasks files already use; a plugin is a tasks
file that lives in a package, which is the design working, not a shortcut.
The model to have in mind is pytest's: a plugin ships hookimpls and options,
the user switches it on, and everything it adds obeys the same rules as
hand-written code — never a dialect.

## A worked provider

One module, three kinds of contribution:

<!-- example: fragment -->
```python
# acme_devkit/footman_tasks.py
from pathlib import Path
import footman
from footman import GlobalOption, task

REGION = GlobalOption("region", str, default="eu", help="deployment region")

@task(uses=[REGION])
def deploy():
    "Deploy to the configured region."
    footman.run(f"./deploy --region={REGION.value}")

@footman.wrap_task
def audited(inv, task):
    entry = {"task": task.name, "args": dict(task.args)}
    result = yield
    ledger.write({**entry, "ok": result.ok, "took": result.duration})

@footman.pre_tasks
def wire(inv):
    if "audit" in inv.tasks:
        for t in inv.tasks:
            if t.name.startswith("deploy"):
                t.add_pre(inv.tasks["audit"])
```

Importing this module registers everything in it — decorators and
`GlobalOption` constructions alike. When footman pulls it, that import runs
inside a **registry capture**, so nothing leaks into the puller's tree except
what the pull grafts deliberately.

## The entry point

Advertise the module (or a specific `Group` in it) under the
`footman.tasks` entry-point group — the console-script of task trees:

```toml
[project.entry-points."footman.tasks"]
"acme.devkit" = "acme_devkit.footman_tasks"
```

A target with an attribute (`pkg.mod:tasks`) names one `Group`; a bare
module target contributes whatever the module registers — which is how a
**lifecycle-only** plugin (hooks and options, not a single task) is a valid
provider. Footman's own `footman.docs`,
`footman.env_files` and `footman.profile` are declared exactly this way.

## Being pulled

An installed plugin is inert metadata until a tasks file says otherwise:

<!-- example: fragment -->
```python
from footman.compose import plugin

plugin("acme.devkit", into="acme")     # fm acme.deploy
```

Everything follows the composing rules from there: the user's own names win
silently, two plugins clashing on one address is loud, provenance is
stamped, and `fm --plugins` lists what is installed against what is pulled.
A `GlobalOption` exists on the command line exactly when its owner is
pulled; unpulled, it is an unknown option, taught.

An option may be named without a value: `--profile` beside
`--profile=out.json`. A bare mention carries no value — `.value` is whatever
the option would have had anyway — and carries *presence*, which `.given`
reports:

<!-- example: fragment -->

```python
PROFILE = GlobalOption("profile", Path, default=Path("fm-profile.json"))

if PROFILE.given:            # `--profile` writes the default file,
    write_trace(PROFILE.value)   # no `--profile` writes nothing at all
```

Three outcomes from one declared value: absent, named, named with a value.
`.value` answers *what*, `.given` answers *whether anyone asked* — and an
`env()` fallback fills the first without touching the second, so a plugin that
wants the environment alone to count can say so, and one that does not can say
that instead.

## Configuration

`inv.config` carries the merged settings table. The convention is one
sub-table per plugin, named by the entry point:

```toml
[tool.footman."acme.devkit"]
region = "us"
```

```python
import footman

@footman.pre_tasks
def configure(inv):
    settings = inv.config.get("acme.devkit", {})
```

The name is the identity users already typed in `plugin(...)`, so nothing
new has to be learned or collided.

!!! note "Under a branded CLI, the outer table is the brand's"

    A [custom CLI](custom-cli.md) reads `[tool.acme]`, not `[tool.footman]` —
    two branded runners in one repo keep their own settings instead of
    fighting over one table. Your sub-table follows it, so the same plugin is
    configured at `[tool.acme."acme.devkit"]` there. Write the outer name your
    users' runner answers to; the inner one is yours either way.

A sub-table is also what keeps plugins from colliding over settings: two
plugins may both read a `region` key, because each reads its own. Their
*flags* are a different namespace — `--region` is claimed once across the whole
tree, and two plugins asking for it is a taught refusal naming both. So a
plugin whose flag has to be `--acme-region` can still call the setting `region`
inside its own section, where nothing else can see it.

## Optional dependencies

Core footman has no runtime dependencies; **plugins are not bound by that
invariant** — but an import must never crash a listing. Two patterns, by
surface:

- A *task* that needs a package gates itself:
  `@requires_dep("rich", reason="renders the report")` — listed with the
  reason, runnable the moment the package appears.
- A *hook* has no task to gate, so it imports lazily and teaches at the
  moment it is asked to act — `footman.env_files` does exactly this with
  python-dotenv: pulled and missing its dependency, the run refuses naming
  the package and the pull; unpulled, silence.

## The determinism rule

`pre_tasks` runs at discovery — including inside the detached child that
rebuilds the completion manifest, which has **no command line**. So tree and
availability edits must derive from files, config and environment, never
from `inv.cli`; the manifest is written from declarations, never
executions. Per-task truth belongs to the per-task moments, which run only
in real invocations.

## Testing a plugin

`Runner` drives the real pull path end to end — a tasks file that pulls
you, a tmp directory, assertions on the report:

```python
from footman.testing import Runner

def test_the_option_reaches_the_task(tmp_path):
    (tmp_path / "tasks.py").write_text(
        'from footman.compose import plugin\nplugin("acme.devkit")\n'
    )
    result = Runner().invoke("--region=us deploy", tasks=tmp_path / "tasks.py")
    assert result.ok
```

Entry points resolve from installed metadata, so the test environment
installs your package (editable is fine) — exactly what your users' will do.
