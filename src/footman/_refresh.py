"""Background completion-manifest refresh — the detached rebuild child.

The completion hot path spawns this detached, two ways:

- `_maybe_refresh` → `refresh_cwd` is the stale-while-revalidate path: when a
  cwd's cached manifest is older than its baked `completion.max_age`, rebuild the
  *cwd cascade's* manifest so dynamic completers (git branches, file lists) don't
  go stale for "time since your last real `fm` command here".
- `_cold_build` → `refresh_source` builds a single `-f <file>`'s (cwd, file)
  manifest the first time it is TAB-completed in a fresh directory, so `fm -f
  <file> <TAB>` answers accurately instead of empty.

Both rebuild exactly as a real run would and are strictly fire-and-forget: they
print nothing and never raise.

A tasks file that carries its own PEP 723 dependencies is rebuilt from inside
its script environment — but only when that environment already exists, since
a keystroke must never reach for the network. Before the first real run there
is nothing to complete from, which is the honest answer.
"""

from __future__ import annotations

import contextlib


def _maybe_reexec(files: list, entry: str, *args: str) -> None:
    """Continue this rebuild inside a script file's own environment.

    Only for a single file that declares dependencies, and only when its
    environment already exists (`_script.child_python` never builds one) —
    otherwise this returns and the child rebuilds in place, exactly as it
    always did. The re-executed child runs the same one-liner *entry*, so
    the two spawn shapes stay identical apart from the interpreter.
    """
    if len(files) != 1:
        return  # a cascade has no single environment to be right about
    from footman import _script

    python = _script.child_python(files[0])
    if python is not None:
        _script.reexec_child(python, ["-c", entry, *args])


def refresh_cwd() -> None:
    """Rebuild the current directory's completion manifest, swallowing errors."""
    # A detached background refresh must never crash or print.
    with contextlib.suppress(Exception):
        _rebuild()


def _rebuild() -> None:
    from pathlib import Path

    from footman import _paths, config, discover, manifest, registry

    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = config.load_config(cwd, ceiling)
    filename = cfg.get("tasks")
    if not isinstance(filename, str):
        # A branded CLI's default filename isn't knowable here — but the
        # manifest this child refreshes baked it in.
        cached = manifest.load_manifest(_paths.manifest_path(cwd))
        baked = cached.get("tasks_file") if isinstance(cached, dict) else None
        filename = baked if isinstance(baked, str) else _paths.DEFAULT_TASKS_FILE
    name = filename
    files = _paths.task_files(cwd, ceiling, name)
    if not files:
        return
    _maybe_reexec(files, "from footman import _refresh; _refresh.refresh_cwd()")

    # Mirror the app layer's cwd cascade build; plugin pulls are authored in
    # the tasks files themselves, so discovery alone rebuilds the whole tree.
    base = registry.Group("root")
    reg = discover.load_tree(files, base=base)
    manifest.sync_manifest(
        reg, cwd, completion_max_age=config.completion_max_age(cfg), tasks_file=name
    )


def refresh_source(tasks_file: str) -> None:
    """Rebuild one `-f <file>`'s (cwd, file) manifest, swallowing errors."""
    # A detached background rebuild must never crash or print.
    with contextlib.suppress(Exception):
        _rebuild_source(tasks_file)


def _rebuild_source(tasks_file: str) -> None:
    from pathlib import Path

    from footman import _paths, discover, manifest, registry

    one = Path(tasks_file).expanduser()
    if not one.is_file():
        return  # a typed-but-missing -f value: nothing to build
    cwd = Path.cwd()
    _maybe_reexec(
        [one],
        "import sys; from footman import _refresh; "
        "_refresh.refresh_source(sys.argv[1])",
        tasks_file,  # the entry reads it back off argv
    )

    # Mirror a real `-f` run (see _app._run): one file, no cascade, cached
    # under the (cwd, file) key with max_age=0 — no background refresh,
    # rebuilt on the next -f run or the next cold TAB.
    base = registry.Group("root")
    reg = discover.load_tree([one], base=base)
    manifest.sync_manifest(
        reg,
        cwd,
        completion_max_age=0,
        tasks_file=tasks_file,
        path=_paths.source_manifest_path(cwd, one),
    )
