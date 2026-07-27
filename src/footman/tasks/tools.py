"""Keep the `tools.*` stubs honest — `fm tools …`.

The bridge never goes stale, because it transcribes nothing. Its *stub*
can: a stub is a description of a tool at a version, and tools move. These
tasks close that gap by regenerating the description from the installed
tools and by failing a check when the two disagree.

    fm tools.list                  what footman curates, and what's installed
    fm tools.spec ruff             what one tool says about itself, right now
    fm tools.sync                  rewrite the stubs from the installed tools
    fm tools.audit                 which tools have moved past their snapshot
    fm tools.color                 how footman forces colour, per tool

A stub is a snapshot, not a contract: `sync` takes one, `audit` says which
tools have released a newer version since. Being behind is news rather than
a fault, so `audit` reports and exits zero unless you ask for `--strict`,
and the snapshots are retaken at release time rather than the moment a tool
ships. A snapshot only ever moves forward: a tool that isn't installed, is
missing from a `--prefix`, or reads older than the stub already records is
named and left alone — a check that quietly covered three of thirteen would
be worse than no check.
"""

from __future__ import annotations

import re as _re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Literal

from footman import _drivers, _stubgen, _toolhistory, _toolspec
from footman._describe import bold, cyan, wants_color
from footman.context import current
from footman.params import doc
from footman.registry import Group
from footman.tools import version_tuple as _version_tuple

tasks = Group("tools", help="Keep the tools.* stubs honest")

_STUBS = Path(__file__).resolve().parent.parent / "_stubs"
# Repo-only, deliberately outside `src/`: generation reads the history and
# generation is a maintainer task run from a checkout, while users read the
# stubs — which already carry everything the log is for. Shipping it would
# make every install pay for history nobody reads.
_HISTORY = Path(__file__).resolve().parents[3] / "tool-history"


class _Ambiguous(Exception):
    """Two readings whose versions the comparator cannot separate.

    Raised rather than resolved, because every resolution would be a guess:
    see `_observe`. The caller names the tool and leaves its stub alone.
    """

    def __init__(self, key: str, reading: str, base: str) -> None:
        super().__init__(f"{key}: cannot tell {reading} from the recorded {base}")
        self.key, self.reading, self.base = key, reading, base


def _stub_path(key: str) -> Path:
    return _STUBS / f"{key}.pyi"


def _history_path(key: str) -> Path:
    return _HISTORY / f"{key}.json"


@contextmanager
def _on_path(prefix: str | Path) -> Iterator[None]:
    """Read binaries from *prefix*`/bin` for the duration — a
    `fm tools.provision` directory, so a task reads the provisioned set
    instead of whatever this machine happens to have.

    Empty *prefix* is a no-op, so every caller can pass its parameter
    straight through.

    Inside a run the overlay goes through `ctx.env`, which scopes it to this
    task and its children: a sibling's `PATH` is untouched, and footman has
    no reason to draw its own note about a raw `os.environ` write. Called
    bare — from a test, or a script importing the task — there is no router
    to serve that overlay, so it patches `os.environ` and restores it, the
    same bare-call fallback `context._process_state` makes.
    """
    if not prefix:
        yield
        return
    import os

    bindir = Path(prefix).expanduser().resolve() / "bin"
    inherited = os.environ.get("PATH", "")
    with _overlay(PATH=f"{bindir}{os.pathsep}{inherited}"):
        yield


@contextmanager
def _overlay(**values: str) -> Iterator[None]:
    """Set environment variables for the duration, then put them back.

    Inside a run the overlay goes through `ctx.env`, which scopes it to this
    task and its children: a sibling's environment is untouched, and footman
    has no reason to draw its own note about a raw `os.environ` write. Called
    bare — from a test, or a script importing the task — there is no router
    to serve that overlay, so it patches `os.environ` and restores it, the
    same bare-call fallback `context._process_state` makes.
    """
    import os

    from footman import _globals

    target = current().env if _globals.active() else os.environ
    saved = {key: target.get(key) for key in values}
    target.update(values)
    try:
        yield
    finally:
        for key, was in saved.items():
            if was is None:
                target.pop(key, None)
            else:
                target[key] = was


@contextmanager
def _sandboxed(scratch: Path) -> Iterator[None]:
    """Keep everything a prime downloads inside *scratch*.

    uv writes to two places of its own accord, and neither is ours to fill:
    a wheel cache, and the store its managed interpreters live in — the one
    holding the pythons this machine actually runs. A prime of CPython's
    releases put 90 interpreters in that store and left them there, because
    nothing in this file had reason to think it owned them.

    Pointing both inside the scratch directory makes the cleanup structural
    rather than a rule someone has to remember: one `rmtree` at the end
    removes every byte the walk caused, and the interpreter you develop
    against is never a candidate for deletion in the first place.
    """
    with _overlay(
        UV_CACHE_DIR=str(scratch / "cache"),
        UV_PYTHON_INSTALL_DIR=str(scratch / "pythons"),
    ):
        yield


def _platform() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


def _generate(driver: _drivers.Driver) -> str:
    """The stub text for one installed tool, formatted the way ruff would.

    The reading goes into the history first, and the stub is rendered from
    *that* — so what ships is a view of the record rather than a second
    record that can disagree with it.
    """
    spec = _drivers.extract(driver)
    doc = _observe(driver, spec)
    return _stub_from(driver, doc, in_process=spec.in_process)


def _stub_from(driver: _drivers.Driver, doc: dict, *, in_process: bool = False) -> str:
    """The stub text for a tool's history.

    Rendered from the *union*, not the newest release: a flag the tool has
    since dropped stays completable, because the reader may be running a
    version that still has it, and its docstring says when it went. With a
    history of one release the union is that release, so nothing is claimed
    that has not been observed.

    The header reports the base observation's own platform rather than this
    machine's — the file says what was read, and a prime run elsewhere must
    not rewrite that claim.
    """
    base = doc["base"]
    spec = _toolhistory.union(doc, name=driver.name, in_process=in_process)
    return _formatted(
        _stubgen.render(
            spec,
            platform=(base.get("platforms") or [_platform()])[0],
            class_name=_class_name(driver.key),
            in_process=_mode(driver, spec),
        )
    )


