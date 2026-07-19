"""Render the invoking project's task tree as markdown — `fm footman docs …`.

`page` prints (or writes) one document; `site` writes linked pages with an
`index.md` per group. Both rebuild the project's tree exactly the way `fm`
itself does — the cascade, the config, the mounted plugins — so the output
can't drift from what `fm --list` shows.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

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


# --- animated casts -----------------------------------------------------------
# An interactive session (TAB completion!) can't be a static screenshot or a
# line-based reduction: shells paint menus with real cursor movement. A cast
# drives a live shell on the pty with scripted keystrokes, replays the byte
# stream through a terminal emulator (pyte) into screen states, renders each
# state with rich, and stacks the frames in one self-contained SVG animated
# by CSS keyframes with the capture's own timing. No JavaScript; an <img>
# plays it.

_KEY_TOKENS = {
    "<TAB>": b"\t",
    "<ENTER>": b"\r",
    "<SPACE>": b" ",
    "<BACKSPACE>": b"\x7f",
    "<CTRL-C>": b"\x03",
}


def keystrokes(script: tuple[str, ...]) -> list[tuple[float, bytes]]:
    """Compile a cast script into (delay-before-send, bytes) steps.

    Each script argument is either literal text — typed one character at a
    time at a human-ish cadence — or a token: `<TAB>`, `<ENTER>`,
    `<SPACE>`, `<BACKSPACE>`, `<CTRL-C>`, `<WAIT>` (pause 0.8 s), or
    `<WAIT:ms>`.
    """
    sends: list[tuple[float, bytes]] = []
    for part in script:
        if part in _KEY_TOKENS:
            sends.append((0.3, _KEY_TOKENS[part]))
        elif part == "<WAIT>":
            sends.append((0.8, b""))
        elif part.startswith("<WAIT:") and part.endswith(">"):
            sends.append((int(part[6:-1]) / 1000.0, b""))
        else:
            sends.extend((0.07, ch.encode("utf-8")) for ch in part)
    return sends


def _pty_session(
    argv: list[str],
    *,
    width: int,
    height: int,
    sends: list[tuple[float, bytes]],
    settle: float,
    env_extra: dict[str, str] | None = None,
) -> list[tuple[float, bytes]]:
    """Run *argv* on a pty, play the keystroke script, and record
    (elapsed-seconds, bytes) chunks until output has settled."""
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time as _time

    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(width)
    env["LINES"] = str(height)
    env.update(env_extra or {})
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True
    )
    os.close(slave)
    start = _time.monotonic()
    chunks: list[tuple[float, bytes]] = []
    queue = list(sends)
    # Typing begins only after the boot has *settled*: keys written before
    # the line editor exists are half-echoed raw and eaten (a TAB pressed
    # then never completes anything), and a slow rc — compinit, the hook's
    # own `--setup-completion` subprocess — paints in bursts. Every boot
    # chunk pushes the start out another half second of silence.
    typing_started = False
    next_at: float | None = None
    deadline = None if queue else start + settle
    try:
        while True:
            now = _time.monotonic()
            if queue and next_at is not None and now >= next_at:
                typing_started = True
                _, data = queue.pop(0)
                if data:
                    os.write(master, data)
                next_at = now + (queue[0][0] if queue else 0.0)
                if not queue:
                    deadline = now + settle
            readable, _, _ = select.select([master], [], [], 0.03)
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO: the child hung up
                    break
                if not data:
                    break
                chunks.append((_time.monotonic() - start, data))
                if not typing_started and queue:
                    next_at = _time.monotonic() + 0.5 + queue[0][0]
                if deadline is not None:  # let late repaints settle too
                    deadline = _time.monotonic() + settle
            if deadline is not None and _time.monotonic() >= deadline:
                break
            if proc.poll() is not None and not readable:
                break
    finally:
        import contextlib as _contextlib

        with _contextlib.suppress(ProcessLookupError):
            proc.kill()
        os.close(master)
        proc.wait()
    return chunks


def _cell_style(char: Any) -> str:
    """A pyte cell's attributes as a rich style string ('' = default)."""
    bits: list[str] = []
    if char.bold:
        bits.append("bold")
    if char.italics:
        bits.append("italic")
    if char.underscore:
        bits.append("underline")
    if char.reverse:
        bits.append("reverse")
    for attr, prefix in ((char.fg, ""), (char.bg, "on ")):
        if attr and attr != "default":
            # pyte names ansi colours ("red") and spells the rest as bare
            # 6-digit hex ("87d7ff") — rich wants a `#` on the hex form.
            longhand = f"#{attr}" if _HEX6.fullmatch(attr) else attr
            bits.append(f"{prefix}{longhand}")
    return " ".join(bits)


