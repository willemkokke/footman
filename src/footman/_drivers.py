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

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from footman import _toolhelp, _toolspec
from footman._toolspec import ToolSpec, Verb


@dataclass(frozen=True)
class Provision:
    """How `fm tools.provision` fetches this tool's *latest* binary.

    Data, like everything else a driver carries. The extractor reads the
    installed tool; this says how to *get* the latest one into a throwaway
    prefix, without touching the machine's own environment.
    """

    kind: str = "uv"
    """`uv` — a PyPI console script, `uv tool install --upgrade`d into an
    isolated prefix (covers the Rust and C++ tools too: ruff, prek, cmake and
    ninja all ship binary wheels). `node` — a package `bun install`s. `bun` —
    bun's own GitHub release, provisioned first because the node tier runs
    through it. `github` / `gitlab` / `gitea` — a prebuilt release asset.
    `docker` — a static build from docker's own per-platform index, which is
    a directory listing rather than an asset list. `man` — a release's
    manual pages, for a tool read from its manual rather than its `-h`.
    `system` — already on PATH (the uv running this); never provisioned.
    `deferred` — parked, `note` saying why (as tea was, until 0.15.0)."""
    package: str = ""
    """The PyPI or npm package, when it differs from the driver's binary name
    (`markdownlint-cli2`); otherwise the binary name is used."""
    repo: str = ""
    """`owner/repo` for a `github` / `gitlab` release download."""
    note: str = ""
    """Why a `deferred` source is parked — shown by `provision`."""
    plugins: tuple[str, ...] = ()
    """Extra packages to install *alongside* the tool (`uv --with`), so a
    plugin-extended CLI is read whole. pytest's `--cov*` flags come from
    `pytest-cov`; without it a bare provisioned pytest would stub none of them."""

    def target(self, name: str) -> str:
        """What to fetch: the explicit `package`/`repo`, else the tool *name*."""
        return self.package or self.repo or name


