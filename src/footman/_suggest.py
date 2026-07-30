"""Fresh values for a dynamic completer — spawned by the completion hot path.

A dynamic completer (`suggest(fn)`) queries live state: git branches, release
candidates, deploy targets. Serving the manifest's *baked* snapshot for a
build-critical answer is as wrong as answering from an empty cache, so when TAB
lands on a dynamic parameter `_complete` spawns this process to run that one
completer fresh.

It lives out of the hot path precisely because it imports the framework and the
user's code — the thing a TAB press must never do. Isolation is the point: a
slow or crashing completer dies here, bounded by the caller's timeout, and the
hot path degrades to no candidates rather than a hung keystroke.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import sys
from typing import Any


def _maybe_reexec(files: list) -> None:
    """Continue in a script file's own environment, when it already exists.

    A completer on a tasks file that carries its own dependencies needs
    that file's world to import at all. Same rule as the refresh child:
    never build one here (a keystroke must not reach for the network), and
    when there is nothing to re-exec into, carry on in place.
    """
    if len(files) != 1:
        return
    from footman import _script

    python = _script.child_python(files[0])
    if python is not None:
        _script.reexec_child(python, ["-m", "footman._suggest", *sys.argv[1:]])


def _fresh(completer: Any) -> list[str]:
    """Run *completer* with its own stdout/stderr muted, so its chatter can't
    leak into the value channel the hot path reads."""
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        return [str(v) for v in completer.fn()]


def _values(param: str, path: list[str], g: dict[str, object]) -> list[str]:
    """The fresh output of *param*'s completer on the task at *path*.

    Rediscovers the same tasks the manifest was built from (honouring
    `-f`/`--config`), walks to the task, peels the parameter, and runs its
    completer. Any miss — no tasks file, a renamed task, a plain parameter —
    is an empty list, never an error.
    """
    from footman import _app, _coerce, _discover, _manifest, registry

    files, _cfg = _app.resolve_task_files(g, on_warning=lambda *a: None, on_note=None)
    if not files or not path:
        return []
    _maybe_reexec(files)  # before any user code is imported
    # Plugin pulls are authored in the tasks files, so discovery alone
    # rebuilds the whole tree — a completer on a pulled task included.
    base = registry.Group("root")
    root = _discover.load_tree(files, base=base)

    node: registry.Group | None = root
    for name in path[:-1]:  # descend the groups
        node = node.groups.get(name) if node else None
    task = node.tasks.get(path[-1]) if node else None
    if task is None:
        return []
    for p in _manifest.resolved_signature(task).parameters.values():
        if (
            registry.cli_name(p.name) != param
            or p.annotation is inspect.Parameter.empty
        ):
            continue
        completer = _coerce.peel(p.annotation).completer
        if completer is None:
            return []
        return _fresh(completer)
    return []


def _global_values(name: str, g: dict[str, object]) -> list[str]:
    """The fresh output of the completer on the plugin global *name*.

    The globals ride the tree's contributions, so the same discovery that
    finds a task's completer finds an option's: rediscover, match the cli
    name, peel the annotation, run what it carries. Any miss — no tasks
    file, an unpulled owner, a plain option — is an empty list."""
    from footman import _app, _coerce, _discover, registry

    files, _cfg = _app.resolve_task_files(g, on_warning=lambda *a: None, on_note=None)
    if not files:
        return []
    _maybe_reexec(files)  # before any user code is imported
    base = registry.Group("root")
    root = _discover.load_tree(files, base=base)
    for opt in root.contributions.get("globals", ()):
        if opt.name != name:
            continue
        completer = _coerce.peel(opt.annotation).completer
        if completer is None:
            return []
        return _fresh(completer)
    return []


def main(argv: list[str]) -> int:
    param: str | None = None
    globopt: str | None = None
    path: list[str] = []
    g: dict[str, object] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--param" and i + 1 < len(argv):
            param, i = argv[i + 1], i + 2
        elif arg == "--global" and i + 1 < len(argv):
            globopt, i = argv[i + 1], i + 2
        elif arg == "--path" and i + 1 < len(argv):
            path.append(argv[i + 1])
            i += 2
        elif arg == "--tasks-file" and i + 1 < len(argv):
            g["tasks_file"], i = argv[i + 1], i + 2
        elif arg == "--config" and i + 1 < len(argv):
            g["config"], i = argv[i + 1], i + 2
        else:
            i += 1
    try:
        if globopt is not None:
            values = _global_values(globopt, g)
        elif param is not None:
            values = _values(param, path, g)
        else:
            return 0
    except Exception:
        return 0  # any failure → no candidates; the hot path falls back to empty
    if values:
        sys.stdout.write("\n".join(values) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
