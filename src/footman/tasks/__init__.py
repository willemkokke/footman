"""footman's own first-party task plugins — one module per family.

Each family is its own `footman.tasks` entry point, pulled with `plugin()`
(see `compose.plugin`):

* `footman.docs`  → `footman.tasks.docs:tasks` — task-documentation
  generation (`fm docs …`). The end-user-facing family.
* `footman.tools` → `footman.tasks.tools:tasks` — the `tools.*` stub
  toolkit and its provisioning (`fm tools …`). Maintainer-facing,
  rarely what a user wants.

They pull independently: `plugin("footman.docs")` takes just the docs
family, and where each lands is the puller's call — `into=` mounts it
wherever you want. Nothing here is imported by a bare `import footman`, or on the
completion hot path — a family imports only when its plugin is mounted, so
this package imports neither submodule at package-init time.
"""

from __future__ import annotations
