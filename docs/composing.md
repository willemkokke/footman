# Composing the task surface

A tasks file doesn't have to be a flat list you write by hand. footman treats
a task tree as a *value*: you can hide tasks with plain Python, disable them
with a reason, adopt tasks from other modules, and mount tasks a pip-installed
package advertises. One contract ties it together: everything resolves when
your code imports (so completion keeps answering from its cache), and
conditions re-check *live* when a task actually runs.

## Hidden, omitted, disabled

Three different intents, and the difference is what happens when someone
names the task anyway:

**Hidden — listed nowhere, callable as ever.** For the tasks a machine calls
and a human never types: a CI entry point, a step another task drives.

```python
@task(hidden=True)
def ci_publish(): ...
```

It drops out of `--list`, `--tree`, group help, the did-you-mean index and
completion. Everything else is untouched: `fm ci-publish` runs it, a
`pre=`/`post=` dependency runs it, a runnable group's empty-body fan-out
still includes it, and `--json` reports it *marked* rather than missing —
a machine is exactly who calls it, so the catalog keeps it. The generated
task docs list it too, badged, because the docs are where you look up
something the listings won't offer.

`hidden` is inherited: unset means "whatever my group said", so one
declaration hides a whole subtree, and a child can still come back.

```python
internal = group("internal", hidden=True)   # the whole subtree, one word

@internal.task
def sweep(): ...                            # hidden, like its group

@internal.task(hidden=False)
def status(): ...                           # listed again, deliberately
```

Setting it on a `@group.default` says the same thing about the group it
speaks for. A group whose every task ends up hidden prints no heading at
all, rather than an empty one.

**Omitted — the task does not exist.** A tasks file is executed code, so an
`if` does exactly what it says: no address, nothing to call, nothing to
list. Reach for it when the task is *meaningless* here, not merely
uninteresting to type.

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

## Two typed verbs over one engine

Composition is two sibling verbs sharing one engine — resolution differs,
everything after it (walk, land, filter, merge) is the same code:

- **`plugin("acme.devkit.lint")`** pulls from an installed package's
  **`footman.tasks` entry point** — the console-script of task trees: a
  stable public identity for a Group the package offers, enumerable,
  inert until pulled.
- **`include("mytasks.lint")`** pulls from an **importable module** — the
  same grammar over your own reach: file-splitting, monorepo-local sharing.

The type tag lives in the verb, so no string is ever resolved against both
registries — there is no precedence and no silent re-pointing when a new
package lands. The model is Python imports: `plugin("acme.devkit.lint")` is
`from acme_devkit import lint` for task trees; pulling a whole container is
the `import *` — safe here, because your own definitions silently win and
imported-vs-imported clashes are loud.

## Pulling from your own modules — `include()`

```python
from footman import include

include("shared_tasks")                          # everything, at root
include("shared_tasks", only=["lint", "fmt"])    # cherry-pick children
include("mytasks.lint")                          # one group out of a module
include("mkdocs_helpers.tasks", into="docs")     # namespaced: fm docs.…
```

The longest importable prefix is imported inside a registry capture (the
provider's decorators can't leak into your tree); the rest of the string
walks the captured tree, so `include("mytasks.lint")` pulls one group out
of a module the way an import pulls one name. Then the engine grafts:

- **A node lands under its own name.** `include("mytasks.lint")` → `fm
  lint`. A whole module is an anonymous container, so pulling it lands its
  *children* — the splat. `into=` (a dotted address, created on demand) is
  your placement; there is no rename.
- **Your names win, silently** — a task or group you define shadows a
  pulled one of the same name, whatever the file order, exactly as nearer
  cascade files shadow farther ones.
- **Pull-vs-pull clashes are loud** — a same-address leaf from two pulls
  raises, citing both providers; pass `override=True` when the later pull
  should win. Group-vs-group is composition, never a clash: two pulls into
  one subtree merge all the way down.
- **Filters take full dotted addresses**, relative to the pulled node —
  `only=["docs.build", "fmt"]` grafts one nested task and one flat one,
  materialising the path (the intermediate groups are the source's own
  copies, help text riding along). Matching is exact; the whole-group
  spelling `only=["docs"]` *is* the glob, and `only=["docs",
  "docs.build"]` is redundant, not an error. A group pruned empty is
  dropped entirely. Default-ness survives only if the default survives —
  the default is the child named `default`, so `only=["lint.default"]`
  grafts *just* the default and `exclude=["lint.default"]` grafts
  everything but it, readable without ever opening the provider's source.
- **Typos are loud** — an unknown filter address errors per segment,
  naming what that level actually has.
- **Included tasks run from *your* directory** — a shared lint task lints
  this project, not the provider's install location.
- `--where=lint` still points at the provider's source, so provenance is
  one flag away — and `fm --plugins` lists every installed entry point,
  pulled or not, with where it landed.

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

