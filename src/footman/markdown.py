"""Render a task tree as markdown: one page, or a linked site of pages.

Pure functions over manifest tree nodes — the same dicts `--json --list`
emits — phrased through `footman._describe`, the module the `--help`
renderer uses, so pages and help can never drift.

Two flavors:

- `plain` (the default) — CommonMark and pipe tables only. Safe verbatim
  through `pymdownx.snippets` includes and pandoc → PDF/HTML.
- `material` — opts into the extensions a zensical / mkdocs-material site
  already enables: attr_list anchors on headings for stable deep links, and
  an `!!! example` admonition for the synthesized invocation.

`render_page` returns one document (headings start at *heading*, so a page
can nest under a host site's own structure). `render_site` returns
`{relative_path: content}` — one file per task, an `index.md` per group with
relative links — ready to write into a docs tree.
"""

from __future__ import annotations

import json
from typing import Any

from footman import _describe

__all__ = ["render_page", "render_site"]


def render_page(
    tree: dict[str, Any],
    *,
    path: tuple[str, ...] = (),
    heading: int = 1,
    flavor: str = "plain",
    prog: str = "fm",
) -> str:
    """One markdown document for the node at *path* (empty = whole tree)."""
    kind, node = _resolve(tree, path)
    if kind == "task":
        parts = _task_page(list(path), node, heading, flavor, prog)
    else:
        parts = _group_page(list(path), node, heading, flavor, prog)
    return "\n".join(parts).rstrip() + "\n"


def render_site(
    tree: dict[str, Any],
    *,
    path: tuple[str, ...] = (),
    flavor: str = "plain",
    prog: str = "fm",
) -> dict[str, str]:
    """A linked set of files for the node at *path*: `index.md` per group
    (name, help, a table of children with relative links), one file per task.
    Keys are POSIX-relative paths."""
    kind, node = _resolve(tree, path)
    if kind == "task":
        name = path[-1]
        page = render_page(tree, path=path, heading=1, flavor=flavor, prog=prog)
        return {f"{name}.md": page}
    files: dict[str, str] = {}
    _site_group(list(path), node, "", files, flavor, prog)
    return files


# --- resolution ---------------------------------------------------------------


def _resolve(tree: dict, path: tuple[str, ...]) -> tuple[str, dict]:
    """Walk *path* to its node; ("task"|"group", node). Taught ValueError."""
    node = tree
    for i, name in enumerate(path):
        if name in node["groups"]:
            node = node["groups"][name]
        elif i == len(path) - 1 and name in node["tasks"]:
            return "task", node["tasks"][name]
        else:
            known = list(node["groups"]) + list(node["tasks"])
            where = ".".join(path[:i]) or "the root"
            raise ValueError(
                f"no task or group named {name!r} under {where} "
                f"(know: {', '.join(known) or 'nothing'})"
            )
    return "group", node


# --- one task -----------------------------------------------------------------


def _slug(path: list[str]) -> str:
    return "-".join(path)


def _cell(text: str) -> str:
    """Make *text* safe inside a pipe-table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _h(level: int, text: str, path: list[str], flavor: str) -> str:
    anchor = f" {{ #{_slug(path)} }}" if flavor == "material" and path else ""
    return f"{'#' * min(level, 6)} {text}{anchor}"


def _type_cell(p: dict) -> str:
    if p["kind"] == "flag":
        return "flag"
    if p.get("mapping"):
        return "KEY=VALUE"
    bits: list[str] = []
    choices = p.get("choices")
    if choices:
        bits.append(" \\| ".join(f"`{c}`" for c in choices))
    elif p.get("types"):
        bits.append(" \\| ".join(p["types"]))
    if p.get("multiple") or p.get("mapping"):
        bits.append("repeatable")
    if p["kind"] == "variadic":
        bits.append("variadic")
    return ", ".join(bits)


def _default_cell(p: dict) -> str:
    # A positional argument is required by kind (a default would have made it
    # an option — the grammar's load-bearing rule); flags/options say so.
    if p.get("required") or p["kind"] == "argument":
        return "*required*"
    if "default" in p and p["default"] is not None:
        return f"`{_cell(json.dumps(p['default']))}`"
    return ""


def _task_page(
    path: list[str], task: dict, level: int, flavor: str, prog: str
) -> list[str]:
    title = " ".join(path) or prog
    parts = [_h(level, title, path, flavor), ""]
    if task["help"]:
        parts += [task["help"], ""]
    if task.get("long"):
        parts += [task["long"], ""]
    if task.get("disabled"):
        parts += [f"*Unavailable here: {task['disabled']}*", ""]

    fragments = [f for p in task["params"] if (f := _describe.usage_fragment(p))]
    usage = " ".join([prog, *path, *fragments])
    parts += ["```text", usage, "```", ""]

    if task["params"]:
        parts += [
            "| Parameter | Type | Default | Description |",
            "| --- | --- | --- | --- |",
        ]
        for p in task["params"]:
            label = f"`{_describe.param_label(p)}`"
            doc = _cell(p.get("doc", ""))
            parts.append(f"| {label} | {_type_cell(p)} | {_default_cell(p)} | {doc} |")
        parts.append("")

    invocation = _describe.example(path, task, prog)
    if flavor == "material":
        parts += [
            "!!! example",
            "",
            "    ```console",
            f"    $ {invocation}",
            "    ```",
            "",
        ]
    else:
        parts += [f"**Example:** `{invocation}`", ""]
    return parts


# --- one group, page mode -----------------------------------------------------


def _group_page(
    path: list[str], node: dict, level: int, flavor: str, prog: str
) -> list[str]:
    title = " ".join(path) if path else f"{prog} tasks"
    parts = [_h(level, title, path, flavor), ""]
    if node.get("help"):
        parts += [node["help"], ""]
    for name, task in node["tasks"].items():
        parts += _task_page([*path, name], task, level + 1, flavor, prog)
    for name, sub in node["groups"].items():
        parts += _group_page([*path, name], sub, level + 1, flavor, prog)
    return parts


# --- site mode ----------------------------------------------------------------


def _site_group(
    path: list[str],
    node: dict,
    prefix: str,
    files: dict[str, str],
    flavor: str,
    prog: str,
) -> None:
    title = " ".join(path) if path else f"{prog} tasks"
    parts = [_h(1, title, path, flavor), ""]
    if node.get("help"):
        parts += [node["help"], ""]
    rows = [
        (f"[`{name}`]({name}.md)", _describe.task_line(task))
        for name, task in node["tasks"].items()
    ]
    rows += [
        (f"[`{name}`]({name}/index.md)", sub.get("help", ""))
        for name, sub in node["groups"].items()
    ]
    if rows:
        parts += ["| Task | Description |", "| --- | --- |"]
        parts += [f"| {link} | {_cell(text)} |" for link, text in rows]
        parts.append("")
    files[f"{prefix}index.md"] = "\n".join(parts).rstrip() + "\n"

    for name, task in node["tasks"].items():
        page = _task_page([*path, name], task, 1, flavor, prog)
        files[f"{prefix}{name}.md"] = "\n".join(page).rstrip() + "\n"
    for name, sub in node["groups"].items():
        _site_group([*path, name], sub, f"{prefix}{name}/", files, flavor, prog)