def _screens(
    chunks: list[tuple[float, bytes]], *, width: int, height: int
) -> list[tuple[float, list[list[tuple[str, str]]]]]:
    """Replay timed chunks through a terminal emulator; return the deduped
    (time, screen) states, each screen a grid of (char, style) cells."""
    import pyte

    screen = pyte.Screen(width, height)
    stream = pyte.Stream(screen)
    frames: list[tuple[float, list[list[tuple[str, str]]]]] = []
    last: list[list[tuple[str, str]]] | None = None
    for t, data in chunks:
        stream.feed(data.decode("utf-8", "replace"))
        snap = [
            [
                (cell.data, _cell_style(cell))
                for x in range(width)
                for cell in (screen.buffer[y][x],)
            ]
            for y in range(height)
        ]
        if snap != last:
            frames.append((t, snap))
            last = snap
    return frames


_HEX6 = re.compile(r"[0-9a-fA-F]{6}")
_SVG_SHELL = re.compile(r"^\s*<svg[^>]*>|</svg>\s*$")


def compose_animation(svgs: list[str], times: list[float], *, hold: float = 1.6) -> str:
    """Stack per-frame SVGs into one, cycled by CSS keyframes.

    Each frame plays over its captured window; the last holds for *hold*
    seconds before the loop restarts. `step-end` opacity keeps the switch
    discrete, and every frame carries its own opaque background, so the
    topmost visible frame is the whole picture.
    """
    total = times[-1] + hold
    head = svgs[0]
    match = re.search(r"<svg[^>]*>", head)
    shell_open = match.group(0) if match else "<svg>"
    css: list[str] = [".cast-frame{opacity:0}"]
    body: list[str] = []
    for i, (svg, t) in enumerate(zip(svgs, times, strict=True)):
        a = 100.0 * t / total
        b = 100.0 * (times[i + 1] / total) if i + 1 < len(times) else 100.0
        window = f"{a:.3f}%{{opacity:1}}" if a > 0 else "0%{opacity:1}"
        off = f"{b:.3f}%{{opacity:0}}" if b < 100.0 else ""
        pre = "0%{opacity:0}" if a > 0 else ""
        css.append(f"@keyframes cf{i}{{{pre}{window}{off}}}")
        css.append(f".cf{i}{{animation:cf{i} {total:.3f}s step-end infinite}}")
        inner = _SVG_SHELL.sub("", svg.strip())
        body.append(f'<g class="cast-frame cf{i}">{inner}</g>')
    style = f"<style>{''.join(css)}</style>"
    return f"{shell_open}{style}{''.join(body)}</svg>"


_CAST_BOOT: dict[str, str] = {"zsh": "zsh", "bash": "bash", "fish": "fish"}


