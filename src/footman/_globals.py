"""Process-globals routers — the parallel regime made real for env (and kin).

In the parallel regime nobody mutates process globals: cwd and env are
context data (`ctx.cwd` / `ctx.env`), applied per child at spawn. The
routers here make that regime hold for code that has never heard of it,
the same way the output router does for `print()`:

- **os.environ is virtualised for the run.** Reads see the run-start
  snapshot plus the current task's overlay — exactly what the subprocess
  branch of the same call would inject as `env=`, closing the
  in-process/subprocess parity hole. Writes from a task body scope to the
  task's own overlay (`ctx.env`): visible to its own reads and every child
  it spawns, invisible to siblings — with a task-attributed teach-once
  note naming the deliberate spelling. Deletion has no additive spelling,
  so it is a taught error.

The class methods of `os._Environ` are wrapped (never the object
replaced), so every alias — `from os import environ`, `os.getenv` — is
covered, and `os.environb` (a different instance) passes through
untouched. Installed at the run boundary (`run_plan`), refcounted for
nested runs, delegating to the originals outside a run — a bare
`import footman` never pays for any of this.

Originals are also re-exported (`real_getcwd`, …) so footman's own
internals keep touching the real process state: the routers exist for
*task* code, not for the framework.
"""

from __future__ import annotations

import os
import threading
from typing import Any

# Originals, captured at import time. Internals use these; the guards and
# routers exist for task code, and footman warning about itself would be
# noise with no lesson in it.
real_getcwd = os.getcwd
real_chdir = os.chdir

_lock = threading.Lock()
_installs = 0  # refcount: nested runs share one install
_snapshot: dict[str, str] = {}  # the run-start environment, pinned
_noted: set[tuple[str, str]] = set()  # (task, kind): teach-once dedup
_environ_saved: dict[str, Any] = {}  # the wrapped class's original methods


def active() -> bool:
    """Whether the routers are installed (a run is in flight)."""
    return _installs > 0


def snapshot_env() -> dict[str, str]:
    """A copy of the pinned run-start environment (empty outside a run)."""
    return dict(_snapshot)


def _norm(key: str) -> str:
    """Windows environment lookups are case-insensitive; mirror that."""
    return key.upper() if os.name == "nt" else key


def _merged() -> dict[str, str]:
    """The virtual environment: run-start snapshot + the task's overlay."""
    from footman.context import current

    overlay = current().env
    if os.name == "nt":
        merged = {k.upper(): v for k, v in _snapshot.items()}
        merged.update((k.upper(), v) for k, v in overlay.items())
        return merged
    return {**_snapshot, **overlay}


def _note(kind: str, text: str) -> None:
    """Emit a teach-once, task-attributed note on the real stderr."""
    from footman.context import current, real_stderr

    key = (current().task or "?", kind)
    with _lock:
        if key in _noted:
            return
        _noted.add(key)
    real_stderr().write(f"note: {text}\n")


def _in_task() -> bool:
    from footman.context import current

    return current().in_task


def _install_environ() -> None:
    env_cls = type(os.environ)
    names = (
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__iter__",
        "__len__",
        "__contains__",
        "copy",
        "setdefault",
    )
    for name in names:
        _environ_saved[name] = getattr(env_cls, name)
    orig_get = _environ_saved["__getitem__"]
    orig_set = _environ_saved["__setitem__"]
    orig_del = _environ_saved["__delitem__"]
    orig_iter = _environ_saved["__iter__"]
    orig_len = _environ_saved["__len__"]
    orig_contains = _environ_saved["__contains__"]
    orig_copy = _environ_saved["copy"]
    orig_setdefault = _environ_saved["setdefault"]

    def _virtual(self: Any) -> bool:
        return self is os.environ and _installs > 0

    def __getitem__(self: Any, key: str) -> str:
        if not _virtual(self):
            return orig_get(self, key)
        return _merged()[_norm(key)]

    def __setitem__(self: Any, key: str, value: str) -> None:
        if not _virtual(self) or not _in_task():
            return orig_set(self, key, value)
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("str expected for environment keys and values")
        from footman.context import current

        ctx = current()
        ctx.env[key] = value
        _note(
            "environ-write",
            f"task {ctx.task or key} sets {key} via os.environ — footman "
            f"scoped it to this task (children see it, siblings don't). "
            f"Say it on purpose with env= / ctx.env.",
        )

    def __delitem__(self: Any, key: str) -> None:
        if not _virtual(self) or not _in_task():
            return orig_del(self, key)
        raise RuntimeError(
            f"deleting {key!s} from os.environ in a parallel task — scoped "
            f"env is additive; spawn the child with an explicit env= that "
            f"omits it, or mark the task serial."
        )

    def __iter__(self: Any) -> Any:
        if not _virtual(self):
            return orig_iter(self)
        return iter(_merged())

    def __len__(self: Any) -> int:
        if not _virtual(self):
            return orig_len(self)
        return len(_merged())

    def __contains__(self: Any, key: object) -> bool:
        if not _virtual(self):
            return orig_contains(self, key)
        return isinstance(key, str) and _norm(key) in _merged()

    def copy(self: Any) -> Any:
        if not _virtual(self):
            return orig_copy(self)
        return _merged()

    def setdefault(self: Any, key: str, value: str) -> str:
        if not _virtual(self):
            return orig_setdefault(self, key, value)
        try:
            return self[key]
        except KeyError:
            self[key] = value
            return value

    env_cls.__getitem__ = __getitem__
    env_cls.__setitem__ = __setitem__
    env_cls.__delitem__ = __delitem__
    env_cls.__iter__ = __iter__
    env_cls.__len__ = __len__
    env_cls.__contains__ = __contains__
    env_cls.copy = copy
    env_cls.setdefault = setdefault


