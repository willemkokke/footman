"""Scaffold a tasks file — `fm new`, the first thing in an empty directory.

Brand-aware through the configured world: a branded CLI writes its own
tasks filename and teaches its own command, so one implementation serves
every runner. Stock footman declares it in `builtin=`, which is what makes
`fm new` answer in a directory with no tasks at all — and inside a project
the ordinary remedy applies: mount `footman.new` from the root tasks file
to offer it there too.
"""

from __future__ import annotations

from footman import _paths, context
from footman.context import fail
from footman.registry import task

# Pure ASCII on purpose: this docstring is the first help text the new
# tasks file renders, and a console encoding narrower than UTF-8 cannot
# print a glyph outside its codec. A starter file plants nothing a legacy
# terminal has to degrade.
_SCAFFOLD = '''\
from footman import task


@task
def hello(name: str = "world") -> None:
    """Say hello. Replace me with your first real task."""
    print(f"hello {name}")
'''


# The built-in set defaults to needing a project; this is the one task that
# does not — writing the first tasks file is precisely what you do where
# there is no project yet.
@task(expose="global_only")
def new() -> None:
    """Write a starter tasks file in this directory."""
    prog = context.current().prog
    target = context.cwd() / _paths._tasks_file
    if target.exists():
        fail(
            f"{target.name} already exists here — edit it instead, or run "
            f"{prog} new somewhere empty"
        )
    target.write_text(_SCAFFOLD, encoding="utf-8")
    print(f"wrote {target.name} — try: {prog} hello")
