"""footman's own tasks — dogfooding the runner and its run()/tools helpers.

Run with ``fm <task>`` (or ``uv run fm <task>`` before it is installed).
Chaining works: ``fm format lint --fix test``.
"""

from __future__ import annotations

import functools
from typing import Annotated

from footman import doc, group, parallel, run, task, tools

SRC = ("src", "tests")


@task
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False):
    """Lint with ruff."""
    tools.ruff.check(*SRC, fix=fix)


@task
def format(check: bool = False):
    """Format with ruff.

    Args:
        check: report instead of rewriting
    """
    tools.ruff_format(*SRC, check=check)


@task
def typecheck():
    """Type-check with basedpyright."""
    tools.basedpyright()


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    tools.pytest(*pytest_args, in_process=False)


@task
def check():
    """Run format --check, lint, typecheck, and test — in parallel.

    The gate: run it before every commit, and CI runs exactly this.
    Coverage is not enforced here — that's the explicit
    `pytest --cov=footman` invocation documented in CLAUDE.md.
    """
    # partial, not a lambda: it keeps the callee's name, so the live line
    # and step column say "format" instead of "…".
    parallel(functools.partial(format, check=True), lint, typecheck, test)


docs = group("docs", help="Documentation site (Zensical)")


def _scaffold_suggest_demo() -> str:
    """A scratch project whose tasks.py is typing.md's dynamic-completion
    example, extracted verbatim from the page — the recording exercises the
    documented code by construction, so example and cast cannot drift."""
    import re
    import tempfile
    from pathlib import Path

    section = Path("docs/typing.md").read_text(encoding="utf-8")
    section = section.split("## Dynamic completion", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S)
    assert code is not None, "typing.md lost its dynamic-completion example"
    demo = Path(tempfile.gettempdir()) / "footman-suggest-demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (demo / "tasks.py").write_text(code.group(1), encoding="utf-8")
    run("fm --list", cwd=str(demo), capture=True)  # warm the manifest TAB serves
    return str(demo)


def _scaffold_interactive_demo() -> str:
    """A scratch project with one task per interactive shape — an `ask()`
    parameter, a `confirm=` gate, and an `interactive=True` wizard — so
    orchestration.md's interactive-input casts play the documented shapes."""
    import tempfile
    from pathlib import Path

    demo = Path(tempfile.gettempdir()) / "footman-interactive-demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (demo / "tasks.py").write_text(
        "from typing import Annotated\n"
        "from footman import ask, prompt, select, task\n\n"
        "@task\n"
        "def release(version: Annotated[str, ask()]):\n"
        '    "Cut a release."\n'
        '    print(f"Releasing {version}")\n\n'
        '@task(confirm="Deploy to production?")\n'
        "def deploy():\n"
        '    "Ship it."\n'
        '    print("Deploying to production")\n\n'
        "@task(interactive=True)\n"
        "def scaffold():\n"
        '    "Start a project."\n'
        '    name = prompt("project name? ")\n'
        '    kind = select("what kind?", ["library", "app", "plugin"])\n'
        '    print(f"Scaffolding {name} ({kind})")\n',
        encoding="utf-8",
    )
    return str(demo)


