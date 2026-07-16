"""'Just write a typer app' — the DIY baseline.

typer matches footman on the typed-CLI *features* (real flags, nested groups via
add_typer, Enum validation), so the interesting contrast isn't features — it's:
- you wire the app object yourself (no zero-boilerplate discovery);
- typer pulls in rich + shellingham (typer 0.27 dropped its click dependency and
  ships its own parser), and you pay that import on every launch, completion
  included (the launch overhead this benchmark measures);
- no separator-free chaining (click's chain mode has the limits duty's does).

Run: python app.py <command>
"""

import subprocess
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _project  # noqa: F401  (simulated project import cost)
import typer

app = typer.Typer(add_completion=True, no_args_is_help=True, help="Dev tasks.")
dist = typer.Typer(help="Build and publish")
app.add_typer(dist, name="dist")

SRC = ("src", "tests")


def _run(*cmd: str) -> int:
    return subprocess.run(cmd).returncode


@app.command()
def lint(fix: bool = False):
    """Lint with ruff."""
    raise typer.Exit(_run("ruff", "check", *SRC, *(["--fix"] if fix else [])))


@app.command()
def format(check: bool = False):
    """Format with ruff."""
    raise typer.Exit(_run("ruff", "format", *SRC, *(["--check"] if check else [])))


@app.command()
def typecheck():
    """Type-check with basedpyright."""
    raise typer.Exit(_run("basedpyright"))


@app.command()
def test(pytest_args: Annotated[list[str] | None, typer.Argument()] = None):
    """Run the test suite."""
    raise typer.Exit(_run("pytest", *(pytest_args or [])))


@app.command()
def check():
    """Run format, lint, typecheck, and test."""


@app.command()
def noop():
    """No-op (execution-overhead benchmark)."""


@dist.command()
def build():
    """Build the sdist and wheel."""
    raise typer.Exit(_run("uv", "build"))


@dist.command()
def clean():
    """Remove build artifacts."""
    raise typer.Exit(_run("rm", "-rf", "dist"))


if __name__ == "__main__":
    app()
