"""Render the invoking project's task tree as markdown — `fm footman docs …`.

`page` prints (or writes) one document; `site` writes linked pages with an
`index.md` per group. Both rebuild the project's tree exactly the way `fm`
itself does — the cascade, the config, the mounted plugins — so the output
can't drift from what `fm --list` shows.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

from footman import _paths, config, context, discover, markdown, registry
from footman import manifest as _manifest
from footman.params import between, doc
from footman.registry import Group

tasks = Group("docs", help="Generate markdown docs for this project's tasks")


def _project_tree(include_self: bool) -> dict:
    """The invoking project's manifest tree, rebuilt the way `fm` builds it.

    Plugin tasks run from the invocation directory (the composing contract),
    so `Path.cwd()` is the right anchor for the cascade walk. Re-importing
    the tasks files inside a running task is the same same-process repeat
    `Runner` performs — `discover` isolates each file per import.
    """
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = config.load_config(
        cwd, ceiling, None, on_warning=lambda m: print(m, file=sys.stderr)
    )
    name = cfg.get("tasks")
    filename = name if isinstance(name, str) else _paths.DEFAULT_TASKS_FILE
    files = _paths.task_files(cwd, ceiling, filename)
    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list) and plugins:
        from footman import compose

        compose.mount_plugins(base, plugins)
    reg = discover.load_tree(files, base=base)
    tree = _manifest.build_manifest(reg)["tree"]
    if not include_self:
        # Don't document the documenter: the mounted `footman` group is
        # opted back in with --all.
        tree["groups"].pop("footman", None)
    return tree


def _path_of(target: str) -> tuple[str, ...]:
    return tuple(target.replace(".", " ").split())


@tasks.task
def page(
    target: Annotated[str, doc("dotted task/group to scope to; empty = all")] = "",
    heading: Annotated[int, between(1, 6), doc("top heading level")] = 1,
    flavor: Annotated[
        Literal["plain", "material"],
        doc("plain CommonMark, or material/zensical extras"),
    ] = "plain",
    out: Path | None = None,
    prog: Annotated[
        str, doc("command name in usage and examples (default: the invoking CLI)")
    ] = "",
    all: Annotated[bool, doc("include footman's own mounted tasks")] = False,
):
    """Render the task tree (or one group/task) as one markdown page.

    Without --out the page is the task's stdout, ready to redirect or pipe
    (into pandoc, say); with --out it is written to the file. The heading
    level makes the page nest under a host site's own structure, so it
    drops into zensical/mkdocs via a snippet include.
    """
    tree = _project_tree(all)
    prog = prog or context.current().prog  # a branded CLI documents itself
    text = markdown.render_page(
        tree, path=_path_of(target), heading=heading, flavor=flavor, prog=prog
    )
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    # Inside a task, stderr merges into task output by contract — a plain
    # print is the honest note here; `returned` carries the machine copy.
    print(f"wrote {out}")
    return [str(out)]


@tasks.task
def site(
    out: Annotated[Path, doc("directory to write the pages into")],
    target: Annotated[str, doc("dotted group to scope to; empty = all")] = "",
    flavor: Annotated[
        Literal["plain", "material"],
        doc("material fits zensical/mkdocs; plain is portable"),
    ] = "material",
    prog: Annotated[
        str, doc("command name in usage and examples (default: the invoking CLI)")
    ] = "",
    all: Annotated[bool, doc("include footman's own mounted tasks")] = False,
):
    """Render the task tree as linked pages: index.md per group, one file per task.

    Made for docs sites — point <out> into your docs tree and add the pages
    to the nav. Regenerate on each docs build so they can't drift.
    """
    tree = _project_tree(all)
    prog = prog or context.current().prog  # a branded CLI documents itself
    files = markdown.render_site(tree, path=_path_of(target), flavor=flavor, prog=prog)
    written: list[str] = []
    for rel, content in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(str(dest))
    print(f"wrote {len(written)} pages under {out}")
    return written
