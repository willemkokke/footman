"""footman's own tasks — dogfooding the runner and its run()/tools helpers.

Run with ``fm <task>`` (or ``uv run fm <task>`` before it is installed).
Chaining works: ``fm format lint --fix test``.
"""

from __future__ import annotations

import dataclasses
import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from footman import RunFailed, doc, fail, group, parallel, plugin, run, stdin, task
from footman.tools import basedpyright, pytest, ruff, ruff_format, uv, zensical

docs = group("docs", help="Documentation site (Zensical)")

if TYPE_CHECKING:
    from pathlib import Path

# The whole repo, as CI lints it (`ruff check .`). Anything narrower lets a
# tracked file outside src/tests — a comparison script, a scratch demo — pass
# the gate and fail the build; tracking `notes/` proved it within minutes.
SRC = (".",)


@task
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False):
    """Lint with ruff."""
    ruff.check(*SRC, fix=fix)


@task
def format(check: bool = False):
    """Format with ruff.

    Args:
        check: report instead of rewriting
    """
    ruff_format(*SRC, check=check)


@task
def typecheck():
    """Type-check with basedpyright — warnings included.

    `--warnings` makes the exit code 1 when anything at all is reported, so
    a warning fails the gate exactly as an error does. A warning nobody has
    to act on is a warning everybody stops reading, and the two this started
    with were real: `__all__` advertised two submodules that no type-checker
    could resolve, so an editor gave a consumer no completion for them and a
    strict consumer saw our package complain about itself.
    """
    basedpyright(warnings=True)


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    pytest.opts(in_process=False)(*pytest_args)


@task
def check():
    """Run format --check, lint, typecheck, and the covered suite — in parallel.

    The gate: run it before every commit, and CI runs the same checks. The
    test step runs under coverage against a local floor, so this one command
    is the whole local gate — no separate `pytest --cov`.
    """
    import os
    import tempfile

    # Coverage data goes to a per-invocation file: two `fm check` runs
    # sharing the repo's .coverage (a second session, a hook racing a manual
    # run) clobber the SQLite file mid-write, and the reporter then sees a
    # bogus partial total with every test passing.
    cov_file = os.path.join(tempfile.mkdtemp(prefix="fm-check-cov-"), "coverage")

    def covered():
        # `fail_under` (pyproject, 92) is the *merged* bar: CI combines three
        # OSes x five Pythons plus the shell jobs, and disables the per-job
        # threshold because one slice can only ever see its own branches
        # (Windows run(), pwsh profiles, the other Pythons, the network-gated
        # tool fetch). One laptop measured against that bar is red by
        # construction, so the local run gets its own floor — low enough that
        # a single platform can clear it, high enough to catch a real
        # regression before CI merges the whole picture.
        run(
            "pytest --cov --cov-report= --cov-fail-under=90",
            env={"COVERAGE_FILE": cov_file},
        )

    # partial, not a lambda: it keeps the callee's name, so the live line
    # and step column say "format" instead of "…"; `covered` borrows the
    # task's name the same way.
    covered.__name__ = "test"
    parallel(functools.partial(format, check=True), lint, typecheck, covered)


@task
def sync():
    """Sync the environment to the lockfile (uv sync).

    Runs through the project's own uv (a dev dependency, resolved by the
    lockfile), so the same uv version writes uv.lock on every laptop and in CI.
    A mismatched system uv silently rewriting the lock is the source of the
    one-line churn this avoids.
    """
    uv.sync()


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


def _assert_cast_captured(svg: Path, needles: list[str]) -> None:
    """A cast whose answer landed too early (a timing regression) still renders
    a valid SVG — just without the interaction. Strip the markup and fail the
    build loudly if a prompt, its answer, or the result went missing, rather
    than shipping a mute recording no test would catch."""
    import re

    text = re.sub(
        r"&#160;", "", re.sub(r"<[^>]+>", "", svg.read_text(encoding="utf-8"))
    )
    missing = [n for n in needles if n not in text]
    if missing:
        raise RuntimeError(f"{svg.name} dropped {missing} — cast timing regressed?")


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
    run("pytest --cov --cov-report=html:docs/htmlcov -q")


