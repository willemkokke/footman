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


@docs.task(progress=False)  # runs until Ctrl-C: no duration to learn from
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
