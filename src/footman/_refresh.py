"""Background completion-manifest refresh — the stale-while-revalidate child.

The completion hot path (`_complete._maybe_refresh`) spawns this detached when a
cwd's cached manifest is older than its baked `completion.max_age`, so dynamic
completers (git branches, file lists) don't go stale for "time since your last
real `fm` command here". It rebuilds the *cwd cascade's* manifest exactly as a
real run would (never an `-f` tree) and is strictly fire-and-forget: it prints
nothing and never raises.
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
    name = filename if isinstance(filename, str) else _paths.DEFAULT_TASKS_FILE
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
    manifest.sync_manifest(reg, cwd, completion_max_age=config.completion_max_age(cfg))
