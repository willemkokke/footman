"""The curated tools, and the little each one needs said about it.

Extraction is generic — `_toolspec` reads a click command's parameters,
`_toolhelp` reads anybody's `--help` — so a driver is not a wrapper. It
carries only what a tool cannot tell you by being asked:

* **which verbs are worth stubbing.** `docker --help` lists forty commands
  and `git` has hundreds; a stub of all of them would be a megabyte nobody
  reads. The list here is the verbs tasks actually call.
* **the quirks.** git's `--help` opens a man page, so it wants `-h`. A tool
  whose real name differs from its attribute (`markdownlint-cli2` is
  `tools.markdownlint`) says so.
* **the default.** Whether `tools.<name>` runs in-process by default, which
  mirrors how it is constructed in `tools.py`.

Everything else — the flags, their help, their types, the negations — comes
from the installed tool, every time the stubs are regenerated.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from footman import _toolhelp, _toolspec
from footman._toolspec import ToolSpec, Verb


@dataclass(frozen=True)
class Provision:
    """How `fm footman tools provision` fetches this tool's *latest* binary.

    Data, like everything else a driver carries. The extractor reads the
    installed tool; this says how to *get* the latest one into a throwaway
    prefix, without touching the machine's own environment.
    """

    kind: str = "uv"
    """`uv` — a PyPI console script, `uv tool install --upgrade`d into an
    isolated prefix (covers the Rust and C++ tools too: ruff, prek, cmake and
    ninja all ship binary wheels). `node` — a package `bun install`s. `bun` —
    bun's own GitHub release, provisioned first because the node tier runs
    through it. `github` / `gitlab` — a prebuilt release asset. `system` —
    already on PATH (git, docker, the uv running this); never provisioned.
    `deferred` — parked, `note` saying why (tea, until > 0.14.2)."""
    package: str = ""
    """The PyPI or npm package, when it differs from the driver's binary name
    (`markdownlint-cli2`); otherwise the binary name is used."""
    repo: str = ""
    """`owner/repo` for a `github` / `gitlab` release download."""
    note: str = ""
    """Why a `deferred` source is parked — shown by `provision`."""

    def target(self, name: str) -> str:
        """What to fetch: the explicit `package`/`repo`, else the tool *name*."""
        return self.package or self.repo or name


@dataclass(frozen=True)
class Driver:
    """One curated tool: what to run, and which verbs to read."""

    name: str
    """The binary as it is invoked."""
    attr: str = ""
    """`tools.<attr>`, when it differs from the binary's name."""
    verbs: tuple[str, ...] = field(default_factory=tuple)
    """The subcommands to stub, dotted for nesting (`compose.up`). Empty
    means the tool is its own command and its options hang off `__call__`."""
    help_flag: str = "--help"
    """git's `--help` opens a man page; `-h` is the help text."""
    in_process: bool = False
    """Whether `tools.<attr>` prefers in-process, as `tools.py` builds it."""
    base: tuple[str, ...] = field(default_factory=tuple)
    """A pre-bound verb: `tools.ruff_format` is `Tool("ruff", "format")`."""
    source: str = "auto"
    """`auto` prefers structure (click) and falls back to `--help`."""
    url: str = ""
    """The tool's home, for the reference page's table."""
    man: bool = False
    """Read each verb's *manual* (`git help <verb>`) instead of its terse
    `-h`. git's `-h` omits about half its flags and prints an idiosyncratic
    multi-form usage; the manual is complete and states one SYNOPSIS per
    form, so both options and positional shape come out right. Runs only at
    stub-generation time, so the man-page dependency never reaches a user."""
    provision: Provision = field(default_factory=Provision)
    """How to fetch the latest binary — the default is a PyPI `uv` install."""

    @property
    def key(self) -> str:
        return self.attr or self.name.replace("-", "_")

    @property
    def wanted(self) -> tuple[str, ...]:
        """The verbs to read: a pre-bound tool wants only the one it binds."""
        if self.base:
            return (".".join(self.base),)
        return self.verbs