@docs.task(name="build")
def docs_build(check: bool = False):  # pragma: no cover — see below
    """Build the docs site into ./site; regenerates llms.txt and docs/tasks/.

    Not unit-tested, and deliberately: the body is orchestration over
    zensical, a pty screenshotter and five real shells, so a test could only
    stub twenty collaborators and assert the call order back — a change
    detector that passes while the site breaks. Its real test is CI's
    strict docs build, which runs the whole thing against the actual tools.
    The pieces with logic of their own — the llms.txt generator, the cast
    guard, the scratch projects — are tested separately, where a test can
    say something true.

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
    # Every error and note the runtime can say, extracted from the source —
    # a reference page that regenerates each build and so can never drift.
    from footman.tasks.docs import errors as taskdocs_errors

    taskdocs_errors(out=Path("docs/_generated/errors.md"))
    # One reference page per curated tool, rendered by mkdocstrings from
    # the checked-in stubs — so the pages say what actually ships, and the
    # build needs no tool on PATH.
    from footman.tasks.tools import pages as toolpages

    toolpages(Path("docs/_generated/tools"), nav=Path("zensical.toml"))
    _write_latest_changes()
    # Terminal screenshots, captured from the real CLI on a pty and framed
    # as SVGs — the pages show footman exactly as a terminal does, and a
    # rebuild regenerates them, so they cannot drift either.
    shot = Path("docs/_generated/shots")
    taskdocs_shots("--tree", out=shot / "tree.svg", width=100)
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
    # <SETTLE> holds each answer until the prompt has finished rendering (output
    # goes quiet), so a frame always lands on the prompt and a slow boot can't
    # race the script — timing-independent, unlike a fixed wait.
    interactive_demo = Path(_scaffold_interactive_demo())
    taskdocs_cast(
        "fm release",
        "<ENTER>",
        "<SETTLE>",
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
        "<SETTLE>",
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
        "<SETTLE>",
        "myapp",
        "<ENTER>",
        "<SETTLE>",
        "2",
        "<ENTER>",
        "<WAIT:2000>",
        out=shot / "interactive-cast.svg",
        shell="zsh",
        width=64,
        height=12,
        cwd=interactive_demo,
    )
    # Each interactive cast must show its whole interaction — the <SETTLE>
    # timing exists to guarantee it, so verify the SVGs actually captured the
    # prompt, the typed answer, and the result before shipping them.
    _assert_cast_captured(shot / "ask-cast.svg", ["version", "1.4.0", "Releasing"])
    _assert_cast_captured(shot / "confirm-cast.svg", ["production", "Deploying"])
    _assert_cast_captured(
        shot / "interactive-cast.svg",
        # single tokens: the SVG drops inter-word spaces (&#160;), so "what
        # kind?" would collapse — "kind" alone still proves the select rendered.
        ["project", "kind", "library", "plugin", "Scaffolding", "myapp"],
    )
    _write_llms_txt()
    # A conditional flag needs no ternary: strict=check is --strict when
    # check is true, omitted otherwise (strict is off by default in zensical).
    zensical.opts(in_process=False).build(clean=True, strict=check)


# The agent-hook adapters: machine-called, human-invisible (the hidden
# group keeps the whole subtree out of --list/--tree/completion). Each one
# reads its harness's JSON payload from stdin as a typed dataclass — no jq,
# no host dependency — and speaks the exit-code contract its caller reads:
# 0 all quiet, 2 a blocking verdict with the receipts on stderr, and
# footman's own 64 passing through untouched, so a broken hook line reaches
# the human who wired it, never the model as a fake verdict. Coupling a
# task to its caller is fine here — this is user-land; only src/footman/
# must stay caller-blind. Wired in .claude/settings.json as
# `uv run fm hooks.<name> 1>&2`.
hooks = group("hooks", hidden=True, help="Agent lifecycle hooks (stdin-driven)")


@dataclass
class ToolInput:
    file_path: str = ""
    command: str = ""  # Bash only: what the agent is about to run


@dataclass
class HookEvent:
    tool_input: ToolInput = dataclasses.field(default_factory=ToolInput)
    stop_hook_active: bool = False
    session_id: str = ""


def _worktree(path: str) -> Path | None:
    """The checkout *path* belongs to — a git worktree root, or `None`.

    A worktree's `.git` is a file rather than a directory, so `exists` is the
    test and not `is_dir`.
    """
    from pathlib import Path

    if not path:
        return None
    start = Path(path).expanduser().resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _gate_dir(session: str) -> Path:
    """Where a session records the checkout its edits landed in.

    A hook runs in the session's *shell* directory, which is not reliably the
    checkout being worked on: `cd` somewhere outside the workspace and the
    harness resets it to the project root. Several agents share this repo
    through worktrees, so a stop hook that ran wherever the shell finished
    would gate one session's work on another's half-finished tree — which it
    did, reporting a failure in a branch this session had never touched.

    So the edits say where the work is, and the file keys on the session so
    two agents cannot answer for each other.
    """
    import tempfile
    from pathlib import Path

    return Path(tempfile.gettempdir()) / f"footman-gate-{session or 'unknown'}"


_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_RUNS_FM = re.compile(r"^\s*(?:uv run(?: --\S+)* )?f(?:m|ootman)\b")
_TRUNCATES = re.compile(r"\|\s*(?:tail|head)\b")


@hooks.task
def pre_bash(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse a footman command piped into tail/head.

    A gate's **exit code is its verdict**, and a pipe replaces it with the
    filter's — so `fm check | tail -4` reports 0 whatever happened, and prints
    the parallel step summary while the failing step scrolls past above. This
    session called a red gate green exactly that way.

    Deliberately narrow. Command separators split first, so `fm check && echo
    done | tail` stays legal; quoted spans are data, so `rg "fm check" | head`
    passes. It is a nudge and not a sandbox: `grep`, `sed` and `wc` destroy a
    verdict just as well and are not blocked, because widening it starts
    eating honest pipes — `fm docs.page | head` previews generated markdown,
    where stdout *is* the product.
    """
    segments = re.split(r";|&&|\|\|", event.tool_input.command)
    blind = (_QUOTED.sub('""', segment) for segment in segments)
    if not any(
        _TRUNCATES.search(segment[match.end() :])
        for segment in blind
        if (match := _RUNS_FM.search(segment)) is not None
    ):
        return
    fail(
        "piping a footman command into tail/head replaces its exit code with "
        "the filter's and hides the failing step — this session reported a red "
        "gate as green that way. Run it unpiped and read the exit code; to "
        "keep the output short, redirect to a file and slice the file.",
        code=2,
    )


