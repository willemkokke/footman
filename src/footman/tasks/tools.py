"""Keep the `tools.*` stubs honest — `fm footman tools …`.

The bridge never goes stale, because it transcribes nothing. Its *stub*
can: a stub is a description of a tool at a version, and tools move. These
tasks close that gap by regenerating the description from the installed
tools and by failing a check when the two disagree.

    fm footman tools list          what footman curates, and what's installed
    fm footman tools spec ruff     what one tool says about itself, right now
    fm footman tools sync          rewrite the stubs from the installed tools
    fm footman tools audit         fail if a stub and its tool disagree

`audit` is the one worth running anywhere: it answers "does the version I
have still match what my editor is telling me?" without changing a file.
Tools that aren't installed are skipped and named — a check that quietly
covered three of thirteen would be worse than no check.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from footman import _drivers, _stubgen
from footman._describe import bold, cyan, wants_color
from footman.params import doc
from footman.registry import Group

tasks = Group("tools", help="Keep the tools.* stubs honest")

_STUBS = Path(__file__).resolve().parent.parent / "_stubs"


def _stub_path(key: str) -> Path:
    return _STUBS / f"{key}.pyi"


def _platform() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


def _generate(driver: _drivers.Driver) -> str:
    """The stub text for one installed tool, formatted the way ruff would."""
    spec = _drivers.extract(driver)
    text = _stubgen.render(
        spec, platform=_platform(), class_name=_class_name(driver.key)
    )
    return _formatted(text)


def _class_name(key: str) -> str:
    return "_" + "".join(part.title() for part in key.split("_"))


def _formatted(text: str) -> str:
    """Run the generated text through the formatter that guards the repo.

    Generated code lands in `src/`, where `ruff format --check` runs on
    every commit — so it has to be formatted the same way by construction,
    not by a follow-up nobody remembers.
    """
    import subprocess

    try:
        done = subprocess.run(
            ["ruff", "format", "--stdin-filename", "stub.pyi", "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return text
    return done.stdout or text


@tasks.task(name="list")
def list_(
    missing: Annotated[bool, doc("only the tools this machine lacks")] = False,
):
    """The curated tools: version, in-process capability, stub state."""
    on = wants_color(sys.stdout)
    rows: list[tuple[str, str, str, str]] = []
    for driver in _drivers.DRIVERS:
        here = _drivers.installed(driver)
        if missing and here:
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
        raise SystemExit(f"no driver for {name!r}; try `fm footman tools list`")
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


@tasks.task
def sync(
    only: Annotated[str, doc("regenerate just this tool")] = "",
):
    """Rewrite the stubs from the tools installed on this machine.

    A tool that isn't installed keeps the stub that is checked in — there
    is nothing to read it from, and a stub that exists beats one that was
    deleted because a laptop happened to be missing a binary.
    """
    _STUBS.mkdir(exist_ok=True)
    wrote, skipped = [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if not _drivers.installed(driver):
            skipped.append(driver.key)
            continue
        text = _generate(driver)
        path = _stub_path(driver.key)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            wrote.append(driver.key)
    print(f"wrote {len(wrote)} stub(s): {', '.join(wrote) or 'none changed'}")
    if skipped:
        print(f"skipped (not installed): {', '.join(skipped)}")


@tasks.task
def audit(
    only: Annotated[str, doc("check just this tool")] = "",
    fix: Annotated[bool, doc("write the differences instead of reporting")] = False,
):
    """Fail when a checked-in stub and its installed tool disagree.

    Drift here is not a broken build — every stubbed verb ends in
    `**flags: Any`, so the bridge still runs whatever you pass. It is a
    stale *hint*, and this is how it gets noticed.
    """
    from footman import tools as _bridge

    stale, skipped, wrong, checked = [], [], [], 0
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if not _drivers.installed(driver):
            skipped.append(driver.key)
            continue
        path = _stub_path(driver.key)
        spec = _drivers.extract(driver)
        fresh = _formatted(
            _stubgen.render(
                spec, platform=_platform(), class_name=_class_name(driver.key)
            )
        )
        checked += 1
        if not path.exists() or path.read_text(encoding="utf-8") != fresh:
            stale.append(driver.key)
            if fix:
                path.write_text(fresh, encoding="utf-8")
        # The negation table is the one extracted fact the *runtime* reads:
        # `off` consults it to spell a flag the convention gets wrong.
        found = spec.negations()
        if not driver.base and found != _bridge._NEGATIONS.get(driver.name, {}):
            wrong.append(f"{driver.name}: {found}")
    if skipped:
        print(f"skipped (not installed): {', '.join(skipped)}")
    if wrong:
        raise SystemExit(
            "tools._NEGATIONS disagrees with the installed tool(s):\n  "
            + "\n  ".join(wrong)
        )
    if not stale:
        print(f"{checked} stub(s) match their installed tool")
        return
    if fix:
        print(f"updated {len(stale)} stub(s): {', '.join(stale)}")
        return
    raise SystemExit(
        f"{len(stale)} stub(s) differ from the installed tool: "
        f"{', '.join(stale)}\nrun `fm footman tools sync` to update"
    )


__all__ = ["tasks"]