## Pulling from installed packages — `plugin()`

A package publishes a `Group` under the `footman.tasks` entry point:

```toml
# the plugin package's pyproject.toml
[project.entry-points."footman.tasks"]
"acme.mkdocs" = "acme_mkdocs:tasks"
```

```python
# acme_mkdocs/__init__.py
from footman import Group, requires_tool

tasks = Group("mkdocs", help="MkDocs site tasks")

@tasks.task
def build(strict: bool = True): ...

@tasks.task
@requires_tool("mike")
def deploy(version: str): ...
```

And a project **opts in** with a pull line in its tasks file:

```python
from footman import plugin

plugin("acme.mkdocs")                        # fm mkdocs.build, fm mkdocs.deploy
plugin("acme.mkdocs", only=["build"])        # just one child
plugin("acme.mkdocs.build", into="site")     # one task, placed by you
plugin("acme.devkit")                        # a container of groups: the splat
```

The longest installed entry-point name is the **identity** — consumed at
resolve time, retained as provenance — and the rest of the string walks the
advertised tree, dots continuing seamlessly. The pulled node lands under
its **own name**: the identity (`acme.mkdocs`) never becomes an address,
and placement is always yours (`into=`). A provider advertising a whole
container of groups splats them — one line adopts a devkit, and a devkit
update that adds a group just appears on the next pull.

Design choices you can rely on:

- **Never auto-loaded.** `pip install something` growing your command
  surface unasked is a supply-chain surprise; the task surface stays
  reproducible from the files in your repo. The `importlib.metadata` scan
  runs only when a pull line asks for it, only on the execution path — the
  completion hot path never changes, and footman stays zero-dependency.
- **A missing plugin is a crisp error** naming the entry points that *are*
  installed — a typo or a missing install should read as one. `fm
  --plugins` lists them all, marked pulled-or-not and where they landed,
  so "installed but nobody pulled it" is visible.
- **Your names win.** A task or group you define shadows a pulled one of
  the same name silently; two pulls clashing at one leaf is loud, and the
  message cites both identities.
- **Publisher convention:** advertise either one named group (an ecosystem
  plugin) or a container of groups (a devkit — it splats). Loose tasks in
  a published container are a smell: the splat drops them straight into
  every consumer's top level. Entry-point names stay vendor-prefixed
  (`acme.devkit`) — identity hygiene in the shared registry, not address
  design.

footman's own tooling follows the same rule — built-ins are ordinary,
opt-in plugins. This repo's tasks.py pulls both of its first-party
plugins: `plugin("footman.docs", into="footman")` is [your tasks,
documented](taskdocs.md) (`fm docs.page` / `site`), and
`plugin("footman.tools", into="footman")` is the maintainer-facing stub
toolkit. A branded CLI writes `into="acme.tools"` instead — branding is a
one-line authoring choice, not framework machinery. A naming symmetry to
know: the `footman.tasks` entry-point *group* is served by the
`footman.tasks` *package* — different namespaces, one product.

## Editing the discovered tree

Sometimes a policy spans many tasks — every `deploy-*` task gets an `audit`
step first, a handful of tasks are switched off in this checkout — and editing
each `@task` by hand is the wrong tool. `@pre_tasks` registers a hook that runs
once per invocation on the **fully-merged** tree, before availability gates, the
manifest, or any task. It is footman's `pytest_collection_modifyitems`.

```python
# repo/tasks.py
import footman
from footman import task

@task
def audit(): ...

@footman.pre_tasks
def gate_deploys(inv):
    for t in inv.tasks:
        if t.name.startswith("deploy") and "audit" in inv.tasks:
            t.add_pre(inv.tasks["audit"])
```

The hook is handed the **`Invocation`**: what this `fm` line is doing, and the
one object every lifecycle hook sees. `inv.tasks` is a `Tasks` view of the
merged tree — iterate it for every task, or index it by command-line name
(`inv.tasks["deploy-web"]`). Each task comes back as a `TaskView`:

- **wiring** — `t.name`, `t.group` (the owning group, or `None` at top level),
  `t.pre`, `t.post`, `t.disabled`;
- **policy flags** — `t.keep_going`, `t.atomic`, `t.infinite`, `t.interactive`,
  `t.timed`, `t.confirm`;
- **cascade provenance** — `t.defining_dir` (the folder it was defined in),
  `t.shadowed` (the task it overrides one level up), `t.shadow_chain`, and
  `t.source_file`;
- **edits** — `t.add_pre(…)`, `t.add_post(…)`, `t.disable("reason")`, and
  `t.set_opts(…)` (permanent, tree-wide policy — the discovery-time counterpart
  to a per-use `.opts()`).

`t.fn` is the underlying function if you need to reach past the view — which
deliberately keeps footman's private task attributes out of your hooks.