@hooks.task
def post_edit(event: Annotated[HookEvent, stdin]) -> None:
    """Format and lint a Python file the agent just edited."""
    # Every edit, not only the Python ones: `stop` needs to know which
    # checkout this session is working in whatever it touched last.
    if root := _worktree(event.tool_input.file_path):
        _gate_dir(event.session_id).write_text(str(root), encoding="utf-8")
    if not event.tool_input.file_path.endswith(".py"):
        return
    try:
        format()
        lint()
    except RunFailed:
        fail("format/lint failed — fix it before continuing", code=2)


@hooks.task
def stop(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse to let a session end on a red gate."""
    if event.stop_hook_active:
        return  # this stop already is the retry — never ping-pong
    from pathlib import Path

    here = _worktree(str(Path.cwd()))
    try:
        recorded = _gate_dir(event.session_id).read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""  # nothing was edited, or the note is gone: gate here
    target = _worktree(recorded)
    try:
        if target is None or target == here:
            check()
        else:
            # Another checkout entirely, so the gate runs there — as a
            # subprocess, because that tree has its own environment and its
            # own footman, and this one has no business standing in for it.
            run(["uv", "run", "fm", "check"], cwd=target)
    except RunFailed:
        where = "" if target is None or target == here else f" in {target.name}"
        fail(f"the gate is red{where} — fix it before stopping", code=2)


dist = group("dist", help="Build and publish")


@dist.task
def build():
    """Build the sdist and wheel."""
    uv("build")


@dist.task
def clean():
    """Remove build artifacts."""
    run("rm -rf dist")


# Dogfood: pull footman's own first-party plugins, exactly as a user would.
# Each node lands under its own name and merges with what the file defines
# — the docs tasks join the local `docs` group leaf by leaf, tools lands at
# top level: one surface, no container. Order doesn't matter (a local
# group() adopts a pulled one, and pulls compose with existing groups);
# pulled last only so the file's own tasks list first.
plugin("footman.docs")
plugin("footman.tools")