def _boot_shell(
    shell: str, prog: str, scratch: Path
) -> tuple[list[str], dict[str, str]]:
    """(argv, extra env) for an interactive *shell* with completion loaded.

    Each shell boots from a scratch config dir — the user's own dotfiles
    never run — with a minimal green prompt and footman's hook installed
    via the same `--setup-completion` path users eval. The scratch HOME
    would also hide the completion cache (TAB answers from cache alone),
    so the invoker's real cache dir is passed through FOOTMAN_CACHE_DIR —
    the override doing exactly the job it was built for.
    """
    env = {
        "HOME": str(scratch),
        "XDG_CONFIG_HOME": str(scratch),
        "FOOTMAN_CACHE_DIR": str(_paths.footman_cache_dir()),
    }
    # A system rc (/etc/zsh/*, /etc/profile) may rebuild PATH and lose the
    # venv that owns *prog* \u2014 then the rc's `eval "$(prog \u2026)"` silently
    # produces nothing and the hook never loads. Pin the interpreter's own
    # bin dir first, the same lesson the functional shell tests carry.
    bin_dir = str(Path(sys.executable).parent)
    if shell == "zsh":
        (scratch / ".zshrc").write_text(
            f"path=({bin_dir!r} $path)\n"
            "PROMPT='%F{green}\u276f%f '\n"
            "autoload -Uz compinit && compinit -u\n"
            f'eval "$({prog} --setup-completion zsh)"\n',
            encoding="utf-8",
        )
        env["ZDOTDIR"] = str(scratch)
        return ["zsh", "-i"], env
    if shell == "bash":
        rc = scratch / "bashrc"
        rc.write_text(
            f'PATH="{bin_dir}:$PATH"\n'
            "PS1='\\[\\e[32m\\]\u276f\\[\\e[0m\\] '\n"
            f'eval "$({prog} --setup-completion bash)"\n',
            encoding="utf-8",
        )
        return ["bash", "--rcfile", str(rc), "-i"], env
    if shell == "fish":
        boot = (
            f"fish_add_path --prepend {bin_dir!r}; "
            f"{prog} --setup-completion fish | source; "
            "function fish_prompt; set_color green; echo -n '\u276f '; "
            "set_color normal; end"
        )
        return ["fish", "-i", "-C", boot], env
    raise RuntimeError(f"cast drives zsh, bash, or fish (got {shell!r})")


@tasks.task(
    name="cast", requires=["rich", "pyte"], when=lambda: sys.platform != "win32"
)
def cast(
    *keys: str,
    out: Annotated[Path, doc("the animated SVG file to write")],
    shell: Annotated[
        Literal["zsh", "bash", "fish"], doc("interactive shell to drive")
    ] = "zsh",
    title: Annotated[str, doc("window title (default: '<shell> · completion')")] = "",
    width: Annotated[int, between(40, 200), doc("terminal columns")] = 72,
    height: Annotated[int, between(4, 50), doc("terminal rows")] = 14,
    prog: Annotated[
        str, doc("CLI whose completion is installed (default: the invoking CLI)")
    ] = "",
    max_frames: Annotated[int, between(2, 120), doc("frame budget")] = 60,
):
    """Record an animated SVG of a real interactive shell session.

    Boots the shell from a scratch config with footman completion loaded
    (via `--setup-completion`), types the script — everything after `--`,
    where `<TAB>`, `<ENTER>`, `<WAIT>` and friends are keys — and replays
    the capture through a terminal emulator into an animated, dependency-
    free SVG with the session's real timing. TAB completion, in motion,
    regenerated on every docs build so it cannot drift.
    """
    if sys.platform == "win32":  # the when= gate already refused; belt
        raise RuntimeError("docs cast needs a POSIX pseudo-terminal")
    import tempfile

    prog = prog or context.current().prog
    if shutil.which(prog) is None:
        raise RuntimeError(f"{prog!r} is not on PATH")
    if shutil.which(_CAST_BOOT.get(shell, shell)) is None:
        raise RuntimeError(f"{shell!r} is not on PATH")

    with tempfile.TemporaryDirectory() as scratch:
        argv, env_extra = _boot_shell(shell, prog, Path(scratch))
        chunks = _pty_session(
            argv,
            width=width,
            height=height,
            sends=keystrokes(keys),
            settle=1.5,
            env_extra=env_extra,
        )
    frames = _screens(chunks, width=width, height=height)
    if not frames:
        raise RuntimeError(f"the {shell} session produced no output")
    if len(frames) > max_frames:  # keep first/last, thin the middle evenly
        keep = {0, len(frames) - 1}
        keep.update(
            round(i * (len(frames) - 1) / (max_frames - 1)) for i in range(max_frames)
        )
        frames = [f for i, f in enumerate(frames) if i in keep]

    from rich.console import Console
    from rich.text import Text

    label = title or f"{shell} · completion"
    svgs: list[str] = []
    for i, (_, grid) in enumerate(frames):
        console = Console(
            record=True, width=width, file=io.StringIO(), force_terminal=True
        )
        for row in grid:
            text = Text()
            for ch, style in row:
                text.append(ch, style or None)
            console.print(text)
        svgs.append(console.export_svg(title=label, unique_id=f"cf{i}"))
    start = frames[0][0]
    times = [t - start for t, _ in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compose_animation(svgs, times), encoding="utf-8")
    print(f"wrote {out} ({len(frames)} frames)")
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