Provenance lets a hook decide by *where* a task came from. To gate every
task defined under an `infra/` folder, regardless of its name:

```python
@footman.pre_tasks
def gate_infra(inv):
    for t in inv.tasks:
        if (t.defining_dir or "").endswith("infra"):
            t.add_pre(inv.tasks["audit"])
```

Because the hook runs **at discovery**, its edits are part of the plan, not
a runtime surprise: an added `pre` runs and shows in `fm <task> --dry-run`, and
a disabled task drops from `--list`, `--help`, and <kbd>Tab</kbd> completion —
exactly as if you had written it into the task.

In a [monorepo](monorepos.md), a **root** `tasks.py` can edit a subfolder's
tasks, because the hook sees the whole merged tree. When several files in the
cascade each register a hook, they run in **cascade order** — root first,
the folder nearest your cwd last, each seeing the previous edits — the same
"local overrides global" precedence the cascade itself uses, so a subfolder
refines what root did.

## Around every task: `@pre_task` and `@post_task`

Where `@pre_tasks` runs once over the plan, the per-task pair runs around
every **execution** — a chain segment, a prerequisite, a fan-out member, a
body call all count the same. `pre_task(inv, task)` fires after binding, so
it sees the arguments the body actually receives; `post_task(inv, task,
result)` fires after the body, whatever the outcome. Both run on the task's
worker thread, in parallel across tasks:

```python
import footman

@footman.pre_task
def open_span(inv, task):
    task.state.span = tracer.start(task.name, dict(task.args))

@footman.post_task
def close_span(inv, task, result):
    task.state.span.end(ok=result.ok, took=result.duration)
```

The `task` handle carries the execution's facts and the two lanes a hook may
write through:

- **`task.name`** — the address the task was reached through; **`task.args`**
  — the bound arguments, defaults included, read-only; **`task.source_hash`**
  — a digest of the task's own body (a tripwire, not an identity: `None` when
  the source can't be read, and shallow — it covers nothing the body calls).
- **`task.state`** — scratch private to *your plugin and this execution*,
  delivered back to your `post_task`. Another plugin's hooks cannot see it,
  and the next execution starts clean.
- **`task.env`** — the task's own environment overlay: `run()` merges it into
  every subprocess, and in-body `os.environ` reads see it. Never write
  `os.environ` from a per-task hook — that is shared with every parallel
  sibling; `inv` is frozen here for the same reason.

`result` reads everything — `ok`, `code`, `returned`, `error`, `duration`,
`output`, `steps` — and writes one thing: `set_returned(value)`, which
rewrites the **reported** value (the summary and the `--json` envelope),
never what a dependent or a body caller received. A `shared` row starts out
reporting what its requester was actually handed — and since the posts fire
on it too, a reporter that rewrites the execution's row can rewrite the
shares the same way.

Pres run in plugin order and posts unwind in reverse, so the first plugin in
speaks last. The post is the **task-finished event**: once an execution
reaches the body stage, every registered `post_task` fires when it concludes
— whether or not that plugin registered a `pre_task`, and however any pre
fared — so a span opened in a pre always closes, even when another plugin's
pre killed the task. Failures are loud and named: a raising `pre_task` fails
the task like a failed prerequisite (the body never runs), and a raising
`post_task` fails an otherwise-green task — a reporter that crashed must not
pass silently. Under `--dry-run` nothing executes, so nothing fires.

**The pair is per request; only the body is shared.** A request satisfied by
an execution the run already performed still gets the whole ladder — its
`pre_task` fires post-bind, before the wait, and its `post_task` closes it
with the `shared` row — so pairing never depends on sharing, and a span
opened for a request always closes. `result.state == "shared"` is how a
reporter that cares tells a share from a run; one that doesn't care never
has to think about it.

### Before binding: `@pre_bind`

One moment sits earlier still. `pre_bind(inv, task)` fires before the task's
parameters are bound, so what it writes into `task.env` is what `env()`
fallbacks resolve, what coercion sees, and what `check(fn)` validators read —
the one moment a plugin can influence what the body will be handed:

```python
@footman.pre_bind
def credentials(inv, task):
    task.env["DEPLOY_TOKEN"] = vault.read("deploy")

@task
def deploy(token: Annotated[str, env("DEPLOY_TOKEN")] = ""): ...
```

Nothing is bound yet, so `task.args` is not readable here — read values in
`pre_task`, the post-bind moment. The same handle carries through the whole
ladder (`pre_bind → bind → pre_task → body → post_task`), so state set at
`pre_bind` is there at `post_task`. A body call binds like a segment, and its
binding sees the same injected environment.

One boundary fact, stated plainly: **a bind failure still fires the posts**
— the attempt concluded, a bind-time span needs closing — with the refusal
as the result. Everything else follows the one rule above: the ladder is per
request, only the body is shared.