def _write_latest_changes() -> None:
    """Extract the newest release's section from CHANGELOG.md into a
    collapsed admonition the home page includes — version, date, and the
    entries, straight from the one source of truth. Rolling the changelog
    for a release updates the home page by construction."""
    import re
    from pathlib import Path

    text = Path("CHANGELOG.md").read_text(encoding="utf-8")
    head = re.search(r"^## \[(\d[^\]]+)\] — (.+?)$", text, re.M)
    if head is None:  # a fresh fork with only [Unreleased]: skip quietly
        body_block = ""
    else:
        rest = text[head.end() :]
        nxt = re.search(r"^## \[", rest, re.M)
        entries = rest[: nxt.start() if nxt else len(rest)].strip()
        indented = "\n".join(
            f"    {line}" if line else "" for line in entries.splitlines()
        )
        title = f"Latest release: {head.group(1)} — {head.group(2)}"
        # No links in here: the file is validated as its own page, where
        # relative targets differ from the including page's. The home page
        # carries the changelog link itself, right after the include.
        body_block = f'??? info "{title}"\n\n{indented}\n'

    out = Path("docs/_generated/latest-changes.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body_block, encoding="utf-8")
    print(f"wrote {out}")


def _write_llms_txt() -> None:
    """Generate docs/llms.txt and docs/llms-full.txt from the nav.

    llms.txt (https://llmstxt.org) is the agent-facing index of the docs
    site: every page in nav order, each with a one-line description pulled
    from its first prose line. llms-full.txt is the whole site's text in one
    file. Both are derived — never hand-edited — and the build copies them
    into site/ like any other docs/ file.
    """
    import tomllib
    from pathlib import Path

    site = "https://willemkokke.github.io/footman/"
    with open("zensical.toml", "rb") as fh:
        nav = tomllib.load(fh)["project"]["nav"]

    pages: list[tuple[str, str]] = []  # (title, md filename), nav order

    def walk(items: list) -> None:
        for item in items:
            for title, value in item.items():
                if isinstance(value, list):
                    walk(value)
                else:
                    pages.append((title, value))

    walk(nav)

    def first_sentence(text: str) -> str:
        """The first sentence of the page's first prose paragraph."""
        if text.startswith("---\n"):  # strip front-matter
            _, _, text = text.partition("\n---\n")
        skip = ("#", "---", "[![", "!!!", ">", "<", "--8<--")
        fenced = False
        paragraph: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("```"):
                fenced = not fenced
                continue
            if fenced or line.startswith(skip):
                continue
            if not line:
                if paragraph:
                    break  # paragraph complete
                continue
            paragraph.append(line)
        prose = " ".join(paragraph)
        end = prose.find(". ")
        return prose[: end + 1] if end != -1 else prose

    index = [
        "# footman",
        "",
        "> A Python task runner: typed function signatures become real CLI "
        "flags, modules become command groups, independent tasks run in "
        "parallel by default, and shell completion answers from a cached "
        "manifest without importing your code. Machine surface: `fm --json "
        "--list` (catalog), `fm --json <chain>` (results), `fm --help <task>`.",
        "",
        "## Docs",
        "",
    ]
    full = ["# footman — full documentation", ""]
    for title, name in pages:
        if name == "coverage.md":
            continue  # an embedded HTML report; nothing for a reader here
        text = (Path("docs") / name).read_text(encoding="utf-8")
        if name == "changelog.md":  # the page is a snippet include; inline it
            text = Path("CHANGELOG.md").read_text(encoding="utf-8")
        url = site if name == "index.md" else f"{site}{name.removesuffix('.md')}/"
        desc = first_sentence(text)
        index.append(f"- [{title}]({url}): {desc}" if desc else f"- [{title}]({url})")
        full += ["", "---", "", f"<!-- {title} — {url} -->", "", text.rstrip()]
    (Path("docs") / "llms.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    joined = "\n".join(full) + "\n"
    (Path("docs") / "llms-full.txt").write_text(joined, encoding="utf-8")


@docs.task(infinite=True)  # runs until Ctrl-C — and now says so
def serve():
    """Build and serve the docs with live reload."""
    _write_llms_txt()
    run("zensical serve")


@docs.task
def coverage():
    """Generate the coverage HTML report into docs/htmlcov (embedded in the site)."""
    run("pytest --cov=footman --cov-report=html:docs/htmlcov -q")


@docs.task(name="build")
def docs_build(check: bool = False):
    """Build the docs site into ./site; regenerates llms.txt and docs/tasks/.

    Args:
        check: build strictly (what CI runs)
    """
    # Dogfood the first-party plugin: regenerate the live task-reference
    # pages (site mode) and the single-page example the taskdocs guide
    # embeds (page mode). Plain calls — @task returns plain functions.
    # Order matters on a fresh checkout: llms.txt walks the nav, and the
    # nav includes the generated tasks/ pages — generate them first.
    from pathlib import Path

    from footman.tasks.docs import globals_ as taskdocs_globals
    from footman.tasks.docs import page as taskdocs_page
    from footman.tasks.docs import shots as taskdocs_shots
    from footman.tasks.docs import site as taskdocs_site

    taskdocs_site(Path("docs/tasks"), all=True)
    taskdocs_page(
        target="docs",
        heading=3,
        flavor="material",
        out=Path("docs/_generated/tasks-page.md"),
    )
    # The CLI reference's global-options table, from the grammar itself —
    # reference.md snippet-includes it, so it can't drift from --help.
    taskdocs_globals(out=Path("docs/_generated/globals.md"))
    # One reference page per curated tool, rendered by mkdocstrings from
    # the checked-in stubs — so the pages say what actually ships, and the
    # build needs no tool on PATH.
    from footman.tasks.tools import pages as toolpages

    toolpages(Path("docs/_generated/tools"))
    _write_latest_changes()
    # Terminal screenshots, captured from the real CLI on a pty and framed
    # as SVGs — the pages show footman exactly as a terminal does, and a
    # rebuild regenerates them, so they cannot drift either.
    shot = Path("docs/_generated/shots")
    taskdocs_shots("--list", out=shot / "list.svg", width=100)
    taskdocs_shots("--help", out=shot / "help.svg", width=100)
    taskdocs_shots("format", "lint", out=shot / "run.svg", width=72)
    # The animated one: a real zsh session — TAB menu, prefix-complete,
    # then `fm check` actually running. Same regeneration rule: the docs
    # play what the CLI does, because they are recordings of it doing it.
    from footman.tasks.docs import cast as taskdocs_cast

    taskdocs_cast(
        "fm ",
        "<TAB>",
        "<WAIT>",
        "che",
        "<TAB>",
        "<WAIT:600>",
        "<ENTER>",
        "<WAIT:2500>",
        out=shot / "zsh-cast.svg",
        shell="zsh",
        width=80,
        height=16,
    )
    # The other four shells: menu, then prefix-complete. Each shell's own
    # real menu — fish's pager, PSReadLine's MenuComplete grid, nushell's
    # completion menu, bash's candidate list. Vanilla bash reveals the
    # list on the *second* TAB (the first just rings the bell), and the
    # recording shows default behaviour, not a tuned readline.
    for sh in ("bash", "fish", "pwsh", "nushell"):
        first_tab = ("<TAB>", "<TAB>") if sh == "bash" else ("<TAB>",)
        taskdocs_cast(
            "fm ",
            *first_tab,
            "<WAIT>",
            "che",
            "<TAB>",
            "<WAIT:800>",
            out=shot / f"{sh}-cast.svg",
            shell=sh,
            width=80,
            height=16,
        )
    # Dynamic completion, recorded against typing.md's own example (the
    # demo project's tasks.py is extracted from the page): TAB offers the
    # values a plain function returned, and TAB again walks the menu.
    taskdocs_cast(
        "fm mount ",
        "<TAB>",
        "<WAIT>",
        "<TAB>",
        "<WAIT:1200>",
        out=shot / "pwsh-suggest-cast.svg",
        shell="pwsh",
        width=80,
        height=12,
        cwd=Path(_scaffold_suggest_demo()),
    )
    # Interactive input, one cast per shape (orchestration.md), against a demo
    # project with an ask() parameter, a confirm= gate, and an interactive wizard.
    # Generous waits: the prompt must persist a beat so a frame lands on it before
    # the scripted answer arrives.
    interactive_demo = Path(_scaffold_interactive_demo())
    taskdocs_cast(
        "fm release",
        "<ENTER>",
        "<WAIT:3500>",
        "1.4.0",
        "<ENTER>",
        "<WAIT:2000>",
        out=shot / "ask-cast.svg",
        shell="zsh",
        width=64,
        height=10,
        cwd=interactive_demo,
    )
    taskdocs_cast(
        "fm deploy",
        "<ENTER>",
        "<WAIT:3500>",
        "y",
        "<ENTER>",
        "<WAIT:2000>",
        out=shot / "confirm-cast.svg",
        shell="zsh",
        width=64,
        height=10,
        cwd=interactive_demo,
    )
    taskdocs_cast(
        "fm scaffold",
        "<ENTER>",
        "<WAIT:3500>",
        "myapp",
        "<ENTER>",
        "<WAIT:1800>",
        "2",
        "<ENTER>",
        "<WAIT:2000>",
        out=shot / "interactive-cast.svg",
        shell="zsh",
        width=64,
        height=12,
        cwd=interactive_demo,
    )
    _write_llms_txt()
    # A conditional flag needs no ternary: strict=check is --strict when
    # check is true, omitted otherwise (strict is off by default in zensical).
    tools.zensical.build(clean=True, strict=check, in_process=False)


dist = group("dist", help="Build and publish")


@dist.task
def build():
    """Build the sdist and wheel."""
    tools.uv("build")


@dist.task
def clean():
    """Remove build artifacts."""
    run("rm -rf dist")
