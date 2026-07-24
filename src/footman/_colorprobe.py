"""Probe a tool's colour control by running it — the engine behind
`fm footman tools color`.

footman forces colour into the tools it spawns (see `context.color_env`): by the
environment for the modern set, by the tool's own flag for the few that ignore
it. Which is which is a fact about each tool, and the honest way to learn it is
to *run* the tool and look at the bytes. This module does that: for each tool it
forces colour on and off, first by environment then by flag, and records whether
each direction obeys the environment (`env`), needs the flag (`flag`), or can be
forced neither way (`none`).

The result generates `_colordata.py` — read by `tools.py` for its forcing table
and by the docs for the support table. A maintainer runs it against the
provisioned binaries; nothing here is imported on a normal `fm` run.

A tool only colourises when it has something to colour, so each needs a
hardcoded *trigger*: a command (and any fixture files) that produces colourable
output. Figured out once per tool; one without a trigger reports `unprobed`.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from footman._toolspec import ToolSpec

_SGR = re.compile("\x1b\\[")  # a CSI escape — how "it emitted colour" is seen

# Forcing environments, mirroring `context.color_env` (presence/absence): on sets
# the force vars; off is NO_COLOR with every force var *absent*.
_ON_ENV = {"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "CLICOLOR": "1"}
_OFF_ENV = {"NO_COLOR": "1"}
_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR")


@dataclass(frozen=True)
class Trigger:
    """How to make a tool emit colour: its argv, and the fixture it needs."""

    args: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)  # filename -> content
    git: bool = False  # init a repo and commit the files first (for git itself)


# One entry per tool footman can actually make colour. A tool absent here is
# reported `unprobed` rather than guessed. Keyed by driver key.
TRIGGERS: dict[str, Trigger] = {
    "ruff": Trigger(("check", "bad.py"), {"bad.py": "import os\n"}),
    "ruff_format": Trigger(("format", "--diff", "bad.py"), {"bad.py": "x=1\n"}),
    "mypy": Trigger(("--pretty", "bad.py"), {"bad.py": "x: int = 'a'\n"}),
    "ty": Trigger(("check", "bad.py"), {"bad.py": "x: int = 'a'\n"}),
    "pytest": Trigger(
        ("test_x.py",), {"test_x.py": "def test_x():\n    assert True\n"}
    ),
    "cspell": Trigger(("lint", "bad.txt"), {"bad.txt": "helllo wrold\n"}),
    "git": Trigger(("log", "--oneline", "-1"), {"a.txt": "hi\n"}, git=True),
}

# A tool's own colour switch, when it is a curated quirk the stub can't surface
# (git spells it as a pre-verb config, not a `--color` flag). `(on, off)` token
# tuples; `pre_verb` places them before the verb. Auto-detected `--color=always`
# switches come from the tool's stub instead (see `flag_candidate`).
_CURATED: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    "git": (("-c", "color.ui=always"), ("-c", "color.ui=never"), True),
}


@dataclass(frozen=True)
class ColourFlag:
    """The tokens that force a tool's colour on/off, and where they go."""

    on: tuple[str, ...] = ()
    off: tuple[str, ...] = ()
    pre_verb: bool = False


def flag_candidate(key: str, spec: ToolSpec) -> ColourFlag | None:
    """The colour switch to try for a tool: a curated quirk (git), else a
    `--color=always/never` detected from its stub (`ToolSpec.color_flags`)."""
    if key in _CURATED:
        on, off, pre = _CURATED[key]
        return ColourFlag(on, off, pre)
    detected = spec.color_flags()
    for _verb, (flag, on_val, off_val) in sorted(detected.items()):
        on = (f"{flag}={on_val}",) if on_val else ()
        off = (f"{flag}={off_val}",) if off_val else ()
        if on or off:
            return ColourFlag(on, off, pre_verb=False)
    return None


@dataclass(frozen=True)
class Verdict:
    """One tool's probed colour control, each direction categorised."""

    on: str  # "env" | "flag" | "none" | "unprobed"
    off: str
    flag: ColourFlag | None = None  # the switch, when a direction needs one


