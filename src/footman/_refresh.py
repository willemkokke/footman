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
"""

from __future__ import annotations

import contextlib


def refresh_cwd() -> None:
    """Rebuild the current directory's completion manifest, swallowing errors."""
    # A detached background refresh must never crash or print.
    with contextlib.suppress(Exception):
        _rebuild()


def _rebuild() -> None:
    from pathlib import Path

    from footman import _paths, compose, config, discover, manifest, registry

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

    # Mirror the app layer's cwd cascade build (discovery + config plugins), so
    # the refreshed manifest matches what a real `fm` run would cache.
    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list) and plugins:
        # A broken plugin shouldn't abort the whole refresh.
        with contextlib.suppress(registry.RegistrationError):
            compose.mount_plugins(base, plugins)

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

    from footman import _paths, compose, config, discover, manifest, registry

    one = Path(tasks_file).expanduser()
    if not one.is_file():
        return  # a typed-but-missing -f value: nothing to build
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = config.load_config(cwd, ceiling)

    # Mirror a real `-f` run (see _app._run): one file, no cascade, config
    # plugins mounted, cached under the (cwd, file) key with max_age=0 — no
    # background refresh, rebuilt on the next -f run or the next cold TAB.
    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list) and plugins:
        with contextlib.suppress(registry.RegistrationError):
            compose.mount_plugins(base, plugins)

    reg = discover.load_tree([one], base=base)
    manifest.sync_manifest(
        reg,
        cwd,
        completion_max_age=0,
        tasks_file=tasks_file,
        path=_paths.source_manifest_path(cwd, one),
    )
