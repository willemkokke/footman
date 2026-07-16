"""duty equivalent of the shared task surface.

Notes vs footman (verified against duty 1.9.0, not assumed):
- duty injects the run context as the first positional arg, so every task must
  accept it (conventionally ``ctx``); omit it and your first real param is
  clobbered by the context object;
- duty DOES support real flags (``duty lint --fix``) *and* ``fix=true``, and it
  chains with flags (``duty format lint --fix test``) and takes bare required
  positionals (``duty deploy prod``) — its grammar is much closer to footman's
  than the "param=value only" reputation suggests;
- what duty does NOT do: eager choice/type validation. A ``Literal`` param
  happily accepts ``rel env=nonsense`` at parse time; footman rejects it with a
  taught error. No nested groups either (flat namespace; ``dist_build`` ->
  ``dist-build``);
- ``ctx.run`` gives the capture / replay-on-failure model for free — something
  footman does not have yet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _project  # noqa: F401  (simulated project import cost)
from duty import duty

SRC = "src tests"


@duty
def lint(ctx, fix: bool = False):
    """Lint with ruff."""
    ctx.run(f"ruff check {SRC}" + (" --fix" if fix else ""), title="Lint")


@duty
def format(ctx, check: bool = False):
    """Format with ruff."""
    ctx.run(f"ruff format {SRC}" + (" --check" if check else ""), title="Format")


@duty
def typecheck(ctx):
    """Type-check with basedpyright."""
    ctx.run("basedpyright", title="Typecheck")


@duty
def test(ctx, args: str = ""):
    """Run the test suite (extra pytest args via args=...)."""
    ctx.run(f"pytest {args}", title="Test")


@duty(pre=["format", "lint", "typecheck", "test"])
def check(ctx):
    """Run format, lint, typecheck, and test."""


@duty
def noop(ctx):
    """No-op (execution-overhead benchmark)."""


@duty
def dist_build(ctx):
    """Build the sdist and wheel."""
    ctx.run("uv build", title="Build")


@duty
def dist_clean(ctx):
    """Remove build artifacts."""
    ctx.run("rm -rf dist", title="Clean")