@dataclass(frozen=True)
class Plugin:
    """A separately released program a tool loads as one of its verbs.

    `docker compose` is not part of docker. It is its own project on its
    own release line, shipped as a binary the CLI discovers under the
    user's config directory — so a static docker build has no compose in
    it, and reading `docker compose up --help` reads whatever the *machine*
    happened to have installed. Under a walk that is a lie with a date on
    it: compose's surface of today, recorded as docker 20.10's. Across a
    platform matrix it is worse, because two machines with different
    compose versions look like a genuine per-platform divergence.

    So the plugin is fetched like the tool is, and paired by date: the
    release a user of *that* docker would have had. The verbs keep their
    place — `tools.docker.compose.up(...)` is still how you say it — and
    the pairing is deterministic, so the same walk gives the same answer
    forever.
    """

    name: str
    """The binary as the host tool looks for it (`docker-compose`)."""
    repo: str
    """`owner/repo` of the plugin's own releases."""
    owns: str = ""
    """The verb prefix these releases account for (`compose`)."""
    path: str = ""
    """Where the host tool looks, relative to the user's home
    (`.docker/cli-plugins`)."""
    since: str = ""
    """The first release that was a plugin at all. compose 1.x was a
    standalone program you ran as `docker-compose`; `docker compose` did
    not exist until 2.0. Dropping a 1.x binary into the plugin directory
    would not make it one, so an era before this pairs with nothing and
    the verbs read as absent — which they were."""


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
    shorts: str = "only"
    """Short-option policy for the stub: `"none"` never keys on a short,
    `"only"` (default) keys on one *when it is the option's sole spelling*
    (python's `-m`, git's `-C`), and `"all"` also keys on a short that has a
    long form. Read only from `--help`, never a man page (its prose is noisy)."""
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
    plugins: tuple[Plugin, ...] = field(default_factory=tuple)
    """Companion programs some of the verbs really come from, each released
    on its own line — see `Plugin`."""

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
        provision=Provision(package="uv"),  # PyPI, `uv tool install uv` — never host
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
        # Read from its manual, and a manual is not a binary: kernel.org
        # publishes the pages per release, so nothing is installed and
        # nothing is run.
        provision=Provision(kind="man"),
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
        # Docker publishes static per-platform builds of every release, so
        # it is fetched like any other tool rather than read from the host.
        provision=Provision(kind="docker"),
        plugins=(
            Plugin(
                name="docker-compose",
                repo="docker/compose",
                owns="compose",
                path=".docker/cli-plugins",
                since="2.0.0",
            ),
            # `docker build` is buildx wherever buildx is installed, which
            # is everywhere docker itself is these days. Left unpaired, the
            # static binary falls back to the builder docker shipped with
            # before 2019 and the stub grows `--compress` and `--cpu-shares`
            # while losing `--platform` and `--push`.
            Plugin(
                name="docker-buildx",
                repo="docker/buildx",
                owns="build",
                path=".docker/cli-plugins",
            ),
        ),
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
    Driver(
        "gh",
        provision=Provision(kind="github", repo="cli/cli"),
        url="https://cli.github.com/manual/",
        verbs=(
            "pr.create",
            "pr.list",
            "pr.view",
            "pr.checkout",
            "pr.merge",
            "issue.create",
            "issue.list",
            "issue.view",
            "release.create",
            "release.upload",
            "release.view",
            "release.list",
            "repo.clone",
            "repo.view",
            "run.list",
            "run.view",
            "run.watch",
            "workflow.run",
            "workflow.list",
            "auth.status",
            "auth.login",
            "api",
            "label.list",
            "label.create",
        ),
    ),
    Driver(
        "tea",
        # 0.13.0-0.14.2 interrogate the console at start-up (an OSC theme
        # query, gitea/tea#1054, fixed in 0.15.0) and hang under ANY captured
        # spawn on Windows — the caller's terminal and a hidden conhost
        # alike. Measured, not inferred: every other release back to 0.9.0
        # answers in 0.1s under the same spawn. Those four read as holes on
        # Windows and fold in from POSIX, where a piped stdout makes the
        # query skip. 0.9.1 ships no windows asset at all. Not a floor —
        # 0.12.0 and below read fine, and a floor would discard them.
        provision=Provision(kind="gitea", repo="gitea/tea"),
        url="https://gitea.com/gitea/tea",
        verbs=(
            "issues.create",
            "issues.list",
            "issues.close",
            "pulls.create",
            "pulls.list",
            "pulls.checkout",
            "pulls.merge",
            "releases.create",
            "releases.list",
            "releases.assets",
            "repos.create",
            "repos.list",
            "repos.fork",
            "labels.list",
            "labels.create",
            "milestones.list",
            "milestones.create",
            "comments.add",
            "comments.list",
            "branches.list",
            "logins.add",
            "logins.list",
            "whoami",
            "clone",
            "api",
        ),
    ),
    Driver(
        "eclint",
        provision=Provision(kind="gitlab", repo="willemkokke/eclint"),
        url="https://gitlab.com/willemkokke/eclint",
    ),
    Driver("djlint", url="https://www.djlint.com/"),
    Driver("mypy", url="https://mypy.readthedocs.io/"),
    Driver("ty", verbs=("check",), url="https://docs.astral.sh/ty/"),
    Driver("twine", verbs=("upload", "check"), url="https://twine.readthedocs.io/"),
    Driver("git-changelog", url="https://pawamoy.github.io/git-changelog/"),
    Driver("git-cliff", url="https://git-cliff.org/"),
    Driver(
        "pyproject-build",
        attr="build",
        provision=Provision(package="build"),
        url="https://build.pypa.io/",
    ),
    Driver("cmake", url="https://cmake.org/documentation/"),
    Driver("ninja", url="https://ninja-build.org/"),
    Driver(
        "pytest",
        url="https://docs.pytest.org/",
        in_process=True,  # `tools.py` builds it in-process, via `pytest:main`
        provision=Provision(plugins=("pytest-cov",)),  # so --cov* is read too
    ),
    Driver(
        "python",
        provision=Provision(kind="python"),  # unpinned: whatever uv calls newest
        url="https://docs.python.org/3/using/cmdline.html",
    ),
    # The shells footman autocompletes for. Their stubs are hand-written (a
    # `source="manual"` driver is listed and paged but never extracted or
    # re-synced): what matters is `<shell>("command")` -> `<shell> -c command`,
    # not the shell binary's own hundred flags.
    Driver("bash", source="manual", url="https://www.gnu.org/software/bash/"),
    Driver("zsh", source="manual", url="https://www.zsh.org/"),
    Driver("fish", source="manual", url="https://fishshell.com/"),
    Driver("pwsh", source="manual", url="https://learn.microsoft.com/powershell/"),
    Driver("nu", source="manual", url="https://www.nushell.sh/"),
    Driver(
        "cmd",
        source="manual",
        url="https://learn.microsoft.com/windows-server/administration/windows-commands/cmd",
    ),
)

