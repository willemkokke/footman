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
from collections.abc import Iterator
from contextlib import contextmanager
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

    from footman import _globals

    bindir = Path(prefix).expanduser().resolve() / "bin"
    if _globals.active():
        ctx = current()
        saved = ctx.env.get("PATH", os.environ.get("PATH", ""))
        ctx.env["PATH"] = f"{bindir}{os.pathsep}{saved}"
        try:
            yield
        finally:
            ctx.env["PATH"] = saved
        return
    saved = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bindir}{os.pathsep}{saved}"
    try:
        yield
    finally:
        os.environ["PATH"] = saved


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
        previous = doc["base"]
        doc["deltas"] = {
            previous["version"]: {
                "date": previous["date"],
                "extractor": previous["extractor"],
                **_toolhistory.delta(surface, previous["surface"]),
            },
            **doc["deltas"],
        }
        doc["base"] = {
            "version": version,
            "date": _today(),
            "platforms": [_platform()],
            "extractor": _toolhistory.EXTRACTOR,
            "surface": surface,
        }
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
    """Read past releases into the option history, newest first.

    Walks backwards from the release the history already holds, installing
    one version at a time into a throwaway environment and appending a delta
    for each. Nothing already written is touched, and a release the chain
    already has is skipped — so a prime interrupted by a rate limit is
    resumed by running it again.

    `--count` is how far back to reach *this run*; run it again to go
    deeper. The floor a tool actually reached is recorded as
    `observed_from`, which is a fact about what was read rather than a policy
    about what we meant to read: an option present in the oldest release read
    is "at or before" that version, never "since" it.

    A tool footman cannot list is named and skipped rather than left looking
    like a tool with no history.

    `--prefix` points at a `fm tools.provision` directory, and the tiers are
    driven from *its* binaries. That is not the same nicety it is on `sync`:
    uv carries CPython's download index inside itself, so a stale uv reports
    a stale newest python and the walk silently starts too low.
    """
    import shutil
    import tempfile

    from footman import _toolfetch

    scratch = Path(tempfile.mkdtemp(prefix="footman-prime-"))
    read: list[str] = []
    skipped: list[str] = []
    try:
        with _on_path(prefix):
            for driver in _drivers.DRIVERS:
                if only and driver.key != only:
                    continue
                if not _toolfetch.can_list(driver):
                    skipped.append(f"{driver.key} ({driver.provision.kind} tier)")
                    continue
                doc = _toolhistory.load(_history_path(driver.key))
                if doc is None:
                    skipped.append(f"{driver.key} (no history — run `sync` first)")
                    continue
                try:
                    added, stopped = _prime_one(driver, doc, count, scratch, _toolfetch)
                except _toolfetch.Unreachable as unreachable:
                    # Not "nothing left to read" — nobody read anything.
                    skipped.append(f"{driver.key} ({unreachable})")
                    continue
                note = f" — stopped at {stopped}" if stopped else ""
                read.append(
                    f"{driver.key} +{added} (from {doc['observed_from']}){note}"
                )
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    for line in read:
        print(line)
    if skipped:
        print(f"skipped: {', '.join(skipped)}")


def _prime_one(driver, doc: dict, count: int, scratch: Path, fetch) -> tuple[int, str]:
    """Read up to *count* releases older than the chain's floor, oldest last.

    Returns what was added and, when the walk ended early, the release it
    stopped at. A release that will not install, or whose binary will not
    describe itself, ends this tool's walk rather than leaving a hole: the
    chain is contiguous by construction, and `observed_from` would otherwise
    claim a reach the file does not have.

    Reporting *why* it stopped matters more than it looks: a scheduled job
    reading "+0" cannot tell "nothing left to read" from "this machine has no
    bun, so the npm tier fetched nothing".

    What counts as older is the *source's* own ordering, not a date this file
    holds: a base carries the date it was observed, so on a first prime the
    floor is dated today and a date test admits every release ever published.
    That was invisible while every base happened to be the newest release —
    and wrong the moment one is not, which is a stub synced from an outdated
    binary. Positioning the floor in the listing instead compares like with
    like, and a floor the listing cannot place stops the walk rather than
    guessing where it belongs.
    """
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
    listed = fetch.releases(driver)
    below = [index for index, release in enumerate(listed) if release.version == floor]
    if listed and not below:
        return 0, f"{floor} is not among the listed releases (sync it forward first)"
    start = below[0] + 1 if below else 0
    wanted = [release for release in listed[start:] if release.version not in known][
        :count
    ]
    added = 0
    stopped = ""
    for release in wanted:
        bindir = fetch.install(driver, release.version, scratch / release.version)
        if bindir is None:
            stopped = f"{release.version} (could not install)"
            break
        with _on_path(bindir.parent):
            spec = _drivers.extract(driver)
        if not spec.verbs:
            # a binary that will not describe itself is not an observation
            stopped = f"{release.version} (no help to read)"
            break
        if _toolhistory.extend(
            doc,
            version=release.version,
            date=release.date,
            surface=_toolhistory.surface_of(spec),
            platforms=[_platform()],
        ):
            added += 1
    if added:
        _toolhistory.save(doc, _history_path(driver.key))
        # A deeper history changes what the stub may say — an option that
        # looked original at the old floor may now be one that arrived. The
        # stub is a rendering of the record, so it is rewritten here rather
        # than waiting for someone to remember a `sync`.
        _stub_path(driver.key).write_text(_stub_from(driver, doc), encoding="utf-8")
    return added, stopped


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
