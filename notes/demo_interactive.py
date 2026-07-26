"""A live demo of footman's interactive input. Run these:

    fm -f DEMO release                  # single prompt
    fm -f DEMO deploy                   # typed choice menu
    fm -f DEMO release --version 2.0    # flag wins, no prompt
    fm -f DEMO build test lint docs     # parallel: asked one at a time, run together
    fm -f DEMO wizard                   # interactive task owns the terminal
    fm -f DEMO --no-input release       # errors loudly, never hangs

where DEMO is this file, `notes/demo_interactive.py`.

`ask()` makes a defaultless param a CLI-optional option: pass it, or get asked.
"""

from __future__ import annotations

from typing import Annotated, Literal

from footman import ask, prompt, select, task


@task
def release(version: Annotated[str, ask()]):
    """Cut a release — asks for the version if --version isn't passed."""
    print(f"releasing {version}")


@task
def deploy(env: Annotated[Literal["staging", "prod"], ask()]):
    """Deploy — a Literal is a typed choice; a bad answer re-asks."""
    print(f"deploying to {env}")


# A chain of these runs parallel by default; the two that prompt ask one at a
# time (serialised on the terminal), while lint/docs run straight through.
@task
def build(tag: Annotated[str, ask()]):
    print(f"build {tag}")


@task
def test(suite: Annotated[Literal["unit", "e2e"], ask()]):
    print(f"test {suite}")


@task
def lint():
    print("lint — no input needed")


@task
def docs():
    print("docs — no input needed")


@task(interactive=True)
def wizard():
    """An interactive task: it owns the terminal, so it may prompt mid-body
    (and a plain task calling prompt() would get a loud, taught error)."""
    name = prompt("project name? ")
    kind = select("what kind?", ["library", "app", "plugin"])
    print(f"scaffolding a {kind} named {name!r}")