_HOST_READ = frozenset(d.name for d in DRIVERS if d.provision.kind == "system")
"""Tools read straight off the host, never provisioned into an isolated prefix
— the only ones for which Homebrew is consulted on macOS.

Empty as it stands: git was the last, and its manuals come from kernel.org
per release now, so every stub footman ships is read from something it
fetched itself. The rule is kept for whatever joins that tier next."""


def _brew_prefixes() -> tuple[str, ...]:
    """Homebrew's prefixes, most-authoritative first: an explicit
    `HOMEBREW_PREFIX`, then the Apple-silicon and Intel defaults."""
    prefixes: list[str] = []
    if "HOMEBREW_PREFIX" in os.environ:
        prefixes.append(os.environ["HOMEBREW_PREFIX"])
    for default in ("/opt/homebrew", "/usr/local"):
        if default not in prefixes:
            prefixes.append(default)
    return tuple(prefixes)


def _resolve(name: str) -> str | None:
    """The executable to read a tool from.

    A *host-read* tool on macOS (git; docker and uv carry no keg) prefers its
    Homebrew **keg** (`opt/<name>/bin/<name>`) — the newest build, and it
    survives `brew unlink`, so an intentionally-off-`PATH` tool is still read;
    a tool with no keg simply falls through. Everything else — every provisioned
    tier (pip/uv/npm/release) and every platform but macOS — is plain
    `shutil.which`, so a `provision --sync` prefix and a venv win, and a stale
    `/opt/homebrew/bin` console-script shim is never picked.
    """
    if name in _HOST_READ and sys.platform == "darwin":
        for prefix in _brew_prefixes():
            keg = os.path.join(prefix, "opt", name, "bin", name)
            if os.access(keg, os.X_OK) and not os.path.isdir(keg):
                return keg
    return shutil.which(name)


def installed(driver: Driver) -> bool:
    """Whether this machine has the tool to ask."""
    return _resolve(driver.name) is not None


def version(name: str) -> str:
    """`<tool> --version`, reduced to the version itself.

    Reads the binary *extraction* resolves, which is not always the one a
    task would run (`_resolve` prefers a Homebrew keg for host-read tools).
    The parsing is shared with `tools.Tool.installed_version` so only the
    choice of binary can ever differ, never the grammar.
    """
    return _read_version(name)[0]


def _read_version(name: str) -> tuple[str, str]:
    """`(version, diagnosis)` — the second names *why* the first is empty.

    An empty version has three very different causes — the spawn failed, the
    spawn hung, the output carried no version token — and a check that can't
    say which teaches nothing when it trips (the CI flake that motivated
    this reported `gh (—)` and left every hypothesis standing).
    """
    from footman import tools

    binary = _resolve(name)
    if binary is None:
        return "", "not on PATH"
    # A version read must never touch the network: gh runs its update check
    # from any command — a banner, a state write, a remote call — unless
    # told not to. The variable is gh's own; every other tool ignores it.
    env = {**os.environ, "GH_NO_UPDATE_NOTIFIER": "1"}
    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
            # No console for a version read either — the same start-up
            # terminal query that wedged help reads runs before --version.
            creationflags=_toolhelp.DETACHED,
        )
    except subprocess.TimeoutExpired:
        return "", "timed out after 30s"
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"spawn failed: {type(exc).__name__}: {exc}"
    found = _without_build_tail(tools.read_version(done.stdout or done.stderr))
    if found:
        return found, ""
    lines = (done.stdout or done.stderr).strip().splitlines()
    head = lines[0][:80] if lines else "<no output>"
    return "", f"no version token (exit {done.returncode}): {head!r}"


