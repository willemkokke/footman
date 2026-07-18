"""Test your tasks — an in-process CLI runner and a silent recording context.

Three altitudes, matching how tasks are actually written:

1. **Plain calls.** `@task` returns your function untouched, so a task body
   is unit-testable by just calling it: `lint(fix=True)`.
2. **Recording.** `recording()` captures the commands a block *would* run —
   silently, without executing anything — so a test can assert on them:

   ```python
   from footman.testing import recording
   from tasks import lint

   def test_lint_fix_passes_the_flag():
       with recording() as steps:
           lint(fix=True)
       assert steps[0].command == "ruff check . --fix"
   ```

3. **CLI-level.** `Runner.invoke` drives argv → exit code → output →
   structured results, entirely in-process:

   ```python
   result = Runner().invoke("--dry-run release 1.2.0 --push")
   assert result.ok
   assert result.results[0].task == "release"
   ```

Everything here is stdlib-only — the zero-dependency promise holds. The
pytest fixtures in `footman.pytest_plugin` are thin shims over this module,
so non-pytest users get the same power.
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footman import _app
from footman.app import App
from footman.context import Context, StepResult, use_context
from footman.executor import TaskResult
from footman.registry import Group

__all__ = [
    "Result",
    "Runner",
    "StepResult",
    "TaskResult",
    "recording",
    "use_context",
]


@contextlib.contextmanager
def recording(**overrides: Any) -> Iterator[list[StepResult]]:
    """Capture the commands a block would `run()` — silently, not executing.

    Yields the live step list; each `run()`/`tools.*` call inside the block
    appends a `StepResult` instead of executing. In-process callables passed
    to `run()` are skipped too — that is the point, but worth knowing.
    Keyword overrides go to the underlying `Context` (e.g. `env={...}`).
    """
    ctx = Context(dry_run=True, quiet=True, **overrides)
    with use_context(ctx):
        yield ctx.steps


@dataclass
class Result:
    """Everything one `Runner.invoke` produced."""

    exit_code: int
    stdout: str
    stderr: str
    results: list[TaskResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@contextlib.contextmanager
def _isolated(cwd: Path | None) -> Iterator[None]:
    """A throwaway completion cache (and optional cwd) for one invocation."""
    with tempfile.TemporaryDirectory(prefix="footman-test-") as tmp:
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = tmp
        try:
            if cwd is not None:
                with contextlib.chdir(cwd):
                    yield
            else:
                yield
        finally:
            if old is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old


class Runner:
    """Drive a footman CLI in-process, capturing output and results.

    Pass a branded `App` to test a custom CLI (`Runner(App(prog="acme"))`) —
    error prefixes, `--version`, and hints then use that brand, exactly as
    they would for real users.
    """

    def __init__(self, app: App | None = None) -> None:
        self.app = app if app is not None else App()

    def invoke(
        self,
        args: str | list[str],
        *,
        tasks: Path | Group | None = None,
        cwd: Path | None = None,
    ) -> Result:
        """Run one command line and return everything it produced.

        `args` is a string (shlex-split) or an argv list. `tasks` overrides
        discovery: a `Path` routes through `--tasks-file`, a `Group` skips
        discovery entirely (an in-memory tree, no files needed). Without it,
        the normal `tasks.py` cascade from `cwd` applies. Never raises on
        task failure — the code is in the `Result`; `KeyboardInterrupt`
        passes through.
        """
        argv = shlex.split(args) if isinstance(args, str) else [str(a) for a in args]
        out, err = io.StringIO(), io.StringIO()
        collected: list[TaskResult] = []
        with (
            _isolated(cwd),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            if isinstance(tasks, Group):
                # One shared surface with the real CLI (help/version/list/tree/
                # json all honoured) — no drifting Group-mode re-implementation.
                code = _app.run_group(
                    tasks, argv, brand=self.app.brand, collect=collected
                )
            else:
                if tasks is not None:
                    argv = ["--tasks-file", str(tasks), *argv]
                # `_run`, not `run`: bypass the CLI's KeyboardInterrupt->130
                # wrapper so Ctrl-C reaches pytest (the invoke docstring's
                # contract). The wrapper stays for real CLI entry.
                code = _app._run(argv, brand=self.app.brand, collect=collected)
        return Result(code, out.getvalue(), err.getvalue(), collected)