DRIVERS: tuple[Driver, ...] = (
    Driver(
        "ruff", verbs=("check", "format", "clean"), url="https://docs.astral.sh/ruff/"
    ),
    Driver(
        "ruff",
        attr="ruff_format",
        base=("format",),
        url="https://docs.astral.sh/ruff/formatter/",
    ),
    Driver("basedpyright", url="https://docs.basedpyright.com/"),
    Driver(
        "uv",
        provision=Provision(kind="system"),  # the uv running provision itself
        url="https://docs.astral.sh/uv/",
        verbs=(
            "sync",
            "lock",
            "run",
            "add",
            "remove",
            "build",
            "publish",
            "export",
            "venv",
            "tree",
            "version",
            "pip.install",
            "pip.compile",
            "pip.sync",
            "pip.list",
            "tool.install",
            "tool.run",
            "tool.upgrade",
        ),
    ),
    Driver(
        "git",
        provision=Provision(kind="system"),
        url="https://git-scm.com/docs",
        help_flag="-h",
        man=True,
        verbs=(
            "add",
            "commit",
            "push",
            "pull",
            "fetch",
            "clone",
            "init",
            "checkout",
            "switch",
            "branch",
            "tag",
            "status",
            "diff",
            "log",
            "rev-parse",
            "describe",
            "stash",
            "restore",
            "worktree",
        ),
    ),
    Driver(
        "docker",
        provision=Provision(kind="system"),
        url="https://docs.docker.com/reference/cli/docker/",
        verbs=(
            "build",
            "run",
            "push",
            "pull",
            "images",
            "ps",
            "exec",
            "logs",
            "compose.up",
            "compose.down",
            "compose.build",
            "compose.logs",
            "compose.ps",
            "compose.run",
            "compose.exec",
        ),
    ),
    Driver(
        "bun",
        provision=Provision(kind="bun", repo="oven-sh/bun"),
        verbs=("install", "add", "remove", "run", "build", "test", "x"),
        url="https://bun.sh/docs/cli/install",
    ),
    Driver(
        "mkdocs",
        verbs=("build", "serve", "new", "gh-deploy"),
        in_process=True,
        url="https://www.mkdocs.org/",
    ),
    Driver(
        "zensical",
        verbs=("build", "serve", "new"),
        in_process=True,
        url="https://zensical.org/",
    ),
    Driver(
        "coverage",
        url="https://coverage.readthedocs.io/",
        verbs=("run", "report", "html", "xml", "json", "combine", "erase", "annotate"),
        in_process=True,
    ),
    Driver(
        "cspell",
        provision=Provision(kind="node"),
        verbs=("lint", "trace", "check", "suggest"),
        url="https://cspell.org/",
    ),
    Driver(
        "prek",
        verbs=("run", "install", "uninstall", "autoupdate", "clean"),
        url="https://prek.j178.dev/",
    ),
    Driver(
        "markdownlint-cli2",
        attr="markdownlint",
        provision=Provision(kind="node"),
        url="https://github.com/DavidAnson/markdownlint-cli2",
    ),
)

_VERSION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)*)\b")


def installed(driver: Driver) -> bool:
    """Whether this machine has the tool to ask."""
    return shutil.which(driver.name) is not None


def version(name: str) -> str:
    """`<tool> --version`, reduced to the version itself."""
    if shutil.which(name) is None:
        return ""
    try:
        done = subprocess.run(
            [name, "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION.search(done.stdout or done.stderr)
    return match[1] if match else ""


def in_process_capable(name: str) -> bool:
    """Whether the tool publishes a `[console_scripts]` entry point.

    That entry point is exactly what `Tool.__call__` resolves to run a tool
    inside footman's process, so its existence *is* the capability — no
    list to maintain, and it answers correctly for a tool footman has never
    heard of.
    """
    from footman import tools

    return tools._console_entrypoint(name) is not None


def extract(driver: Driver) -> ToolSpec:
    """Ask the installed tool to describe itself, best source first.

    click hands over its parameters as data — including `secondary_opts`,
    the negation a `--help` scrape can only find if the tool happens to
    mention it in prose. So structure wins when it is available, and the
    help text covers everyone else.
    """
    spec = ToolSpec(name=driver.name)
    if driver.source in {"auto", "click"}:
        spec = _from_click(driver) or spec
    if not spec.verbs and driver.source in {"auto", "help"}:
        spec = _toolhelp.from_help(
            driver.name,
            verbs=driver.wanted,
            version=version(driver.name),
            in_process=in_process_capable(driver.name),
            flag=driver.help_flag,
            man=driver.man,
        )
    return _rebase(spec, driver.base) if driver.base else spec


def _rebase(spec: ToolSpec, base: tuple[str, ...]) -> ToolSpec:
    """A tool bound to one verb calls it directly: `tools.ruff_format(...)`.

    So that verb's options become the stub's `__call__`, and the rest of
    the tool is somebody else's stub.
    """
    wanted = ".".join(base).replace("-", "_")
    for verb in spec.verbs:
        if verb.name == wanted:
            return ToolSpec(
                name=spec.name,
                help=verb.help or spec.help,
                version=spec.version,
                verbs=(Verb(name="", help=verb.help, options=verb.options),),
                in_process=spec.in_process,
            )
    return ToolSpec(name=spec.name, help=spec.help, version=spec.version)


def _from_click(driver: Driver) -> ToolSpec | None:
    """A spec from the tool's click command, when it is a click tool."""
    from footman import tools

    entry = tools._console_entrypoint(driver.name)
    if entry is None:
        return None
    try:
        command = entry.load()
    except Exception:  # a tool that won't import can't describe itself
        return None
    if not hasattr(command, "params"):
        return None  # not click: argparse mains and plain functions land here
    spec = _toolspec.from_click(command, name=driver.name, version=version(driver.name))
    return _select(spec, driver.wanted)


def _select(spec: ToolSpec, verbs: tuple[str, ...]) -> ToolSpec:
    """Keep the verbs the driver asked for, plus the tool's own options."""
    if not verbs:
        return spec
    wanted = {v.replace("-", "_") for v in verbs} | {""}
    kept = tuple(v for v in spec.verbs if v.name in wanted)
    return ToolSpec(
        name=spec.name,
        help=spec.help,
        version=spec.version,
        verbs=kept,
        in_process=spec.in_process,
    )


def find(key: str) -> Driver | None:
    """The driver for `tools.<key>`."""
    for driver in DRIVERS:
        if driver.key == key:
            return driver
    return None
