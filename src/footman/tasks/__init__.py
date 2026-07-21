"""footman's own first-party task plugins — one module per family.

Each family is its own `footman.tasks` entry point, and a plugin's name is
its command path (see `compose.mount_plugins`):

* `footman.docs`  → `footman.tasks.docs:tasks` — task-documentation
  generation (`fm footman docs …`). The end-user-facing family.
* `footman.tools` → `footman.tasks.tools:tasks` — the `tools.*` stub
  toolkit and its provisioning (`fm footman tools …`). Maintainer-facing,
  rarely what a user wants.

They mount independently: `plugins = ["footman.docs"]` takes just the docs
family, and both share the `footman` namespace group without either owning
it. Nothing here is imported by a bare `import footman`, or on the
completion hot path — a family imports only when its plugin is mounted, so
this package imports neither submodule at package-init time.
"""

from __future__ import annotations
