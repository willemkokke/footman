"""A live demo of footman's interactive input. Run these:

    fm -f notes/demo_interactive.py release            # single prompt
    fm -f notes/demo_interactive.py deploy             # typed choice menu
    fm -f notes/demo_interactive.py release --version 2.0   # flag wins, no prompt
    fm -f notes/demo_interactive.py build test lint docs    # parallel: ask-serial, run-parallel
    fm -f notes/demo_interactive.py wizard             # interactive task owns the terminal
    fm -f notes/demo_interactive.py --no-input release      # errors loudly, never hangs

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
