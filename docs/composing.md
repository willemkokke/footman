# Composing the task surface

A tasks file doesn't have to be a flat list you write by hand. footman treats
a task tree as a *value*: you can hide tasks with plain Python, disable them
with a reason, adopt tasks from other modules, and mount tasks a pip-installed
package advertises. One contract ties it together: everything resolves when
your code imports (so completion keeps answering from its cache), and
conditions re-check *live* when a task actually runs.

## Hiding vs disabling

Two different intents, two mechanisms — and the first one is deliberately
not a feature, because Python already has it:

**Hidden** — not in the tree, the listing, or completion. A tasks file is
executed code, so an `if` does exactly what it says:

```python
if sys.platform == "darwin":
    @task
    def notarize(app: Path): ...
```

**Disabled but listed** — pytest-skip semantics, for "this task exists but
can't run *here*":

```python
from footman import task, requires_tool

@task
@requires_tool("docker")
def up(detach: bool = True):
    "Start the dev containers."
```

```console
$ fm --list
Tasks:
  up  Start the dev containers.  (unavailable: requires docker on PATH)
$ fm up
fm: up: Unavailable: requires docker on PATH
```

The name always completes and lists — the manifest stays stable — and every
gate is re-evaluated **live** on every run, so the moment docker appears on
PATH, `fm up` works, whatever the cached manifest thought. `@requires_tool`,
`@requires_dep`, and `@requires_env` are the common gates — a tool on `PATH`, a
Python module importable, a variable set — and `@requires(predicate, reason=…)`
is the generic they build on. Stack as many as apply: **every** failure is
reported, each in its own words, so a task needing both a tool and a variable
says both. A predicate that raises reads as unavailable (a broken gate must not
swing open).

Keep the gates **below `@task`**, as above — `@task` on top, `@requires_*`
stacked beneath it. Either order *runs* (a gate sets an attribute the same task
object carries), but `@task` outermost is what keeps the task's typed signature
and `.opts()` in view for a type checker; flipped, the gate erases them. It also
reads the way it works: `@task` is the identity, the gates are modifiers under
it.

!!! warning "Keep a predicate cheap — it runs live"

    A gate's predicate runs **every time the manifest is built** — on every
    `fm --list`, every help render, and every background cache refresh — not
    only when the task runs. That liveness is the whole point (no stale
    availability), but it means a slow gate slows *listing*, not just
    execution. Keep predicates to a `which`, an `in os.environ`, a `find_spec`
    (which is what `@requires_tool`/`_env`/`_dep` already do); never a network
    call or a heavy import. The completion hot path is exempt — a `<Tab>` reads
    the baked reason from the cache and runs no predicate — but the refresh that
    fills that cache is not.

A `pre`/`post` dependency on a disabled task is a **hard failure**, not a
silent skip — silently dropping `lint` from `check` on the wrong machine is
how CI learns to lie. When you want the optional-dependency flow, compose the
list instead:

```python
@task(pre=[fmt, lint] + ([docker_up] if shutil.which("docker") else []))
def check(): ...
```

## Adopting tasks from another module — `include()`

```python
from footman import group, include

include("shared_tasks")                          # graft everything at root
include("shared_tasks", only=["lint", "fmt"])    # cherry-pick by CLI name
docs = group("docs", help="Docs")
include("mkdocs_helpers.tasks", into=docs)       # mounts under: fm docs …
```

`include()` imports the provider inside a registry capture, so its decorators
can't leak into your tree, then grafts what you asked for:

- **Collisions are loud** — a name you already have raises immediately; pass
  `override=True` when the shadowing is intended.
- **Typos are loud** — an unknown name in `only=`/`exclude=` is an error
  listing what the provider actually has.
- **Included tasks run from *your* directory** — a shared lint task lints
  this project, not the provider's install location.
- `--where lint` still points at the provider's source, so provenance is one
  flag away.

Two idioms worth knowing. Renaming a single task needs no machinery at all —
`@task` returns plain functions, so `task(name="fmt")(shared.fmt)` re-exports
one under a new name. And a bare `from shared_tasks import build` at the top
of a tasks file is the one form to avoid: the import executes the provider's
decorators against *your* registry, all-or-nothing, sensitive to import
order. `include()` exists so you never need it.

### A shared library with heavy or optional dependencies

Say you keep release tasks in a `devkit` library, and some need heavy
third-party packages (an API client, a cloud SDK). You want to
`include("devkit.tasks")` at the top of your monorepo's `tasks.py` without
paying those imports on every `fm lint`. You already can — it comes down to
where the heavy `import` lives:

```python
# devkit/tasks.py
from footman import task, requires_dep

@task
@requires_dep("stripe", reason="pip install devkit[release]")
def publish(version: str):
    "Cut and publish a release."
    import stripe          # imported only when publish actually runs
    ...
```

`include()` imports `devkit.tasks` to read task *signatures* for the
manifest, listing, and completion — it never runs a body. So a body-level
`import stripe` costs nothing until `fm publish` executes; `fm lint`,
`fm --list`, and every `<TAB>` stay clean. (Keep your CLI parameter types
cheap — `version: str`, `dry_run: bool` — for the same reason; an exotic
annotation is the one thing signature introspection might try to resolve.)

`@requires_dep` closes the last gap: the *optional* dependency. It names modules
the task needs, checked with `importlib.util.find_spec` — which locates them
**without importing** — so a missing package makes the task list as
`(unavailable: pip install devkit[release])` and refuse to run with that
message, instead of crashing with a raw `ModuleNotFoundError`. Installed or
not, the check never imports the package; your body still does, only when it
runs. (`find_spec` is import-free for a top-level distribution; a deeply
dotted name like `google.cloud.storage` imports its parent packages, so name
the top-level dist where you can.)