@contextlib.contextmanager
def _fixture(binary: str, trigger: Trigger) -> Iterator[Path]:
    """A throwaway directory with the trigger's files (and a git repo if asked)."""
    with tempfile.TemporaryDirectory(prefix="fm-color-") as tmp:
        cwd = Path(tmp)
        for name, content in trigger.files.items():
            (cwd / name).write_text(content, encoding="utf-8")
        if trigger.git:
            quiet = {"cwd": cwd, "capture_output": True}
            subprocess.run([binary, "init", "-q"], **quiet)
            subprocess.run([binary, "add", "-A"], **quiet)
            subprocess.run(
                [
                    binary,
                    "-c",
                    "user.email=t@t.t",
                    "-c",
                    "user.name=t",
                    "commit",
                    "-qm",
                    "x",
                ],
                **quiet,
            )
        yield cwd


def _argv(
    binary: str, trigger: Trigger, pre: tuple[str, ...], post: tuple[str, ...]
) -> list[str]:
    """`binary [pre-verb flags] <trigger args> [trailing flags]`."""
    return [binary, *pre, *trigger.args, *post]


def _emits_colour(argv: list[str], cwd: Path, env_add: dict[str, str]) -> bool:
    """Run *argv* over a pipe with a clean-plus-*env_add* environment; did it
    print an SGR escape?"""
    env = {k: v for k, v in os.environ.items() if k not in _COLOR_VARS}
    env.update(env_add)
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(_SGR.search(done.stdout + done.stderr))


def probe(key: str, binary: str, spec: ToolSpec) -> Verdict:
    """Categorise one tool by running its trigger with colour forced each way."""
    trigger = TRIGGERS.get(key)
    if trigger is None:
        return Verdict("unprobed", "unprobed")
    flag = flag_candidate(key, spec)
    pre_on = flag.on if (flag and flag.pre_verb) else ()
    post_on = flag.on if (flag and not flag.pre_verb) else ()
    pre_off = flag.off if (flag and flag.pre_verb) else ()
    post_off = flag.off if (flag and not flag.pre_verb) else ()

    with _fixture(binary, trigger) as cwd:

        def emits(env_add: dict[str, str], pre=(), post=()) -> bool:
            return _emits_colour(_argv(binary, trigger, pre, post), cwd, env_add)

        # The trigger must be able to produce colour at all, or nothing is
        # provable — force it maximally (environment and flag together).
        if not emits(_ON_ENV, pre_on, post_on):
            return Verdict("unprobed", "unprobed")

        if emits(_ON_ENV):
            on = "env"
        elif flag and (pre_on or post_on) and emits({}, pre_on, post_on):
            on = "flag"
        else:
            on = "none"

        if not emits(_OFF_ENV):  # footman's off signal keeps it clean
            off = "env"
        elif flag and (pre_off or post_off) and not emits({}, pre_off, post_off):
            off = "flag"
        else:
            off = "none"

    needs_flag = "flag" in (on, off)
    return Verdict(on, off, flag if needs_flag else None)


def probe_all(
    installed: list[tuple[str, str, str, ToolSpec]],
) -> dict[str, tuple[str, Verdict]]:
    """Probe each `(key, argv0, binary, spec)`; return `{key: (argv0, verdict)}`."""
    return {
        key: (argv0, probe(key, binary, spec)) for key, argv0, binary, spec in installed
    }


_HEADER = """\
# Generated by `fm footman tools color` — do not edit by hand.
#
# Each curated tool's probed colour control: how footman forces it on and off.
# `tools.py` reads this for its forcing table; the docs read it for the support
# table. A tool obeys `env` (FORCE_COLOR/NO_COLOR), needs its own `flag`, or can
# force neither way (`none`); `unprobed` had no trigger.
#
# key -> (argv0, on, off, flag_on, flag_off, pre_verb)
_Row = tuple[str, str, str, tuple[str, ...], tuple[str, ...], bool]

COLOUR: dict[str, _Row] = {
"""


def render(results: dict[str, tuple[str, Verdict]]) -> str:
    """The text of `_colordata.py` for a batch of probe results."""
    lines = [_HEADER]
    for key in sorted(results):
        argv0, v = results[key]
        f_on = v.flag.on if v.flag else ()
        f_off = v.flag.off if v.flag else ()
        pre = v.flag.pre_verb if v.flag else False
        lines.append(
            f"    {key!r}: ({argv0!r}, {v.on!r}, {v.off!r}, "
            f"{f_on!r}, {f_off!r}, {pre!r}),"
        )
    lines.append("}\n")
    return "\n".join(lines)
