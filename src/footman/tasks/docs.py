"""Render the invoking project's task tree as markdown — `fm footman docs …`.

`page` prints (or writes) one document; `site` writes linked pages with an
`index.md` per group. Both rebuild the project's tree exactly the way `fm`
itself does — the cascade, the config, the mounted plugins — so the output
can't drift from what `fm --list` shows.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
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


_CLEAR = "\x1b[K"


def reduce_frames(raw: str) -> str:
    """Collapse a pty capture to the final frame of every line.

    footman's live output repaints a line in place — `\\r` then a full
    rewrite (step lines, the status bar) — and clears with `ESC[K`; a pty
    records every intermediate frame. Keeping only the text after the last
    `\\r` of each physical line, and dropping the clear sequences, leaves
    what a human saw once the run settled. Colour (SGR) sequences pass
    through untouched.
    """
    text = raw.replace("\r\n", "\n")  # the pty's ONLCR translation, undone
    lines = [seg.rsplit("\r", 1)[-1].replace(_CLEAR, "") for seg in text.split("\n")]
    return "\n".join(lines)


@tasks.task(name="shots", requires="rich", when=lambda: sys.platform != "win32")
def shots(
    *argv: str,
    out: Annotated[Path, doc("the SVG file to write")],
    title: Annotated[str, doc("window title (default: the command line)")] = "",
    width: Annotated[int, between(40, 200), doc("terminal columns")] = 72,
    cmd: Annotated[str, doc("executable to run (default: the invoking CLI)")] = "",
):
    """Run the CLI on a pseudo-terminal and save a framed SVG screenshot.

    Runs `<cmd> <argv…>` on a real pty — colours, receipts, taught errors,
    exactly as a terminal shows them — collapses live rewrites to their
    final frame, and renders the capture with rich as an SVG in a
    macOS-style window. Regenerate on every docs build and a screenshot
    can never drift from the CLI: it *is* the CLI.

    The command really executes, so don't screenshot tasks whose side
    effects you don't want. A failing command still renders — a taught
    error message is a perfectly good screenshot.
    """
    if sys.platform == "win32":  # the when= gate already refused; belt
        raise RuntimeError("docs shots needs a POSIX pseudo-terminal")
    import fcntl
    import pty
    import struct
    import termios

    prog = cmd or context.current().prog
    exe = shutil.which(prog)
    if exe is None:
        raise RuntimeError(f"{prog!r} is not on PATH")

    env = os.environ.copy()
    env.pop("NO_COLOR", None)  # the pty asks for colour; let it answer
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(width)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, width, 0, 0))
    proc = subprocess.Popen(
        [exe, *argv], stdin=slave, stdout=slave, stderr=slave, env=env
    )
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(master, 65536)
        except OSError:  # EIO: the child hung up (how Linux spells EOF)
            break
        if not data:
            break
        chunks.append(data)
    os.close(master)
    proc.wait()

    # The blessed lazy import: rich is docs tooling, never a dependency —
    # requires="rich" lists this task as unavailable when it's absent.
    from rich.console import Console
    from rich.text import Text

    capture = reduce_frames(b"".join(chunks).decode("utf-8", "replace"))
    console = Console(record=True, width=width, file=io.StringIO(), force_terminal=True)
    console.print(Text.from_ansi(capture.rstrip("\n")))
    out.parent.mkdir(parents=True, exist_ok=True)
    line = " ".join([prog, *argv])
    out.write_text(console.export_svg(title=title or line), encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]


@tasks.task(name="globals")
def globals_(
    out: Path | None = None,
    prog: Annotated[
        str, doc("command name in the table (default: the invoking CLI)")
    ] = "",
):
    """Render the runner's global options as a markdown table.

    The rows come straight from the CLI grammar — the same table `--help`
    prints — so a reference page that regenerates this on each docs build
    can never drift from the runner. Without --out the table is the task's
    stdout; with --out it is written to the file.
    """
    prog = prog or context.current().prog  # a branded CLI documents itself
    text = markdown.globals_table(prog=prog)
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]
