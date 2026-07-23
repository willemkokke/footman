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


def _values(param: str, path: list[str], g: dict[str, object]) -> list[str]:
    """The fresh output of *param*'s completer on the task at *path*.

    Rediscovers the same tasks the manifest was built from (honouring
    `-f`/`--config`), walks to the task, peels the parameter, and runs its
    completer. Any miss — no tasks file, a renamed task, a plain parameter —
    is an empty list, never an error.
    """
    from footman import _app, coerce, compose, discover, manifest, registry

    files, cfg = _app.resolve_task_files(g, on_warning=lambda *a: None, on_note=None)
    if not files or not path:
        return []
    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list):  # a completer may live on a plugin task
        compose.mount_plugins(base, plugins)
    root = discover.load_tree(files, base=base)

    node: registry.Group | None = root
    for name in path[:-1]:  # descend the groups
        node = node.groups.get(name) if node else None
    task = node.tasks.get(path[-1]) if node else None
    if task is None:
        return []
    for p in manifest.resolved_signature(task).parameters.values():
        if (
            registry.cli_name(p.name) != param
            or p.annotation is inspect.Parameter.empty
        ):
            continue
        completer = coerce.peel(p.annotation).completer
        if completer is None:
            return []
        # Mute the completer's own stdout/stderr so its chatter can't leak into
        # the value channel the hot path reads.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return [str(v) for v in completer.fn()]
    return []


def main(argv: list[str]) -> int:
    param: str | None = None
    path: list[str] = []
    g: dict[str, object] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--param" and i + 1 < len(argv):
            param, i = argv[i + 1], i + 2
        elif arg == "--path" and i + 1 < len(argv):
            path.append(argv[i + 1])
            i += 2
        elif arg == "--tasks-file" and i + 1 < len(argv):
            g["tasks_file"], i = argv[i + 1], i + 2
        elif arg == "--config" and i + 1 < len(argv):
            g["config"], i = argv[i + 1], i + 2
        else:
            i += 1
    if param is None:
        return 0
    try:
        values = _values(param, path, g)
    except Exception:
        return 0  # any failure → no candidates; the hot path falls back to empty
    if values:
        sys.stdout.write("\n".join(values) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