The window the ladder runs in is the task's managed window, opened before
binding: hook code and validator code answer to the same rules a body does
(an `os.environ` write is captured into the task's overlay, a prompt outside
an interactive task is refused), while footman's own prompts — `ask()`
menus, `confirm=` — use the real terminal and are never caught.

### After the run: `@post_tasks`

The closing bookend to `@pre_tasks`: once per invocation, on the main
thread, after every task has concluded and *before* the summary or the
`--json` envelope prints — so a rewrite a hook makes through a result view
is what gets reported. The invocation now carries the whole story:

```python
@footman.post_tasks
def digest(inv):
    failed = [r for r in inv.results if not r.ok]
    slack.post(f"{len(failed)} failed of {len(inv.results)}, "
               f"{inv.total_ms:.0f} ms")
```

`inv.results` is every row, chronological — executions, `shared` rows,
refusals, and `skipped` nodes (`inv.skipped` is that subset). This is the
moment that sees what never ran: a `post_task` reporter only meets requests
whose ladder opened, so the run-level view is where "what didn't happen"
becomes visible. Under `--json` anything a hook prints goes to stderr — the
envelope owns stdout. Hooks run in cascade order; a raising hook is named
and fails the invocation, exactly as a crashing reporter should.

### One generator instead of a pair: `@wrap_task` and `@wrap_bind`

When the pre and the post are two halves of one thought — open a span, close
it; start a clock, log it — a wrapper says it in one place, with locals
instead of `task.state` and `try/finally` doing the pairing:

```python
@footman.wrap_task
def span(inv, task):
    s = tracer.start(task.name, dict(task.args))
    result = yield                 # the body runs here
    s.end(ok=result.ok, took=result.duration)
```

`wrap_task` is sugar over the pair — it enters at the `pre_task` moment and
is resumed with the `ResultView` — so every rule above is its rule too: per
request (a request satisfied by an execution the run already performed is
resumed with its `shared` row), reverse unwinding, a raising half failing
the task, named. The family grammar: `pre_X` runs *before* moment X,
`wrap_X` *enters at* X and rides to the end.

The one thing `wrap_task` cannot see is a task that failed to **bind** — its
anchor moment never fires, so there is no generator to unwind. `wrap_bind`
enters at the bind boundary, takes two yields, and closes even then:

```python
@footman.wrap_bind
def audit(inv, task):
    started = clock()
    try:
        bound = yield              # after binding: the real values
        result = yield             # after the body: the outcome
    finally:
        log(task.name, clock() - started)
```

A failed bind arrives as the failure raised at the first yield, so the
`try/finally` (or an `except`) around it observes it and still closes.
Yield-count violations are taught, naming the wrapper: `wrap_task` takes
exactly one, `wrap_bind` exactly two.

One more contract, stated for the future: a pre hook's **return value is
reserved** — the moment where a pre supplies the task's result and the body
is skipped belongs to a later cache. Today a returned value gets a note and
is ignored; state belongs on `task.state`. (A wrapper never touches the
channel: its `yield` is the moment itself.)

## A plugin's own globals: `GlobalOption`

A plugin whose behaviour is invocation-wide wants an invocation-wide switch —
`--env-file=…` beside `--jobs=…`, not a flag repeated on every task.
Constructing a `GlobalOption` **is** registering it: a module-level singleton
in the provider, stamped with the module that defined it, riding the same
carriage as lifecycle hooks — so it reaches a run only when its owner is
pulled, and an unpulled owner's option is an unknown option, taught.

```python
from pathlib import Path
from footman import GlobalOption

ENV_FILE = GlobalOption("env-file", Path, help="load this .env file first")
AUDIT = GlobalOption("audit", help="report, change nothing")   # bool → a flag
```

The value is `=`-attached like every option's, long-form only, coerced and
validated through the same pipeline as a task parameter — `Literal` choices,
`Path` file completion and bounds all work, because the manifest describes it
with the same machinery and <kbd>Tab</kbd> answers from that. Read it
anywhere in-run as `ENV_FILE.value` (parsed once, frozen for the run; outside
a run the read is a taught error). Cross-plugin use is an ordinary import of
the singleton.

A task that reads one says so — `@task(uses=[ENV_FILE])` — which puts the
dependency in the manifest for help and agents; an undeclared read still
works, with a note naming the fix. Names collide loudly: with footman's own
globals naming footman, between two plugins naming both owners.

## The caching contract, stated once

Hiding, `include()`, `plugin()`, and `@pre_tasks` all resolve at
import/manifest-build time, so what completion offers reflects the *last real
run* — the same contract dynamic `suggest()` choices have always had.
Availability (`@requires`) is the one thing never trusted from the cache: it
re-checks live at the moment of execution.