def _observe(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> dict:
    """Record this reading in the tool's history, and return the history.

    Three cases, and the third is the one the format exists for: a first
    reading opens the file; re-reading the release the base already holds
    updates it in place; a *newer* release becomes the base and demotes the
    old one to a delta — one entry rewritten, the rest untouched.
    """
    path = _history_path(driver.key)
    surface = _toolhistory.surface_of(spec)
    version = spec.version or "unknown"
    doc = _toolhistory.load(path)
    if doc is None:
        doc = _toolhistory.new(
            driver.key,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    elif doc["base"]["version"] == version:
        doc["base"]["surface"] = surface
        doc["base"]["extractor"] = _toolhistory.EXTRACTOR
        doc["base"]["platforms"] = sorted(
            {*doc["base"].get("platforms", []), _platform()}
        )
    elif _version_tuple(version) == _version_tuple(doc["base"]["version"]):
        # Two builds of one base — eclint's `0.6.0-wk.3` against its
        # `-wk.5`. The comparator cannot separate them and the dates cannot
        # help, because an incoming reading is stamped today whatever build
        # it holds. Ordering a chain breaks such a tie on publication date;
        # here there is no such date, so the base does not move. Declining is
        # the only answer that cannot be wrong, and it is what "a snapshot
        # only ever moves forward" means when forward is unknowable.
        raise _Ambiguous(driver.key, version, doc["base"]["version"])
    elif _version_tuple(version) < _version_tuple(doc["base"]["version"]):
        # An *older* reading is an older observation, not a new head. Demoting
        # on any change let a machine with a stale tool rewrite the base and
        # push the newer release down the chain as though it came first — the
        # base only ever moves forward, exactly as a snapshot does.
        _toolhistory.extend(
            doc,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    else:
        _toolhistory.promote(
            doc,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    _toolhistory.save(doc, path)
    return doc


def _today() -> str:
    """The observation date. A release's own date belongs to the release, and
    the fetchers will carry it; a live reading only knows when it looked."""
    import datetime

    return datetime.date.today().isoformat()


def _render(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> str:
    return _stubgen.render(
        spec,
        platform=_platform(),
        class_name=_class_name(driver.key),
        in_process=_mode(driver, spec),
    )


def _mode(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> str:
    """How this tool runs: in footman's process by default, or on request.

    A Python tool publishes a `[console_scripts]` entry point, which is
    what `Tool.__call__` resolves — so the capability is detected, not
    listed. Whether footman *prefers* it is the driver's business.
    """
    if driver.in_process:
        return "default"
    return "available" if spec.in_process else "no"


def _class_name(key: str) -> str:
    return "".join(part.title() for part in key.split("_"))


def _formatted(text: str) -> str:
    """Run the generated text through the linter and formatter that guard the
    repo.

    Generated code lands in `src/`, where `ruff check` and `ruff format
    --check` run on every commit — so it has to satisfy both by construction,
    not by a follow-up nobody remembers. Import sorting is the half a
    formatter cannot do: the generator writes one `from footman.tools import
    …` line and ruff's isort has its own opinion about aliased members.
    """
    import subprocess

    for argv in (
        [
            "ruff",
            "check",
            "--fix",
            "--select",
            "I",
            "--stdin-filename",
            "stub.pyi",
            "-",
        ],
        ["ruff", "format", "--stdin-filename", "stub.pyi", "-"],
    ):
        try:
            done = subprocess.run(
                argv, input=text, capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            return text
        text = done.stdout or text
    return text


@tasks.task(name="list")
def list_(
    show: Annotated[
        Literal["all", "installed", "missing"],
        doc("which tools to list (default: all, present or not)"),
    ] = "all",
):
    """The curated tools: version, in-process capability, stub state.

    Every curated tool is listed by default, absent ones included — the
    version column says `not installed`. `--show installed` narrows to what
    this machine can actually run, `--show missing` to what it can't.
    """
    on = wants_color(sys.stdout)
    rows: list[tuple[str, str, str, str]] = []
    for driver in _drivers.DRIVERS:
        here = _drivers.installed(driver)
        if show == "missing" and here:
            continue
        if show == "installed" and not here:
            continue
        version = _drivers.version(driver.name) if here else ""
        capable = _drivers.in_process_capable(driver.name) if here else False
        mode = "in-process" if driver.in_process else ("capable" if capable else "—")
        stub = "yes" if _stub_path(driver.key).exists() else "no"
        rows.append((driver.key, version or "not installed", mode, stub))
    width = max((len(r[0]) for r in rows), default=4)
    print(bold(f"{'tool'.ljust(width)}  version      in-process  stub", on))
    for key, version, mode, stub in rows:
        print(f"{key.ljust(width)}  {version:<12} {mode:<11} {stub}")


@tasks.task
def spec(
    name: Annotated[str, doc("a curated tool: ruff, uv, mkdocs, …")],
    verb: Annotated[str, doc("one verb, dotted for nesting (compose.up)")] = "",
):
    """Print what a tool says about itself, as footman reads it."""
    driver = _drivers.find(name)
    if driver is None:
        raise SystemExit(f"no driver for {name!r}; try `fm tools.list`")
    if not _drivers.installed(driver):
        raise SystemExit(f"{driver.name} is not installed")
    on = wants_color(sys.stdout)
    extracted = _drivers.extract(driver)
    print(bold(f"{extracted.name} {extracted.version}", on), extracted.help)
    for one in extracted.verbs:
        if verb and one.name != verb:
            continue
        label = one.name or "(the tool itself)"
        print(cyan(f"\n  {label}", on), f"— {len(one.options)} options")
        for option in one.options:
            negation = f"  off → {option.negation}" if option.negation else ""
            print(f"    {option.name:<28} {option.type_name:<10}{negation}")


def _from_prefix(binary: str, root: Path) -> bool:
    """Whether *binary* was reached through the provisioned prefix.

    The launcher in `<prefix>/bin` is what counts, not where it points: the
    node tier's scripts live in a shared `node_modules`, and a provisioned
    interpreter lives in uv's own store, so following the symlink would call
    two properly provisioned tools missing.
    """
    path = Path(binary)
    return path.parent == root / "bin" or path.resolve().is_relative_to(root)


def _ignore(driver: _drivers.Driver, root: Path | None) -> str:
    """Why this tool is left alone, or `""` to read it.

    Two ways a reading is worth less than the snapshot already checked in,
    and in both the honest move is to change nothing:

    * **not in the prefix** — a provisioned tool that failed to fetch (or a
      tier that was skipped) would otherwise fall through to whatever the
      host has, quietly turning a partial provision into "the tools moved".
      Only the `system` tier is *meant* to come from the host.
    * **older than the snapshot** — a host-read tool (git, docker) on a
      machine behind the one that took the snapshot. Reading it would
      rewrite the stub *backwards*, losing flags that exist upstream.
    """
    binary = _drivers._resolve(driver.name)
    if binary is None:
        return "not installed"
    if (
        root is not None
        and driver.provision.kind != "system"
        and not _from_prefix(binary, root)
    ):
        return "not in the prefix"
    stub = _stub_path(driver.key)
    if not stub.exists():
        return ""
    recorded = _header(stub)[0].partition(" ")[0]
    found = _drivers.version(driver.name)
    # One comparator, shared with the bridge: only the leading numeric run
    # counts, so a build tail can never read as "newer than its own base".
    here, snapshot = _version_tuple(found), _version_tuple(recorded)
    if here and snapshot > here:
        return f"older than the snapshot ({found} < {recorded})"
    return ""


def _prefix_root(prefix: str) -> Path | None:
    """The provisioned tree a reading must come from, or None for "anywhere"."""
    return Path(prefix).expanduser().resolve() if prefix else None


@tasks.task
def sync(
    only: Annotated[str, doc("regenerate just this tool")] = "",
    prefix: Annotated[str, doc("read binaries from this prefix's bin/")] = "",
):
    """Rewrite the stubs from the tools installed on this machine.

    A stub is a *snapshot*: what one tool accepted at one version, on one
    machine. Point `--prefix` at a `fm tools.provision` directory to take
    that snapshot from the isolated latest set instead of whatever this
    machine has — a dev environment's pytest carries its plugins' flags
    too, and those do not belong in a stub whose driver never asked for
    them.

    A tool that isn't installed keeps the stub that is checked in — there
    is nothing to read it from, and a stub that exists beats one that was
    deleted because a laptop happened to be missing a binary.
    """
    with _on_path(prefix):
        _sync(only, _prefix_root(prefix))


def _sync(only: str, root: Path | None = None) -> None:
    _STUBS.mkdir(exist_ok=True)
    wrote, skipped = [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # hand-written stub — never extracted or overwritten
        if reason := _ignore(driver, root):
            # A snapshot only ever moves forward: a reading worth less than
            # the checked-in one leaves the stub exactly as it is.
            skipped.append(f"{driver.key} ({reason})")
            continue
        try:
            text = _generate(driver)
        except _Ambiguous as ambiguous:
            skipped.append(f"{driver.key} ({ambiguous.reading} vs {ambiguous.base})")
            continue
        path = _stub_path(driver.key)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            wrote.append(driver.key)
    print(f"wrote {len(wrote)} stub(s): {', '.join(wrote) or 'none changed'}")
    if skipped:
        print(f"left alone: {', '.join(skipped)}")


@tasks.task
def audit(
    only: Annotated[str, doc("check just this tool")] = "",
    fix: Annotated[bool, doc("take a fresh snapshot instead of reporting")] = False,
    prefix: Annotated[str, doc("read binaries from this prefix's bin/")] = "",
    strict: Annotated[bool, doc("exit non-zero when a snapshot is behind")] = False,
):
    """Report which tools have moved on since their stub snapshot.

    A stub records what one tool accepted at the version it was read from.
    Tools keep releasing, and footman promises no particular speed at
    following them — so a tool showing up here means a newer version exists,
    **not** that anything is wrong. Every stubbed verb ends in
    `**flags: Any`, so the bridge already speaks a flag the stub has never
    heard of; only the *hint* is behind. `--fix` takes a fresh snapshot,
    `--strict` gives automation something to trip on, and `--prefix` asks
    the question against a provisioned latest set rather than this machine.

    A snapshot only ever moves **forward**, so two readings are worth less
    than the file already checked in and are named and left alone: a tool
    missing from `--prefix` (a partial provision must not read as drift,
    and the host's copy is not the answer), and one whose version is older
    than the stub records (a machine behind the one that took the snapshot
    has nothing to add). Neither counts as behind — they are unanswered.

    One finding here *is* a fault, and always exits non-zero: footman's
    negation and wrapper tables are read by the runtime, so a disagreement
    there means a task emits the wrong command today.
    """
    with _on_path(prefix):
        return _audit(only, fix, strict, _prefix_root(prefix))


def _audit(
    only: str, fix: bool, strict: bool, root: Path | None = None
) -> dict[str, object]:
    from footman import tools as _bridge

    stale, skipped, wrong, checked = [], [], [], 0
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # hand-written stub — nothing to compare against
        if reason := _ignore(driver, root):
            # Nothing to say about a tool this machine can't read *better*
            # than the snapshot already did — it is not behind, it is
            # unanswered, and the difference matters to a release job.
            skipped.append(f"{driver.key} ({reason})")
            continue
        path = _stub_path(driver.key)
        spec = _drivers.extract(driver)
        fresh = _formatted(_render(driver, spec))
        checked += 1
        if not path.exists() or path.read_text(encoding="utf-8") != fresh:
            stale.append(driver.key)
            if fix:
                path.write_text(fresh, encoding="utf-8")
        # Two extracted facts the *runtime* reads: the negation table `off`
        # consults, and the wrapper set that decides flag ordering. Both
        # must match the installed tool, or a task emits the wrong command.
        if driver.base:
            continue
        found = spec.negations()
        if found != _bridge._NEGATIONS.get(driver.name, {}):
            wrong.append(f"_NEGATIONS[{driver.name!r}] should be {found}")
        wraps = spec.wrappers()
        if wraps != _bridge._WRAPPERS.get(driver.name, frozenset()):
            wrong.append(f"_WRAPPERS[{driver.name!r}] should be {set(wraps)}")
    if skipped:
        print(f"left alone: {', '.join(skipped)}")
    report: dict[str, object] = {
        "checked": checked,
        "behind": stale,
        "skipped": skipped,
        "resnapshotted": bool(fix and stale),
    }
    if wrong:
        # Not news: these two tables are what the *runtime* reads, so a
        # disagreement means the wrong command goes out today.
        raise SystemExit(
            "tools.py runtime tables disagree with the installed tool(s):\n  "
            + "\n  ".join(wrong)
        )
    if not stale:
        print(f"{checked} stub(s) match the tools they were read from")
        return report
    if fix:
        print(f"took a fresh snapshot of {len(stale)}: {', '.join(stale)}")
        return report
    print(
        f"{len(stale)} tool(s) have released a newer version than the stub "
        f"snapshot: {', '.join(stale)}\n"
        f"nothing is broken — the bridge speaks flags the stub hasn't heard "
        f"of. Take a fresh snapshot with `fm tools.sync` when you want one."
    )
    if strict:
        raise SystemExit(2)
    return report


@tasks.task
def color(
    only: Annotated[str, doc("probe just this tool")] = "",
    write: Annotated[bool, doc("regenerate src/footman/_colordata.py")] = True,
    prefix: Annotated[str, doc("probe binaries from this prefix's bin/")] = "",
):
    """Probe how footman forces colour for each installed tool, and regenerate
    the colour data.

    footman spawns over pipes (no PTY), so it forces colour into the tools it
    spawns — by the environment (`FORCE_COLOR`/`NO_COLOR`) for the modern set, by
    the tool's own switch for the few that ignore it. Which is which is *probed*,
    not assumed: each tool is run with colour forced on and off, and the bytes
    read, so a direction is recorded `env`, `flag` (like git's
    `-c color.ui=always`), `none`, or `unprobed` (no trigger figured out).

    Writes `src/footman/_colordata.py`, which `tools.py` reads for its forcing
    table and the docs read for the support table. Point `--prefix` at a
    `fm tools.provision` directory to probe the complete, latest set
    rather than whatever happens to be on PATH.
    """
    with _on_path(prefix):
        _color_probe_and_write(only, write, wants_color(sys.stdout))


def _color_probe_and_write(only: str, write: bool, on: bool) -> None:
    from footman import _colorprobe

    installed: list[tuple[str, str, str, _toolspec.ToolSpec]] = []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual" or not _drivers.installed(driver):
            continue
        binary = _drivers._resolve(driver.name)
        if binary is None:
            continue
        # Only a triggered, non-curated tool needs its stub read for a `--color`
        # candidate; a curated tool (git) and an untriggered one (→ `unprobed`)
        # skip the sometimes-slow extraction.
        needs_spec = (
            driver.key in _colorprobe.TRIGGERS
            and driver.key not in _colorprobe._CURATED
        )
        spec: _toolspec.ToolSpec = (
            _drivers.extract(driver)
            if needs_spec
            else _toolspec.ToolSpec(name=driver.name)
        )
        installed.append((driver.key, driver.name, binary, spec))

    results = _colorprobe.probe_all(installed)
    width = max((len(k) for k in results), default=4)
    print(bold(f"{'tool'.ljust(width)}  {'on':<8}  {'off':<8}  switch", on))
    for key in sorted(results):
        _argv0, verdict = results[key]
        switch = " ".join(verdict.flag.on) if verdict.flag else ""
        print(f"{key.ljust(width)}  {verdict.on:<8}  {verdict.off:<8}  {switch}")

    if write and not only:
        data = Path(__file__).resolve().parent.parent / "_colordata.py"
        data.write_text(_formatted(_colorprobe.render(results)), encoding="utf-8")
        docs = Path(__file__).resolve().parents[3] / "docs" / "color-support.md"
        docs.write_text(_color_docs_table(results), encoding="utf-8")
        print(f"\nwrote {data.name} + {docs.name} ({len(results)} tools)")


# How each probed verdict reads in the docs support table.
_ON_WORD = {"env": "environment", "none": "— *(no colour over a pipe)*", "n/a": ""}
_OFF_WORD = {"env": "environment", "none": "**can't silence**", "n/a": "—"}


def _color_docs_table(results: dict) -> str:
    """A Markdown support table from the probe results — generated into the docs,
    never hand-maintained. `on`/`off` columns read the verdict for each tool;
    the forced switch is shown where a direction needs one."""
    lines = [
        "<!-- Generated by `fm tools.color` — do not edit by hand. -->",
        "",
        "| Tool | Colour on | Colour off |",
        "| ---- | --------- | ---------- |",
    ]
    for key in sorted(results):
        _argv0, v = results[key]
        if v.on == "n/a" and v.off == "n/a":
            lines.append(f"| `{key}` | *(pass-through wrapper)* | |")
            continue
        on = f"`{' '.join(v.flag.on)}`" if v.on == "flag" and v.flag else _ON_WORD[v.on]
        off = (
            f"`{' '.join(v.flag.off)}`"
            if v.off == "flag" and v.flag and v.flag.off
            else _OFF_WORD[v.off]
        )
        lines.append(f"| `{key}` | {on} | {off} |")
    return "\n".join(lines) + "\n"


@tasks.task
def prime(
    only: Annotated[str, doc("prime just this tool")] = "",
    count: Annotated[int, doc("how many releases back to read")] = 20,
    keep: Annotated[bool, doc("leave the throwaway environments behind")] = False,
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
):
    """Read past releases into the option history, deepening each chain.

    Reaches below each tool's floor, up to `--count` releases further back.
    Nothing already written is touched, and a release the chain already has
    is skipped — so a prime interrupted by a rate limit is resumed by
    running it again.

    The releases are gathered **in parallel**, a bounded wave at a time —
    installing a release and reading its `--help` depends on no other
    release, and the chain assembles whatever order the observations arrive
    in. A release that will not install, or whose binary will not describe
    itself, is a **hole**: named in the report, filled by a later run, and
    never the end of the tool's walk.

    `--prefix` points at a `fm tools.provision` directory, and the tiers are
    driven from *its* binaries. That is not the same nicety it is on `sync`:
    uv carries CPython's download index inside itself, so a stale uv reports
    a stale newest python and the walk silently starts too low.
    """
    import shutil
    import tempfile

    from footman import _toolfetch

    _bounce_bare_call("prime")
    scratch = Path(tempfile.mkdtemp(prefix="footman-prime-"))
    lines: list[str] = []
    try:
        with _on_path(prefix), _sandboxed(scratch):
            drivers, skipped = _curated(only, _toolfetch)
            listings, unreachable = _list_phase(drivers, _toolfetch)
            skipped += [f"{key} ({why})" for key, why in sorted(unreachable.items())]

            plans: dict[str, list] = {}
            docs: dict[str, dict] = {}
            for driver in drivers:
                if driver.key not in listings:
                    continue
                doc_ = _toolhistory.load(_history_path(driver.key))
                if doc_ is None:
                    skipped.append(f"{driver.key} (no history — run `sync` first)")
                    continue
                planned, refused = _plan_prime(doc_, listings[driver.key], count)
                if refused:
                    lines.append(
                        f"{driver.key} +0 (from {doc_['observed_from']}) — {refused}"
                    )
                    continue
                docs[driver.key] = doc_
                plans[driver.key] = planned

            work = [(d, r) for d in drivers if d.key in plans for r in plans[d.key]]
            surfaces = _gather(work, scratch)
            for driver in drivers:
                if driver.key not in plans:
                    continue
                fresh, holes = _assemble(
                    driver, docs[driver.key], plans[driver.key], surfaces
                )
                note = f" — holes: {', '.join(holes)}" if holes else ""
                lines.append(
                    f"{driver.key} +{len(fresh)}"
                    f" (from {docs[driver.key]['observed_from']}){note}"
                )
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    for line in lines:
        print(line)
    if skipped:
        print(f"skipped: {', '.join(skipped)}")


@dataclass(frozen=True)
class Refreshed:
    """What a refresh found — the release decision, as data.

    Returned rather than printed, so `fm --json tools.refresh` hands a
    scheduled job the same answer a person reads.
    """

    read: dict[str, list[str]]
    """Releases newly observed, per tool, oldest first."""
    events: dict[str, list[str]]
    """The subset of those that *changed* the tool's surface. This is the
    release decision and the CHANGELOG line at once."""
    unreachable: dict[str, str]
    """Indexes that would not answer, and why. Not the same as a tool with
    nothing new — see `_toolfetch.Unreachable`."""
    skipped: list[str]
    """Tools with no index to read, or no history to add to."""
    holes: dict[str, list[str]] = field(default_factory=dict)
    """Releases that were listed but could not be observed — an install that
    failed, a binary that would not describe itself. A hole is not an error:
    the chain stays contiguous by construction, a later run fills it via
    `insert`, and until then a change the missing release carried reads as
    arriving at the next release actually read."""
    wrote_changelog: bool = False
    """Whether the events reached `CHANGELOG.md`. False with nothing to say,
    and false when the file has no `[Unreleased]` section to write into —
    which a caller should notice rather than assume the notes got written."""

    @property
    def release(self) -> bool:
        """Whether any tool's surface moved — decision 4, in one line."""
        return any(self.events.values())


@tasks.task
def refresh(
    only: Annotated[str, doc("refresh just this tool")] = "",
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
    changelog: Annotated[bool, doc("write the events into CHANGELOG.md")] = True,
) -> Refreshed:
    """Read every release published since the history was last updated.

    The forward walk, and the one a release job runs. Every release between
    each tool's base and its index's newest is observed — not just the
    newest, so a flag that arrived in 0.16.1 is attributed to 0.16.1 rather
    than to whatever happened to be latest the day the job ran. A release
    that changed nothing records an empty delta, which is a real observation
    and not the same as never having looked.

    Three phases. The listings are fetched concurrently; the releases are
    observed **in parallel**, a bounded wave at a time, each observation a
    task of its own so it owns its environment; the chains are assembled
    single-threaded from whatever arrived, in whatever order it arrived —
    which is what lets a failed install be a reported *hole* rather than the
    end of a tool's walk.

    Nothing new anywhere means nothing to release, and that is the whole
    exit condition. So an index that would not answer is reported and exits
    non-zero rather than counting as "nothing new": a rename or a moved repo
    would otherwise make a tool silently untracked while the job kept
    reporting success.
    """
    import shutil
    import tempfile

    from footman import _toolfetch

    _bounce_bare_call("refresh")
    scratch = Path(tempfile.mkdtemp(prefix="footman-refresh-"))
    read: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}
    holes: dict[str, list[str]] = {}
    try:
        with _on_path(prefix), _sandboxed(scratch):
            drivers, skipped = _curated(only, _toolfetch)
            listings, unreachable = _list_phase(drivers, _toolfetch)

            plans: dict[str, list] = {}
            docs: dict[str, dict] = {}
            for driver in drivers:
                if driver.key not in listings:
                    continue
                doc_ = _toolhistory.load(_history_path(driver.key))
                if doc_ is None:
                    skipped.append(f"{driver.key} (no history — run `sync` first)")
                    continue
                docs[driver.key] = doc_
                plans[driver.key] = _plan_refresh(doc_, listings[driver.key])

            work = [(d, r) for d in drivers if d.key in plans for r in plans[d.key]]
            surfaces = _gather(work, scratch)
            for driver in drivers:
                if driver.key not in plans:
                    continue
                fresh, missing = _assemble(
                    driver, docs[driver.key], plans[driver.key], surfaces
                )
                if fresh:
                    read[driver.key] = fresh
                if missing:
                    holes[driver.key] = missing
                if moved := _events_of(docs[driver.key], fresh):
                    events[driver.key] = moved
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    found = Refreshed(
        read=read,
        events=events,
        unreachable=unreachable,
        skipped=skipped,
        holes=holes,
    )
    if changelog and events:
        entries = [
            _entry_for(key, _toolhistory.load(_history_path(key)) or {}, versions)
            for key, versions in sorted(events.items())
        ]
        found = replace(found, wrote_changelog=_write_changelog(entries))
    _report_refresh(found)
    if unreachable:
        from footman import fail

        fail(
            f"{len(unreachable)} index(es) would not answer: "
            f"{', '.join(sorted(unreachable))}",
            code=75,  # EX_TEMPFAIL: try again, rather than "nothing to do"
        )
    return found


_CHANGELOG = _HISTORY.parent / "CHANGELOG.md"


def _entry_for(key: str, doc: dict, versions: list[str]) -> str:
    """One CHANGELOG bullet for one tool's refresh.

    Per tool rather than per release: a reader cares that prek gained
    `--glob`, not which patch carried it, and a tool that moved three times
    would otherwise take three bullets to say one thing.

    The span runs from the release *before* the earliest change to the
    newest — a release compared against itself is empty by construction, so
    the predecessor is what makes the first change visible.

    Added and dropped options are named, because they are few and they are
    what someone acts on. Rewordings are counted rather than listed: a
    release can reword half a dozen descriptions without changing what the
    tool accepts, and spelling those out would make the entry a diff dump.
    """
    since = _predecessor(doc, versions[0])
    span = _toolhistory.changes(doc, since=since, until=versions[-1])
    newest = versions[-1]
    # Two keys can share a spelling — a flag on the bare command and on one
    # of its verbs — and a reader wants to be told about `--glob` once.
    added = sorted(
        set(_toolhistory.spellings(doc, newest, span.get("drop", ())).values())
    )
    dropped = sorted(
        set(_toolhistory.spellings(doc, since, span.get("add", {})).values())
    )
    # `None` means the newer release added the verb. Anything else is a verb
    # the step back restores or amends, and which of those it is says so in
    # the newer surface rather than in the shape of the payload.
    now = (_toolhistory.at(doc, newest) or {}).get("verbs", {})
    gained, lost, amended = [], [], 0
    for name, moved in span.get("verbs", {}).items():
        if moved is None:
            gained.append(name)
        elif name not in now:
            lost.append(name)
        else:
            amended += 1
    reworded = len(span.get("revert", {})) + amended

    said: list[str] = []
    if added:
        said.append(f"adds {_names(added)}")
    if dropped:
        said.append(f"drops {_names(dropped)}")
    if gained:
        said.append(
            f"gains the {_names(sorted(gained))} {_plural('command', len(gained))}"
        )
    if lost:
        said.append(
            f"withdraws the {_names(sorted(lost))} {_plural('command', len(lost))}"
        )
    if reworded:
        said.append(f"rewords {reworded} {_plural('description', reworded)}")
    if "help" in span:
        said.append("restates its own description")
    if not said:  # pragma: no cover - only versions with events are offered
        said.append("changes its option surface")

    over = "" if len(versions) == 1 else f", over {len(versions)} releases"
    rest = f" It also {_and(said[1:])}." if len(said) > 1 else ""
    return f"- **{key} {newest}** {said[0]}{over}.{rest}"


def _predecessor(doc: dict, version: str) -> str:
    """The observed release just older than *version*, or the oldest there is."""
    chain = _toolhistory.observed(doc)  # newest first
    if version in chain and chain.index(version) + 1 < len(chain):
        return chain[chain.index(version) + 1]
    return chain[-1]


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _names(items: list[str]) -> str:
    """`a`, `a` and `b`, `a`, `b` and `c` — with the flags in code spans."""
    quoted = [f"`{item}`" for item in items]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _and(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return clauses[0]
    return f"{', '.join(clauses[:-1])} and {clauses[-1]}"


def _write_changelog(entries: list[str], path: Path | None = None) -> bool:
    """Put *entries* under `[Unreleased]` → `### Changed`, in place.

    Written rather than printed because the refresh already edits
    `tool-history/` and the stubs and has to land through a PR either way —
    a scheduled job that emitted release notes to stdout would be producing
    them for nobody. `### Changed` because a tool gaining a flag changes
    footman's *stub*; footman itself added nothing.
    """
    path = path or _CHANGELOG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.split("\n")
    try:
        start = next(
            i for i, line in enumerate(lines) if line.startswith("## [Unreleased]")
        )
    except StopIteration:
        return False
    # The next release heading bounds the section; the file may hold only one.
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    changed = next(
        (
            i
            for i, line in enumerate(lines[start:end], start)
            if line.strip() == "### Changed"
        ),
        -1,
    )
    if changed == -1:
        # Keep a Changelog's order, so a new section lands where a reader
        # expects it rather than at whichever end is easiest to append to.
        after = next(
            (
                i
                for i, line in enumerate(lines[start:end], start)
                if line.strip()
                in ("### Deprecated", "### Removed", "### Fixed", "### Security")
            ),
            end,
        )
        lines[after:after] = ["### Changed", "", *entries, ""]
    else:
        lines[changed + 2 : changed + 2] = entries
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def _report_refresh(found: Refreshed) -> None:
    """The human-readable half of what `Refreshed` carries."""
    for key, versions in found.read.items():
        moved = found.events.get(key, [])
        note = f" — events in {', '.join(moved)}" if moved else " — no change"
        print(f"{key} +{len(versions)} ({', '.join(versions)}){note}")
    if not found.read:
        print("nothing new")
    print(f"release warranted: {'yes' if found.release else 'no'}")
    for key, missing in sorted(found.holes.items()):
        print(f"holes in {key}: {', '.join(missing)} — a later run fills them")
    for key, why in sorted(found.unreachable.items()):
        print(f"could not read {key}: {why}")
    if found.skipped:
        print(f"skipped: {', '.join(found.skipped)}")


def _bounce_bare_call(task: str) -> None:
    """Refuse to gather outside a run, with directions rather than a race.

    Every isolation property the parallel gather leans on — the environ
    router, the per-call env copy at the task boundary, the subprocess env
    injection — belongs to a run. Called bare, all three are absent: the
    observations would share the one real environment across threads, which
    is exactly the cross-contamination this engine exists to remove. One
    implementation, and a bouncer — never a degraded twin.
    """
    from footman import _globals, fail

    if not _globals.active():
        fail(
            f"tools.{task} gathers releases in parallel and needs a run — "
            f"invoke `fm tools.{task}`, or drive it with "
            "footman.testing.Runner in tests"
        )


def _curated(only: str, fetch) -> tuple[list, list[str]]:
    """The drivers a walk can work on, and the ones it names as skipped."""
    chosen, skipped = [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if not fetch.can_list(driver):
            skipped.append(f"{driver.key} ({driver.provision.kind} tier)")
            continue
        chosen.append(driver)
    return chosen, skipped


def _list_phase(drivers: list, fetch) -> tuple[dict[str, list], dict[str, str]]:
    """Every tool's release listing, fetched concurrently.

    Network-bound and environment-free, so plain thunks are enough.
    `Unreachable` is collected per tool rather than aborting the sweep — the
    tools that could be read still deserve their walk, and the caller
    decides what an unreadable index costs.
    """
    from footman import parallel

    listings: dict[str, list] = {}
    unreachable: dict[str, str] = {}
    lock = threading.Lock()

    def look(driver):
        def call():
            try:
                found = fetch.releases(driver)
            except fetch.Unreachable as blocked:
                with lock:
                    unreachable[driver.key] = str(blocked)
                return
            with lock:
                listings[driver.key] = found

        call.__name__ = f"list:{driver.key}"
        return call

    calls = [look(driver) for driver in drivers]
    if calls:
        parallel(*calls, keep_going=True)
    return listings, unreachable


def _plan_refresh(doc: dict, listing: list) -> list:
    """Every listed release the chain does not hold, down to its floor.

    Not just the ones above the base: an interior gap — a hole a previous
    gather reported — is this walk's to fill too, or nobody fills it. Below
    the floor stays `prime`'s business, because depth is a budget and a
    refresh must not silently spend it.
    """
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
    return [
        release
        for release in listing
        if release.version not in known
        and _version_tuple(release.version) >= _version_tuple(floor)
    ]


def _plan_prime(doc: dict, listing: list, count: int) -> tuple[list, str]:
    """Up to *count* releases below the floor — the backward walk's work.

    The floor is positioned in the *listing*, never compared by date: a base
    carries the date it was observed, so on a first prime a date test admits
    every release ever published. A floor the listing cannot place refuses
    the tool with directions rather than guessing where it belongs.
    """
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
    below = [index for index, release in enumerate(listing) if release.version == floor]
    if listing and not below:
        return [], f"{floor} is not among the listed releases (sync it forward first)"
    start = below[0] + 1 if below else 0
    return [release for release in listing[start:] if release.version not in known][
        :count
    ], ""


@tasks.task(hidden=True)
def observe(
    tool: str,
    version: str,
    tag: str = "",
    date: str = "",
    scratch: str = "",
) -> dict | None:
    """Install one release, read what it accepts, and throw it away.

    The unit of the gather, pure in (tool, version): requests for the same
    release dedupe on the futures work key, and arrival order is nobody's
    business — `_toolhistory.insert` assembles the chain from whatever order
    these finish in.

    A real task deliberately, not a helper. The task boundary is what buys
    each observation its own environment: a body call copies the caller's
    overlay, so the `PATH` written around extraction here is this
    observation's alone, while the sandbox variables and prefix `PATH` the
    caller set flow in — and on into every subprocess the tiers spawn.

    `None` — a release that would not install, or a binary that would not
    describe itself — is a hole for the caller to report, never an error:
    the chain stays contiguous by construction, and a later run fills it.
    """
    from footman import _toolfetch

    driver = _drivers.find(tool)
    if driver is None or not scratch:  # pragma: no cover - engine-supplied
        return None
    release = _toolfetch.Release(version=version, tag=tag, date=date)
    bindir = _toolfetch.install(driver, release, Path(scratch) / f"{tool}-{version}")
    if bindir is None:
        return None
    try:
        with _on_path(bindir.parent):
            spec = _drivers.extract(driver)
    finally:
        _discard(bindir)
    if not spec.verbs:
        return None  # a binary that will not describe itself is no observation
    return _toolhistory.surface_of(spec)


def _gather(work: list, scratch: Path) -> dict[str, dict[str, dict | None]]:
    """Observe every (driver, release) in *work*, a bounded wave at a time.

    Each observation is a body call into `observe` — the task boundary is
    the isolation — and the wave width caps concurrent downloads and peak
    disk in one number: at most that many releases exist on disk at any
    moment. Results land keyed by tool and version, in whatever order the
    pool finishes; an observation that crashes outright simply never
    reports, which reads as a hole exactly like a release that would not
    install, with the traceback in the wave's output.
    """
    from footman import parallel
    from footman.context import current

    surfaces: dict[str, dict[str, dict | None]] = {}
    lock = threading.Lock()

    def observing(driver, release):
        def call():
            surface = observe(
                tool=driver.key,
                version=release.version,
                tag=release.tag,
                date=release.date,
                scratch=str(scratch),
            )
            with lock:
                surfaces.setdefault(driver.key, {})[release.version] = surface

        call.__name__ = f"{driver.key}=={release.version}"
        return call

    calls = [observing(driver, release) for driver, release in work]
    width = current().jobs or 8
    for start in range(0, len(calls), width):
        parallel(*calls[start : start + width], keep_going=True)
    return surfaces


def _assemble(
    driver, doc: dict, planned: list, surfaces: dict
) -> tuple[list[str], list[str]]:
    """Insert whatever the gather brought home; say what is missing.

    Single-threaded on purpose: the arithmetic is microseconds against the
    installs, the doc is mutated in place, and one writer per file means the
    atomic save needs no coordination. Returns the fresh releases oldest
    first — the order a reader tells the story in — and the holes.
    """
    observed_here = surfaces.get(driver.key, {})
    fresh: list[str] = []
    holes: list[str] = []
    for release in planned:
        surface = observed_here.get(release.version)
        if surface is None:
            holes.append(release.version)
            continue
        if _toolhistory.insert(
            doc,
            version=release.version,
            date=release.date,
            surface=surface,
            platforms=[_platform()],
        ):
            fresh.append(release.version)
    if fresh:
        chain = _toolhistory.observed(doc)  # newest first
        fresh.sort(key=chain.index, reverse=True)  # oldest first
        _toolhistory.save(doc, _history_path(driver.key))
        # The stub is a rendering of the record, so it follows the record
        # rather than waiting for someone to remember a `sync`.
        _stub_path(driver.key).write_text(_stub_from(driver, doc), encoding="utf-8")
    return fresh, holes


def _events_of(doc: dict, fresh: list[str]) -> list[str]:
    """Which of *fresh* changed the tool's surface — the release decision.

    Answered from the assembled chain rather than remembered from arrival
    order: a release's own changes live in the delta keyed by its
    predecessor, the step back *from* it. A hole just below a release makes
    that delta span the gap, so the change is attributed to the release
    actually read — the chain's standing imprecision, reported as the hole.
    """
    chain = _toolhistory.observed(doc)  # newest first
    changed: list[str] = []
    for version in fresh:
        spot = chain.index(version)
        if spot + 1 >= len(chain):
            continue  # the floor: nothing below to have changed from
        step = doc["deltas"][chain[spot + 1]]
        if any(key not in ("date", "platforms", "extractor") for key in step):
            changed.append(version)
    return changed


def _discard(bindir: Path) -> None:
    """Delete one release once its surface has been read.

    The walk needs the surface, not the binary, and the surface is in hand by
    the time this is called. Without it a prime holds every release it has
    ever fetched until the run ends — ruff alone would stand up 416
    environments at once — so this is the difference between peak disk being
    one release and being all of them.

    Safe only because `_sandboxed` has put uv's interpreter store inside the
    scratch directory: *bindir*`.parent` is that release's own directory in
    every tier, and for the python tier that would otherwise be an
    interpreter this machine actually uses.
    """
    import shutil

    shutil.rmtree(bindir.parent, ignore_errors=True)


@tasks.task
def provision(
    only: Annotated[str, doc("provision just this tool")] = "",
    prefix: Annotated[Path, doc("directory to materialise the binaries into")] = Path(
        ".tools-latest"
    ),
    sync_: Annotated[
        bool, doc("run `tools sync` against the prefix afterwards")
    ] = False,
    clean: Annotated[bool, doc("remove the prefix when done")] = False,
):
    """Fetch the latest curated tools into an isolated prefix — no pollution.

    The stubs are read from installed binaries, so syncing against the newest
    release means having it on PATH. This gathers the latest of every curated
    tool under one throwaway prefix — `uv tool install` for the PyPI wheels
    (the Rust and C++ tools included), bun's own release then `bun add` for the
    node CLIs, a release asset for the Go ones — touching nothing outside it.
    `--sync` then rewrites the stubs against that prefix; `--clean` deletes it.
    Deleting the prefix is the whole undo.
    """
    from footman import _provision

    # Absolute: bun errors `ReadOnlyFileSystem` on a relative BUN_INSTALL, and
    # an absolute prefix keeps every tier's launchers and env vars unambiguous.
    prefix = Path(prefix).expanduser().resolve()
    outcomes = _provision.provision(_drivers.DRIVERS, prefix, only=only)
    _print_outcomes(outcomes)
    if sync_:
        _sync_against(prefix, only)
    else:
        print(
            f'\nput them on PATH:\n  export PATH="{_provision.bin_dir(prefix)}:$PATH"'
        )
    if clean:
        import shutil

        shutil.rmtree(prefix, ignore_errors=True)
        print(f"removed {prefix}")


_MARK = {"ok": "ok", "fail": "FAIL", "skip": "—", "deferred": "parked"}


def _print_outcomes(outcomes: list) -> None:
    """The provisioning result, one aligned line per tool."""
    width = max((len(o.key) for o in outcomes), default=4)
    for out in outcomes:
        mark = _MARK.get(out.status, out.status)
        print(f"{mark:<6} {out.key.ljust(width)}  {out.kind:<8} {out.detail}")


def _sync_against(prefix: Path, only: str) -> None:
    """Run `sync` with the prefix on PATH, so it reads the fresh binaries."""
    sync(only=only, prefix=str(prefix))


_READ_FROM = _re.compile(
    r"Read from (?P<tool>\S+) (?P<version>\S+) on (?P<platform>\w+)\."
    r"(?: In-process: (?P<mode>\w+)\.)?"
)

_INDEX = """\
# Tools

Import a tool by name — `from footman.tools import git` — and call it,
`git.commit(…)`. No declaration needed: [the bridge](../../tools-bridge.md)
translates keyword arguments into flags mechanically, and every tool on
your PATH already works. These pages document the **stubs**: what each
curated tool accepted at the version footman last read it from, with that
tool's own help text per flag.

Nothing here is a wrapper. The stubs are generated by `fm tools.sync`,
which asks the installed binaries what they take, and `fm tools.audit`
reports which tools have released a newer version since. A flag missing
from a stub still runs — every verb ends in `**flags: Any`, so a stub can
suggest but never forbid.

Where a flag defaults *on*, its documentation names the spelling that
turns it off, because that is the one thing the bridge cannot infer:
`clean=off` emits `mkdocs build --dirty`, not `--no-clean`.

The **In-process** column is a deliberate choice, not a capability dump.
Tasks run concurrently as threads, and a tool call is normally a subprocess —
isolated, trivially parallel. A Python tool with a `[console_scripts]` entry
point *can* run in footman's own process instead, skipping the spawn:

- **default** — footman prefers in-process. `mkdocs` (macOS strips `DYLD_*`
  from subprocesses, so cairo only resolves in-process), `zensical` and
  `coverage` (pure Python) qualify, and their entry points accept an argument
  list, so they stay parallel.
- **available** — an entry point exists but running it in-process buys
  nothing: `basedpyright` ships a Python launcher that just spawns node, so
  footman subprocesses it anyway.
- **no** — a Rust/Go/Node binary with no Python entry point; always a
  subprocess.

See [the tools bridge](../../tools-bridge.md#parallelism) for how in-process
tools stay parallel (and the one case that can't).

{table}
"""


def _header(path: Path) -> tuple[str, str]:
    """`(read from, in-process)` as a checked-in stub records them.

    The table is built from the files rather than from the tools, so
    building the docs needs nothing on PATH and the page says exactly what
    ships — including for the tools this machine cannot ask.
    """
    head = path.read_text(encoding="utf-8")[:600].replace("\n# ", " ")
    match = _READ_FROM.search(head)
    if not match:
        # A hand-written stub exists precisely because the tool is not a
        # Python package to extract from (the shells, cmd): there is no
        # entry point to call, so in-process is structurally "no" — not
        # unknown.
        return "hand-written", "no"
    return f"{match['version']} ({match['platform']})", match["mode"] or "unknown"


def _verb_tree(path: Path) -> dict[str, object]:
    """A stub's verbs, nested the way its classes are.

    A subcommand group is a nested class holding an attribute of that type
    (`class Compose` + `compose: Compose`), so the attribute name is the verb
    and the class is what hangs under it.
    """
    import ast

    def walk(node: ast.ClassDef) -> dict[str, object]:
        classes = {
            item.name: item for item in node.body if isinstance(item, ast.ClassDef)
        }
        out: dict[str, object] = {}
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                # `flags` is footman's own typed-globals accessor, written into
                # every subcommand class by the generator — not a verb of the
                # tool, and listing it once per group buries the real ones.
                if item.name != "flags":
                    out[item.name] = None
            elif (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and isinstance(item.annotation, ast.Name)
                and item.annotation.id in classes
            ):
                out[item.target.id] = walk(classes[item.annotation.id])
        return out

    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    return walk(roots[0]) if roots else {}


def _verbs_of(path: Path) -> list[str]:
    """The verbs a stub declares, dotted, for the index table.

    Dotted because that is how they are called: `compose.up`, `pip.install`,
    `tool.install`. Flattened to bare names they read as `up` and collide —
    uv's two `install` verbs are not one verb.
    """
    found: list[str] = []

    def walk(node: dict[str, object], prefix: str) -> None:
        for name, child in node.items():
            if isinstance(child, dict):
                walk(child, f"{prefix}{name}.")
            else:
                found.append(f"{prefix}{name}")

    walk(_verb_tree(path), "")
    return sorted(found)


@tasks.task
def pages(
    out: Annotated[Path, doc("directory to write the reference pages into")],
    nav: Annotated[
        Path | None, doc("a config whose Tools nav block to rewrite")
    ] = None,
):
    """Write one reference page per tool, plus the index table.

    Built from the checked-in stubs rather than from the installed tools, so
    the docs build needs nothing on PATH and says exactly what ships. Tools
    are ordered alphabetically. With *nav*, the tool entries of that config's
    Tools list are regenerated too (between markers), so the sidebar can never
    fall behind the drivers again.
    """
    out.mkdir(parents=True, exist_ok=True)
    stubbed = sorted(
        (d for d in _drivers.DRIVERS if _stub_path(d.key).exists()),
        key=lambda d: d.key,
    )
    rows = ["| Tool | Read from | In-process | Verbs |", "| --- | --- | --- | --- |"]
    for driver in stubbed:
        rows.append(_row(driver, _stub_path(driver.key)))
        (out / f"{driver.key}.md").write_text(_page(driver), encoding="utf-8")
    (out / "index.md").write_text(
        _INDEX.format(table="\n".join(rows)), encoding="utf-8"
    )
    if nav is not None:
        write_tools_nav(nav, [d.key for d in stubbed])
    print(f"wrote {len(stubbed)} tool page(s) into {out}")


# The tool entries of the docs nav are regenerated between these markers, so a
# new driver never needs a hand-edit — `nav_keys` reads them back for the test
# that fails when the sidebar falls behind `DRIVERS`.
_NAV_BEGIN = "    # tools-nav:begin (generated by `fm tools.pages`)"
_NAV_END = "    # tools-nav:end"
_NAV_RE = _re.compile(
    _re.escape(_NAV_BEGIN) + r".*?" + _re.escape(_NAV_END), _re.DOTALL
)
_NAV_ENTRY = _re.compile(r'\{\s*"(?P<key>[^"]+)"\s*=\s*"_generated/tools/')


def write_tools_nav(config: Path, keys: list[str]) -> None:
    """Rewrite a zensical/mkdocs Tools nav's tool entries from *keys*."""
    entries = [f'    {{ "{k}" = "_generated/tools/{k}.md" }},' for k in keys]
    block = "\n".join([_NAV_BEGIN, *entries, _NAV_END])
    config.write_text(
        _NAV_RE.sub(lambda _m: block, config.read_text(encoding="utf-8")),
        encoding="utf-8",
    )


def nav_keys(config: Path) -> list[str]:
    """The tool keys the config's generated Tools-nav block lists, in order."""
    match = _NAV_RE.search(config.read_text(encoding="utf-8"))
    return [m["key"] for m in _NAV_ENTRY.finditer(match.group())] if match else []


def _row(driver: _drivers.Driver, path: Path) -> str:
    """One line of the index table: what it is, and what it was read from."""
    verbs = _verbs_of(path)
    listed = ", ".join(f"`{v}`" for v in verbs[:5]) or "the tool itself"
    if len(verbs) > 5:
        listed += f", … ({len(verbs)} in all)"
    version, mode = _header(path)
    home = f" ([docs]({driver.url}))" if driver.url else ""
    return (
        f"| [`{driver.key}`]({driver.key}.md){home} | {version} | {mode} | {listed} |"
    )


def _page(driver: _drivers.Driver) -> str:
    """One tool's reference page — mkdocstrings renders it from the stub.

    One directive is enough: a subcommand group is a *nested* class, which is
    a member, and the renderer walks members. `docker compose up` and its
    flags come along without the page having to name `Docker.Compose`.
    """
    home = f"[{driver.name} documentation]({driver.url})\n\n" if driver.url else ""
    return (
        f"# {driver.key}\n\n{home}"
        f"::: footman._stubs.{driver.key}.{_class_name(driver.key)}\n"
    )


__all__ = ["tasks"]
