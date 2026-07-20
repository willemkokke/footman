"""footman's own first-party tasks — mount with `plugins = ["footman"]`.

One module per task family: the aggregate group mounts each family as a
subgroup, so everything lives under `fm footman …` once configured
(`fm footman docs page`, `fm footman docs site …`; future families slot in
as siblings). Deliberate symmetry: the `footman.tasks` entry-point *group*
is served by the `footman.tasks` *package* — different namespaces, same
name, one product.

The package is imported only when the plugin is mounted — never by a bare
`import footman`, and never on the completion hot path.
"""

from __future__ import annotations

from footman.registry import Group
from footman.tasks import docs as _docs
from footman.tasks import tools as _tools

tasks = Group("footman", help="footman's own tasks")
tasks.groups["docs"] = _docs.tasks
tasks.groups["tools"] = _tools.tasks

__all__ = ["tasks"]