def _restore_environ() -> None:
    env_cls = type(os.environ)
    for name, orig in _environ_saved.items():
        setattr(env_cls, name, orig)
    _environ_saved.clear()


# --- the Popen injection ------------------------------------------------------

_popen_saved: Any = None


def _managed_task() -> tuple[Any, bool]:
    """(ctx, guarded) — guarded only inside a managed parallel task body.

    `unmanaged` is the one off-switch: that token *means* footman stays out."""
    from footman.context import current

    ctx = current()
    return ctx, bool(_installs) and ctx.in_task and not ctx.cwd_unmanaged


def _install_popen() -> None:
    import subprocess

    global _popen_saved
    _popen_saved = subprocess.Popen.__init__
    orig = _popen_saved

    def __init__(self: Any, args: Any, *pa: Any, **kw: Any) -> None:
        ctx, guarded = _managed_task()
        # Inject only for a managed parallel task, only into kwargs left at
        # their defaults, and never when positionals reach as far as cwd
        # (the 10-positional spelling is antique; leave it alone entirely).
        if guarded and len(pa) < 9:
            filled = []
            if kw.get("cwd") is None and ctx.cwd is not None:
                kw["cwd"] = ctx.cwd
                filled.append("cwd")
            if kw.get("env") is None:
                kw["env"] = {**_snapshot, **ctx.env}
                filled.append("env")
            if filled:
                _note(
                    "popen-inject",
                    f"task {ctx.task or '?'} spawns via raw subprocess — "
                    f"footman filled in {' and '.join(filled)} from the task "
                    f"context. Prefer run() for capture and reporting, or "
                    f"pass cwd=/env= to make it deliberate.",
                )
        orig(self, args, *pa, **kw)

    subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]


def _restore_popen() -> None:
    global _popen_saved
    if _popen_saved is not None:
        import subprocess

        subprocess.Popen.__init__ = _popen_saved  # type: ignore[method-assign]
        _popen_saved = None


# --- the os guards ------------------------------------------------------------

_guard_saved: dict[str, Any] = {}