_BUILD_TAIL = re.compile(r"\.(?!post\d)[A-Za-z].*$")


def _without_build_tail(version: str) -> str:
    """A vendored build tail dropped, so the reading names a real release.

    PyPI ships `ninja` 1.13.0; the binary in it answers
    `1.13.0.git.kitware.jobserver-pipe-1`. The history keys on what was read,
    so that string became the base — and nothing in the index matches it, so
    ninja could not be primed at all.

    Only a *dot-attached* alphabetic tail goes, which is what a vendored
    build looks like. `0.6.0-wk.5` keeps its hyphenated series, because that
    is eclint's own release identity rather than a build of something else,
    and `.post1` is spared because it names a real published release.
    """
    return _BUILD_TAIL.sub("", version)


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
        # A fetched manual names its own release, and there is no binary
        # to ask: the whole point of reading the pages is that the version
        # they document never has to be installed.
        tree = _toolhelp._fetched_manpath() if driver.man else ""
        spec = _toolhelp.from_help(
            driver.name,
            binary=_resolve(driver.name),
            verbs=driver.wanted,
            version=(
                _toolhelp.man_version(Path(tree)) if tree else version(driver.name)
            ),
            in_process=in_process_capable(driver.name),
            flag=driver.help_flag,
            man=driver.man,
            shorts=driver.shorts,
        )
    return _anonymous(_rebase(spec, driver.base) if driver.base else spec)


def _anonymous(spec: ToolSpec) -> ToolSpec:
    """Replace this machine's home directory with `~` throughout *spec*.

    Tools that default an option to a path under `$HOME` report it
    expanded: docker says its config lives in `/Users/willem/.docker`, and
    that string went into the snapshot, the store, and the published stub —
    one machine's home directory shipped to PyPI as if it were docker's
    documented default.

    It is also the one difference guaranteed to divide every platform.
    Linux reads `/home/runner/.docker` and Windows
    `C:\\Users\\runneradmin\\.docker` for the same option of the same
    release, so each leg of the matrix would overwrite the last, every
    weekly run would record a change nobody made, and the release gate —
    which fires on "did anything change" — would never be quiet again.

    `~` is what the tool means and what every platform can agree on.
    """
    home = str(Path.home()).rstrip("/\\")
    if not home:  # pragma: no cover - a home of "/" is not a home
        return spec

    def scrub(text: str) -> str:
        return text.replace(home, "~") if isinstance(text, str) else text

    verbs = tuple(
        replace(
            verb,
            help=scrub(verb.help),
            options=tuple(
                replace(opt, help=scrub(opt.help), default=scrub(opt.default))
                for opt in verb.options
            ),
        )
        for verb in spec.verbs
    )
    return replace(spec, help=scrub(spec.help), verbs=verbs)


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
    """A spec from the tool's click command, when it is a click tool.

    Only when the importable package and the PATH binary are the **same
    release**. The entry point loads from this process's environment while
    the binary comes from `PATH`, and nothing ties the two together: a prime
    reading mkdocs 1.4.0 from a throwaway venv would import *this* venv's
    1.6.1 and record its surface under 1.4.0's label — which is exactly what
    happened, nine empty deltas in a row, before this guard. A mismatch (or
    a binary whose version cannot be read) falls through to the help path,
    which always asks the binary itself.
    """
    from footman import tools

    entry = tools._console_entrypoint(driver.name)
    if entry is None:
        return None
    packaged = getattr(getattr(entry, "dist", None), "version", "") or ""
    binary = version(driver.name)
    if not binary or binary != packaged:
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
