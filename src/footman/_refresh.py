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
its script environment — but only when uv can reach one without the network,
since a keystroke must never download anything. Otherwise the rebuild happens
here, in place, exactly as it always did: often enough to answer, because a
tasks file's *module-level* imports are usually just the runner.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime imports stay deferred: this child spawns cheap
    from pathlib import Path


def _maybe_reexec(files: list[Path], entry: str, *args: str) -> None:
    """Continue this rebuild inside a script file's own environment — the
    rule lives in `_script.maybe_reexec`, shared with the suggest child.
    The re-executed child runs the same one-liner *entry* under the same
    interpreter flags, so the two spawn shapes stay identical apart from the
    interpreter itself — `-P` included, or the re-exec would reopen the hole
    the spawn closed and let a planted `footman.py` win the import."""
    from footman import _script

    _script.maybe_reexec(files, ["-P", "-c", entry, *args])


def refresh_cwd(*where: str) -> None:
    """Rebuild the current directory's completion manifest, swallowing errors.

    The location words come from the parent (`_paths.child_args`): this child
    inherits the environment but not the brand, so it is *told* where the
    cache is rather than re-deriving it. Taken as `*where` rather than named
    parameters so the two sides cannot disagree about arity — this call is
    inside `suppress`, where a `TypeError` would show up as a manifest that
    silently never appears.
    """
    # A detached background refresh must never crash or print.
    with contextlib.suppress(Exception):
        from footman import _paths

        _paths.configure_child(*where)
        _rebuild()


def _rebuild() -> None:
    from pathlib import Path

    from footman import _config, _discover, _manifest, _paths, registry

    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = _config.load_config(cwd, ceiling)
    filename = cfg.get("tasks")
    if not isinstance(filename, str):
        # The brand's default filename rode in on the spawn (`child_args`
        # hands it over; `configure_child` installed it), so the child asks
        # `_paths` — the peek at the cached manifest's baked `tasks_file`
        # predated that handoff and could serve a stale answer.
        filename = _paths.tasks_file_name()
    name = filename
    project_files = _paths.task_files(cwd, ceiling, name)
    if not project_files:
        # The cascade went empty — the last tasks file here was deleted or
        # renamed. Nothing else rewrites the cwd-keyed manifest (global mode
        # writes the *global* one), so without this the cache outlives its
        # project: TAB keeps offering tasks the runner already refuses by
        # name, and every aged TAB bumps the mtime, so the collector's idle
        # sweep never reaches it either. Removing it puts the directory back
        # where a fresh one starts — the hot path falls through to global
        # mode.
        with contextlib.suppress(OSError):
            _paths.manifest_path(cwd).unlink()
    files = project_files
    user = _paths.user_tasks_file(name)
    if user.is_file():
        # The cascade's outermost rung rides here too: the child must
        # rebuild exactly the tree the run serves, or a background refresh
        # strips personal tasks from the completions it answers.
        files = [user, *files]
    if not files and not _paths.builtin():
        return
    # The re-executed child is a fresh interpreter, so it needs telling where
    # the cache is exactly as the first child did.
    _maybe_reexec(
        files,
        "import sys; from footman import _refresh; _refresh.refresh_cwd(*sys.argv[1:])",
        *_paths.child_args(),
    )

    base = registry.Group("root")
    if not project_files:
        # Global mode: mount the brand's built-ins as the base — exactly as
        # the run does — with the user rung over them, and write the shared
        # global manifest. Keyed by the brand rather than the cwd, so this
        # one build warms every project-less directory.
        from footman import compose

        with registry.capture() as base:
            for entry in _paths.builtin():
                compose.plugin(entry)
        # Same sealing the app layer does, for the same reason — this child
        # rebuilds the very manifest that answers TAB outside a project.
        registry.seal_needs_project(base)
        reg = _discover.load_tree(files, base=base)
        _manifest.sync_manifest(
            reg,
            cwd,
            completion_max_age=_config.completion_max_age(cfg),
            tasks_file=name,
            path=_paths.global_manifest_path(),
            bake_cwd=False,
            builtin=_paths.builtin(),
            project=False,
        )
        return

    # Mirror the app layer's cwd cascade build; plugin mounts are authored in
    # the tasks files themselves, so discovery alone rebuilds the whole tree.
    reg = _discover.load_tree(files, base=base)
    _manifest.sync_manifest(
        reg, cwd, completion_max_age=_config.completion_max_age(cfg), tasks_file=name
    )


def refresh_source(tasks_file: str, *where: str) -> None:
    """Rebuild one `-f <file>`'s (cwd, file) manifest, swallowing errors.

    `*where` for the same reason as `refresh_cwd`: an arity disagreement
    inside `suppress` is a manifest that silently never appears.
    """
    # A detached background rebuild must never crash or print.
    with contextlib.suppress(Exception):
        from footman import _paths

        _paths.configure_child(*where)
        _rebuild_source(tasks_file)


def _rebuild_source(tasks_file: str) -> None:
    from pathlib import Path

    from footman import _discover, _manifest, _paths, registry

    one = Path(tasks_file).expanduser()
    if not one.is_file():
        return  # a typed-but-missing -f value: nothing to build
    cwd = Path.cwd()
    _maybe_reexec(
        [one],
        "import sys; from footman import _refresh; "
        "_refresh.refresh_source(*sys.argv[1:])",
        tasks_file,  # the entry reads it back off argv
        *_paths.child_args(),  # …and the locations behind it
    )

    # Mirror a real `-f` run (see _app._run): one file, no cascade, cached
    # under the (cwd, file) key with max_age=0 — no background refresh,
    # rebuilt on the next -f run or the next cold TAB.
    base = registry.Group("root")
    reg = _discover.load_tree([one], base=base)
    _manifest.sync_manifest(
        reg,
        cwd,
        completion_max_age=0,
        tasks_file=tasks_file,
        path=_paths.source_manifest_path(cwd, one),
    )