def _install_os_guards() -> None:
    _guard_saved["chdir"] = os.chdir
    _guard_saved["getcwd"] = os.getcwd
    _guard_saved["putenv"] = getattr(os, "putenv", None)
    _guard_saved["unsetenv"] = getattr(os, "unsetenv", None)
    _guard_saved["fchdir"] = getattr(os, "fchdir", None)
    _guard_saved["fork"] = getattr(os, "fork", None)

    def _chdir_error(ctx: Any) -> RuntimeError:
        return RuntimeError(
            f"task {ctx.task or '?'} changes the process directory in a "
            f"parallel task — the cwd belongs to no one there. Mark the task "
            f"serial, or build paths from footman.cwd()."
        )

    orig_chdir = _guard_saved["chdir"]

    def chdir(path: Any) -> None:
        ctx, guarded = _managed_task()
        if guarded:
            raise _chdir_error(ctx)
        orig_chdir(path)

    os.chdir = chdir  # type: ignore[assignment]

    if _guard_saved["fchdir"] is not None:
        orig_fchdir = _guard_saved["fchdir"]

        def fchdir(fd: Any) -> None:
            ctx, guarded = _managed_task()
            if guarded:
                raise _chdir_error(ctx)
            orig_fchdir(fd)

        os.fchdir = fchdir  # type: ignore[assignment]

    orig_getcwd = _guard_saved["getcwd"]

    def getcwd() -> str:
        ctx, guarded = _managed_task()
        if guarded:
            _note(
                "getcwd",
                f"task {ctx.task or '?'} reads the process cwd — in a "
                f"parallel run it can be anyone's; footman.cwd() is this "
                f"task's own directory.",
            )
        return orig_getcwd()

    os.getcwd = getcwd  # type: ignore[assignment]

    def _env_bypass_error(name: str) -> RuntimeError:
        return RuntimeError(
            f"os.{name} bypasses env scoping even in plain Python (it never "
            f"updates os.environ) — assign through os.environ (scoped to "
            f"this task), or pass env= to the call."
        )

    if _guard_saved["putenv"] is not None:
        orig_putenv = _guard_saved["putenv"]

        def putenv(name: str, value: str) -> None:
            _, guarded = _managed_task()
            if guarded:
                raise _env_bypass_error("putenv")
            orig_putenv(name, value)

        os.putenv = putenv  # type: ignore[assignment]

    if _guard_saved["unsetenv"] is not None:
        orig_unsetenv = _guard_saved["unsetenv"]

        def unsetenv(name: str) -> None:
            _, guarded = _managed_task()
            if guarded:
                raise _env_bypass_error("unsetenv")
            orig_unsetenv(name)

        os.unsetenv = unsetenv  # type: ignore[assignment]

    if _guard_saved["fork"] is not None:
        orig_fork = _guard_saved["fork"]

        def fork() -> int:
            ctx, guarded = _managed_task()
            if guarded:
                _note(
                    "fork",
                    f"task {ctx.task or '?'} forks — forking a threaded "
                    f"process is unsafe (the child can inherit locks "
                    f"mid-hold). Prefer run()/subprocess, or mark the task "
                    f"serial.",
                )
            return orig_fork()

        os.fork = fork  # type: ignore[assignment]


def _restore_os_guards() -> None:
    if not _guard_saved:
        return
    os.chdir = _guard_saved["chdir"]
    os.getcwd = _guard_saved["getcwd"]
    if _guard_saved["fchdir"] is not None:
        os.fchdir = _guard_saved["fchdir"]
    if _guard_saved["putenv"] is not None:
        os.putenv = _guard_saved["putenv"]
    if _guard_saved["unsetenv"] is not None:
        os.unsetenv = _guard_saved["unsetenv"]
    if _guard_saved["fork"] is not None:
        os.fork = _guard_saved["fork"]
    _guard_saved.clear()


# --- multiprocessing detection ------------------------------------------------

_mp_saved: Any = None


def _install_multiprocessing() -> None:
    global _mp_saved
    try:
        from multiprocessing import process as mp_process
    except Exception:  # a stripped-down build without multiprocessing
        return
    _mp_saved = mp_process.BaseProcess.start
    orig = _mp_saved

    def start(self: Any) -> None:
        ctx, guarded = _managed_task()
        if guarded:
            _note(
                "mp-start",
                f"task {ctx.task or '?'} spawns worker processes in-process "
                f"— they inherit the real environment, not the task's "
                f"overlay. A tool that parallelises itself loses little in "
                f"the serial lane: mark the task serial.",
            )
        return orig(self)

    mp_process.BaseProcess.start = start  # type: ignore[method-assign]


def _restore_multiprocessing() -> None:
    global _mp_saved
    if _mp_saved is not None:
        from multiprocessing import process as mp_process

        mp_process.BaseProcess.start = _mp_saved  # type: ignore[method-assign]
        _mp_saved = None


def install() -> None:
    """Arm the routers for a run. Refcounted; the first install pins the
    environment snapshot (so anything published at the run boundary —
    colour, say — is in it)."""
    global _installs
    with _lock:
        _installs += 1
        if _installs > 1:
            return
        # Warm stdlib caches that lazily read process globals on first use
        # (tempfile.gettempdir walks candidates with a getcwd fallback), so a
        # task's first mkdtemp doesn't trip the getcwd note for a read the
        # task never made.
        import tempfile

        tempfile.gettempdir()
        _snapshot.clear()
        _snapshot.update(os.environ)
        _noted.clear()
        _install_environ()
        _install_popen()
        _install_os_guards()
        _install_multiprocessing()


def uninstall() -> None:
    """Disarm after a run; the last uninstall restores the originals."""
    global _installs
    with _lock:
        if _installs == 0:
            return
        _installs -= 1
        if _installs > 0:
            return
        _restore_multiprocessing()
        _restore_os_guards()
        _restore_popen()
        _restore_environ()
        _snapshot.clear()