## Packages advertising tasks — `footman.tasks` entry points

A package publishes a `Group` under the `footman.tasks` entry point:

```toml
# the plugin package's pyproject.toml
[project.entry-points."footman.tasks"]
mkdocs = "footman_mkdocs:tasks"
```

```python
# footman_mkdocs/__init__.py
from footman import Group, requires_tool

tasks = Group("mkdocs", help="MkDocs site tasks")

@tasks.task
def build(strict: bool = True): ...

@tasks.task
@requires_tool("mike")
def deploy(version: str): ...
```

And a project **opts in** through config:

```toml
# pyproject.toml (or footman.toml)
[tool.footman]
plugins = ["mkdocs"]        # mounts as `fm mkdocs build`, `fm mkdocs deploy`
```

A plugin's **name is its command path**, so a dotted name nests — one group
per segment — and plugins that share a prefix meet under one namespace group
without either owning it:

```toml
plugins = ["footman.docs", "footman.tools"]   # `fm footman docs …`, `fm footman tools …`
```

or adopts pieces of it from a tasks file, composing with `include()`:

```python
from footman import include, plugin

include(plugin("mkdocs"), only=["build"])        # flat: `fm build`
```

Design choices you can rely on:

- **Never auto-loaded.** `pip install something` growing your command
  surface unasked is a supply-chain surprise; the task surface stays
  reproducible from the files in your repo. The `importlib.metadata` scan
  runs only when `plugins` is configured, only on the execution path — the
  completion hot path never changes, and footman stays zero-dependency.
- **A missing plugin is a crisp error** naming the entry points that *are*
  installed — a typo or a missing install should read as one.
- **Your names win.** A task or group you define shadows a plugin group of
  the same name silently, exactly as nearer cascade files shadow farther
  ones. One rule of thumb: *config mounts a tool; tasks.py adopts a task.*
- Config-mounted plugin tasks run from your invocation directory;
  `include()`-adopted tasks run from the including file's directory.

footman ships two first-party plugins, `footman.docs` and `footman.tools` —
dotted names that share the `footman` namespace group without either owning it
(there is no plugin named plain `footman`). Mounting `footman.docs` is the
two-line demo of this whole mechanism, and what it mounts is
[your tasks, documented](taskdocs.md) (`fm footman docs page` / `site`);
`footman.tools` mounts the maintainer-facing stub toolkit under
`fm footman tools …`. A naming symmetry to know: the `footman.tasks`
entry-point *group* is served by the `footman.tasks` *package* — different
namespaces, one product.

## Editing the discovered tree

Sometimes a policy spans many tasks — every `deploy-*` task gets an `audit`
step first, a handful of tasks are switched off in this checkout — and editing
each `@task` by hand is the wrong tool. `@finalize` registers a hook that runs
once on the **fully-merged** tree, at discovery, before anything dispatches.
It is footman's `pytest_collection_modifyitems`.

```python
# repo/tasks.py
import footman
from footman import task

@task
def audit(): ...

@footman.finalize
def gate_deploys(tasks):
    for t in tasks:
        if t.name.startswith("deploy") and "audit" in tasks:
            t.add_pre(tasks["audit"])
```

The hook is handed a `Tasks` view of the merged tree — iterate it for every
task, or index it by command-line name (`tasks["deploy-web"]`). Each task comes
back as a `TaskView`:

- **wiring** — `t.name`, `t.group` (the owning group, or `None` at top level),
  `t.pre`, `t.post`, `t.disabled`;
- **policy flags** — `t.keep_going`, `t.atomic`, `t.infinite`, `t.interactive`,
  `t.timed`, `t.confirm`;
- **cascade provenance** — `t.defining_dir` (the folder it was defined in),
  `t.shadowed` (the task it overrides one level up), `t.shadow_chain`, and
  `t.source_file`;
- **edits** — `t.add_pre(…)`, `t.add_post(…)`, `t.disable("reason")`, and
  `t.set_opts(…)` (permanent, tree-wide policy — the finalize-time counterpart
  to a per-use `.opts()`).

`t.fn` is the underlying function if you need to reach past the view — which
deliberately keeps footman's private task attributes out of your hooks.

Provenance lets a finalizer decide by *where* a task came from. To gate every
task defined under an `infra/` folder, regardless of its name:

```python
@footman.finalize
def gate_infra(tasks):
    for t in tasks:
        if (t.defining_dir or "").endswith("infra"):
            t.add_pre(tasks["audit"])
```

Because a finalizer runs **at discovery**, its edits are part of the plan, not
a runtime surprise: an added `pre` runs and shows in `fm <task> --dry-run`, and
a disabled task drops from `--list`, `--help`, and <kbd>Tab</kbd> completion —
exactly as if you had written it into the task.

In a [monorepo](monorepos.md), a **root** `tasks.py` can finalize a subfolder's
tasks, because the hook sees the whole merged tree. When several files in the
cascade each register a finalizer, they run in **cascade order** — root first,
the folder nearest your cwd last, each seeing the previous edits — the same
"local overrides global" precedence the cascade itself uses, so a subfolder
refines what root did.

## The caching contract, stated once

Hiding, `include()`, `plugin()`, and `@finalize` all resolve at
import/manifest-build time, so what completion offers reflects the *last real
run* — the same contract dynamic `suggest()` choices have always had.
Availability (`@requires`) is the one thing never trusted from the cache: it
re-checks live at the moment of execution.
