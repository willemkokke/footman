"""footman's own tasks — dogfooding the runner, run(), and toolroom's handles.

Run with ``fm <task>`` (or ``uv run fm <task>`` before it is installed).
Chaining works: ``fm format lint --fix test``.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from toolroom import (
    basedpyright,
    mypy,
    pyrefly,
    pytest,
    ruff,
    ruff_format,
    ty,
    uv,
    zensical,
)

from footman import (
    RunFailed,
    doc,
    fail,
    group,
    lane,
    parallel,
    plugin,
    run,
    stdin,
    step,
    task,
)

docs = group("docs", help="Documentation site (Zensical)")

# The whole repo, as CI lints it (`ruff check .`). Anything narrower lets a
# tracked file outside src/tests — a comparison script, a scratch demo — pass
# the gate and fail the build; tracking `notes/` proved it within minutes.
SRC = (".",)

# The light checks share one slot: pytest fans out `-n auto` workers of its
# own, and checkers running wide beside them fight those workers for cores
# (the `fm --profile check` trace drew it). Serialised against each other —
# and only each other — their sum still fits inside the suite's shadow.
CHECKERS = lane("checkers", reason="pytest -n auto owns the cores")


@task(lanes=(CHECKERS,))
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False):
    """Lint with ruff."""
    ruff.check(*SRC, fix=fix)


@task(lanes=(CHECKERS,))
def format(check: bool = False):
    """Format with ruff.

    Args:
        check: report instead of rewriting
    """
    ruff_format(*SRC, check=check)


@task(lanes=(CHECKERS,))
def typecheck():
    """Type-check with all four gating checkers, in parallel.

    basedpyright runs `--warnings` so a warning fails the gate exactly as an
    error does — a warning nobody has to act on is a warning everybody stops
    reading. mypy is strict on footman itself and checks every test body as
    consumer code — once per platform (linux from config, darwin and win32
    by flag), since mypy has no all-platforms mode; ty and pyrefly check
    every platform at once (`python-platform = "all"`) at the scopes
    pyproject pins. All four gate: a checker footman uses is a checker the
    tree is clean against (notes/20260730-typing-citizenship.md).
    """

    def based():
        basedpyright(warnings=True)

    # Each run gets its own cache dir: mypy's SQLite cache (2.x default)
    # does not tolerate three concurrent writers on one file.
    def mypy_linux():
        mypy(cache_dir=".mypy_cache/linux")

    def mypy_darwin():
        mypy(platform="darwin", cache_dir=".mypy_cache/darwin")

    def mypy_win32():
        mypy(platform="win32", cache_dir=".mypy_cache/win32")

    def run_ty():
        ty.check()

    def run_pyrefly():
        pyrefly("check")

    parallel(
        step(based, title="basedpyright")(),
        step(mypy_linux)(),
        step(mypy_darwin)(),
        step(mypy_win32)(),
        step(run_ty, title="ty")(),
        step(run_pyrefly, title="pyrefly")(),
    )


@task(lanes=(CHECKERS,))
def typecomplete():
    """Verify the public API is 100% type-complete (pyright --verifytypes).

    The exit code is the verdict: 0 only when every public symbol has a
    fully known type — a new unannotated export fails the gate here before
    a consumer's checker ever sees it. `--ignoreexternal` scopes the claim
    to footman's own surface (the pytest plugin necessarily references
    pytest types, whose completeness is not ours to promise).
    """
    basedpyright(verifytypes="footman", ignoreexternal=True)


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    pytest.opts(in_process=False)(*pytest_args)


@task
def check():
    """Run format --check, lint, both type gates, and the covered suite — in parallel.

    The gate: run it before every commit, and CI runs the same checks. The
    test step runs under coverage against a local floor, so this one command
    is the whole local gate — no separate `pytest --cov`.
    """
    import os
    import shutil
    import tempfile

    # Coverage data goes to a per-invocation file: two `fm check` runs
    # sharing the repo's .coverage (a second session, a hook racing a manual
    # run) clobber the SQLite file mid-write, and the reporter then sees a
    # bogus partial total with every test passing.
    #
    # Per-invocation means one directory per run, and the gate runs on every
    # commit and from the stop hook on every turn — so left behind they
    # accumulate at the rate the agent works. A day of one session left 308
    # of them, 80 MB, and nothing would ever have collected them.
    cov_dir = tempfile.mkdtemp(prefix="fm-check-cov-")
    cov_file = os.path.join(cov_dir, "coverage")

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
            # `env=` is the child's whole environment, as subprocess means it:
            # spread the task's own (which `os.environ` *is* in here) and add.
            env={**os.environ, "COVERAGE_FILE": cov_file},
        )

    try:
        # The block form carries a task's arguments naturally, and the one
        # foreign def lifts into a named step — a receipt instead of a
        # borrowed __name__.
        with parallel() as p:
            format(check=True)
            lint()
            typecheck()
            typecomplete()
            p(step(covered, title="test")())
    finally:
        # In a `finally`, because a red gate is the common case and the one
        # that would otherwise leak: `parallel` raises on the first failing
        # step, and a run that never reaches its own cleanup line is exactly
        # the run you make most often while fixing something.
        shutil.rmtree(cov_dir, ignore_errors=True)


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


def _scaffold_completion_demo() -> str:
    """The project every completion cast is recorded against.

    One small CLI, so the five recordings differ only in *shell behaviour* —
    which is the whole reason to show five. Recording against footman's own
    tasks.py meant the casts drifted whenever the gate did, and the tasks a
    reader saw were ones only this repo has.

    It carries what the casts need to show: documented options (a
    description column has to have something to put in it), a nested group
    (dotted addressing), and a `Literal` (value completion).
    """
    import tempfile
    from pathlib import Path

    demo = Path(tempfile.gettempdir()) / "footman-completion-demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (demo / "tasks.py").write_text(
        "from typing import Literal\n"
        "from footman import group, task\n\n"
        "@task\n"
        "def build(release: bool = False, jobs: int = 4):\n"
        '    """Compile and bundle.\n\n'
        "    Args:\n"
        "        release: optimise and strip symbols\n"
        "        jobs: parallel compile jobs\n"
        '    """\n\n'
        "@task\n"
        "def test(watch: bool = False, marker: str = ''):\n"
        '    """Run the test suite.\n\n'
        "    Args:\n"
        "        watch: re-run on every file change\n"
        "        marker: only tests carrying this marker\n"
        '    """\n\n'
        "@task\n"
        "def lint(fix: bool = False):\n"
        '    """Check style and types.\n\n'
        "    Args:\n"
        "        fix: apply safe fixes in place\n"
        '    """\n\n'
        'deploy = group("deploy", help="Ship it somewhere")\n\n'
        "@deploy.task\n"
        "def staging():\n"
        '    "Deploy to staging."\n\n'
        "@deploy.task\n"
        "def prod(region: Literal['eu', 'us', 'ap'] = 'eu'):\n"
        '    """Deploy to production.\n\n'
        "    Args:\n"
        "        region: which region to ship to\n"
        '    """\n',
        encoding="utf-8",
    )
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

    def walk(items: list[dict[str, Any]]) -> None:
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


# The output contract, dogfooded: the annotation below bakes this shape into
# the manifest, `fm --json docs.coverage` carries it as `returned_schema`
# beside the data, `fm --describe=docs.coverage` renders it as JSON Schema,
# and a rename here goes red in our own gate via `returned_mismatch`.
@dataclass
class CoverageReport:
    percent: float
    statements: int
    missing: int
    report: Path


@docs.task
def coverage() -> CoverageReport:
    """Generate the coverage HTML report into docs/htmlcov (embedded in the site).

    Returns:
        The measured total and where the HTML report landed.
    """
    import json
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fm-cov-totals-") as tmp:
        totals_file = Path(tmp) / "coverage.json"
        run(
            "pytest --cov --cov-report=html:docs/htmlcov "
            f"--cov-report=json:{totals_file} -q"
        )
        totals = json.loads(totals_file.read_text("utf-8"))["totals"]
    return CoverageReport(
        percent=round(totals["percent_covered"], 2),
        statements=totals["num_statements"],
        missing=totals["missing_lines"],
        report=Path("docs/htmlcov/index.html"),
    )


# --- The API reference page is generated: correct by construction --------
# Input: the typechecked TYPE_CHECKING export table in src/footman/
# __init__.py (a wrong module path there fails basedpyright). Presentation:
# the declaration below. The build refuses on divergence in either
# direction, naming the name — a new export cannot ship undocumented, and a
# stale entry cannot outlive its export. Directives use PUBLIC paths
# (`::: footman.run`), so anchors and the objects.inv inventory carry the
# contract spelling, not the defining module.

_API_INTRO = """\
<!-- Generated by 'fm docs.api' — edit _API_SECTIONS in tasks.py, not this file. -->

# API reference

Auto-generated from the source via
[mkdocstrings](https://mkdocstrings.github.io/). Everything here is importable
straight from the `footman` package (`from footman import task, run, App`).
"""

# (section title, intro prose or "", entries). An entry is a dotted path
# under `footman.`; its first component must be a root export, or the whole
# entry sits in _API_EXTRA with a reason.
_API_SECTIONS: list[tuple[str, str, list[str]]] = [
    ("Defining tasks", "", ["task", "group", "Group"]),
    (
        "Availability gates",
        "Stack these above `@task` to list a task as unavailable (with a "
        "reason) where it can't run. Every availability gate is evaluated "
        "live, and all failures are collected.",
        ["requires", "requires_dep", "requires_tool", "requires_env"],
    ),
    (
        "Running commands",
        "",
        [
            "run",
            "Result",
            "ResultView",
            "AuditEntry",
            "Argv",
            "RunFailed",
            "parallel",
            "step",
            "pre_record",
            "passthrough",
            "inherited",
        ],
    ),
    ("Failing on purpose", "", ["fail", "Failed"]),
    ("Progress", "", ["progress", "track"]),
    (
        "Profiling from inside a task",
        "",
        ["section", "mark", "stream", "Stream", "Section"],
    ),
    ("Asking the person running it", "", ["prompt", "confirm", "select"]),
    (
        "The process boundary",
        "stdin binds to typed parameters, and a `Stdout[T]` return owns "
        "stdout. The full contract lives on [Pipelines](pipelines.md) and "
        "[JSON output](json.md).",
        ["Stdin", "stdin", "Stdout", "stdout"],
    ),
    (
        "The working directory & lanes",
        "",
        ["cwd", "chdir", "Lane", "lane", "cwd_lane", "console_lane"],
    ),
    (
        "Where a task keeps things",
        "Two folders, both created on access. `cache_dir()` is derived data "
        "the collector sweeps by age; `data_dir()` is durable and "
        "machine-local — credentials, tokens, generated assets — and is never "
        "collected. Where each one lands is the CLI's business, not the "
        "task's; see [Custom CLIs](custom-cli.md#two-folders-of-your-own).",
        ["cache_dir", "data_dir"],
    ),
    ("Fetching", "", ["fetch", "FetchError"]),
    (
        "The task context",
        "`given()` answers whether the caller supplied a parameter or footman "
        "filled it in — the difference between asking for the default and "
        "having no opinion, which the value alone cannot tell you.",
        ["Context", "given", "use_context"],
    ),
    ("Composing tasks", "", ["include", "plugin", "capture"]),
    (
        "The invocation, and editing the discovered tree",
        "`@pre_tasks` runs a hook once per invocation, over the fully-merged "
        "cascade and before anything else — see "
        "[Hooks & plugin options](hooks.md#editing-the-discovered-tree). It is "
        "handed the `Invocation`, whose `tasks` is a `Tasks` view; iterating "
        "or indexing that yields a `TaskView` that reads and edits one task. "
        "The per-task pair — `@pre_task` and `@post_task` — runs around every "
        "execution; see "
        "[Around every task](hooks.md#around-every-task-pre_task-and-post_task).",
        [
            "pre_tasks",
            "pre_bind",
            "pre_task",
            "post_task",
            "post_tasks",
            "GlobalOption",
            "config_section",
            "wrap_task",
            "wrap_bind",
            "Invocation",
            "Tasks",
            "TaskView",
        ],
    ),
    ("Custom CLI", "", ["App", "Brand", "main"]),
    (
        "Typed-parameter helpers",
        "",
        [
            "Many",
            "Arg",
            "Forward",
            "forward",
            "NoSplit",
            "nosplit",
            "Hidden",
            "hidden",
            "suggest",
            "exists",
            "isfile",
            "isdir",
            "Exists",
            "IsFile",
            "IsDir",
            "matching",
            "between",
            "env",
            "check",
            "default",
            "doc",
            "ask",
            "Secret",
        ],
    ),
    (
        "Docstrings",
        "Standalone (stdlib-only, no footman imports) — reusable outside footman.",
        ["docstrings.parse", "docstrings.Docstring"],
    ),
    (
        "Markdown export",
        "Pure functions over manifest tree nodes — see "
        "[Your tasks, documented](taskdocs.md) for the task-level surface.",
        ["markdown.render_page", "markdown.render_site"],
    ),
    ("Testing", "", ["Runner", "testing.InvokeResult", "recording"]),
]

# Entries documented beyond the export table — each with its reason, never
# silent — and exports deliberately absent from the page, ditto.
_API_EXTRA: dict[str, str] = {
    "testing.InvokeResult": "Runner.invoke's return type",
}
_API_OMITTED: dict[str, str] = {}


@docs.task
def api(out: Path = Path("docs/api.md")):
    "Generate the API reference from the export table; refuse on drift."
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_api_markdown(), encoding="utf-8")


def _api_markdown() -> str:
    """The API page content, validated against the export table.

    Pure — no filesystem writes — so the docs-drift test can join the
    generated page into its blob by construction: a fresh checkout has no
    docs/api.md on disk until the docs build runs."""
    import ast

    src = Path("src/footman/__init__.py").read_text("utf-8")
    exported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.List | ast.Tuple)
            and any(getattr(t, "id", "") == "__all__" for t in node.targets)
        ):
            exported = {ast.literal_eval(e) for e in node.value.elts}
    exported -= {"__version__"}

    entries = [e for _, _, names in _API_SECTIONS for e in names]
    dupes = sorted({e for e in entries if entries.count(e) > 1})
    if dupes:
        fail(f"docs.api: duplicated entries: {', '.join(dupes)}")
    covered = {e.split(".")[0] for e in entries}
    problems = []
    missing = sorted(exported - covered - set(_API_OMITTED))
    if missing:
        problems.append(
            f"exported but undocumented: {', '.join(missing)} — add each to "
            f"a section in _API_SECTIONS, or to _API_OMITTED with a reason"
        )
    stale = sorted(
        e for e in entries if e.split(".")[0] not in exported and e not in _API_EXTRA
    )
    if stale:
        problems.append(
            f"documented but not exported: {', '.join(stale)} — drop the "
            f"entry, or record it in _API_EXTRA with a reason"
        )
    ghosts = sorted(set(_API_OMITTED) & covered) + sorted(set(_API_OMITTED) - exported)
    if ghosts:
        problems.append(
            f"_API_OMITTED disagrees with the page or the exports: "
            f"{', '.join(dict.fromkeys(ghosts))}"
        )
    if problems:
        fail(
            "docs.api: the page and the export table disagree — " + "; ".join(problems)
        )

    parts = [_API_INTRO]
    for title, intro, names in _API_SECTIONS:
        parts.append(f"\n## {title}\n")
        if intro:
            parts.append(f"\n{intro}\n")
        for name in names:
            parts.append(f"\n::: footman.{name}\n")
    return "".join(parts)


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
    import shutil
    from pathlib import Path

    from footman.tasks.docs import config as taskdocs_config
    from footman.tasks.docs import globals_ as taskdocs_globals
    from footman.tasks.docs import page as taskdocs_page
    from footman.tasks.docs import shots as taskdocs_shots
    from footman.tasks.docs import site as taskdocs_site

    # Start from nothing, which is the only state that ever gets validated:
    # both trees are gitignored, so CI builds them from an empty checkout
    # while a working copy accumulates whatever an older layout left behind.
    # A stale `docs/_generated/tools/` — pages for stubs that moved out to
    # toolroom releases ago — failed a local strict build with
    # `Could not collect 'footman._stubs.nu.Nu'`, a module this repo has not
    # had in months. Clearing costs nothing: every file below is rewritten
    # unconditionally anyway.
    for stale in (Path("docs/_generated"), Path("docs/tasks")):
        shutil.rmtree(stale, ignore_errors=True)

    # The API reference regenerates from the export table every build — the
    # page cannot omit an export, because the generator refuses to.
    api()
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
    # The [tool.footman] key table, from the keys the runner recognises —
    # configuration.md snippet-includes it, so it can't fall behind.
    taskdocs_config(out=Path("docs/_generated/config.md"))
    # Every error and note the runtime can say, extracted from the source —
    # a reference page that regenerates each build and so can never drift.
    from footman.tasks.docs import errors as taskdocs_errors

    taskdocs_errors(out=Path("docs/_generated/errors.md"))
    _write_latest_changes()
    # Terminal screenshots, captured from the real CLI on a pty and framed
    # as SVGs — the pages show footman exactly as a terminal does, and a
    # rebuild regenerates them, so they cannot drift either.
    shot = Path("docs/_generated/shots")
    taskdocs_shots("--tree", out=shot / "tree.svg", width=100)
    taskdocs_shots("--help", out=shot / "help.svg", width=100)
    taskdocs_shots("format", "lint", out=shot / "run.svg", width=72)
    # The animated ones: five real shells, one script, one demo project, so
    # the recordings differ only in shell behaviour — which is the entire
    # reason to show five. Same regeneration rule as the stills: the docs
    # play what the CLI does, because they are recordings of it doing it.
    from footman.tasks.docs import cast as taskdocs_cast

    demo = Path(_scaffold_completion_demo())
    for sh in ("zsh", "bash", "fish", "pwsh", "nushell"):
        # Two per-shell adjustments, both about *default* behaviour rather
        # than a tuned config nobody reading this has:
        #
        # Vanilla bash reveals the candidate list on the SECOND Tab — the
        # first only rings the bell — so it presses twice everywhere the
        # script means "show me".
        tab = ("<TAB>", "<TAB>") if sh == "bash" else ("<TAB>",)
        # PSReadLine's menu swallows Ctrl-C while it is open, so the line
        # survived and the next keystrokes landed inside it — one recording
        # ended on `fm build -- fm deploy.release`. Escape closes the menu
        # first, and then the cancel reaches the line.
        clear = ("<ESC>", "<CTRL-C>") if sh == "pwsh" else ("<CTRL-C>",)
        out = shot / f"{sh}-cast.svg"
        taskdocs_cast(
            # Every task, each with its summary.
            "fm ",
            *tab,
            "<WAIT>",
            # Prefix-complete to one of them.
            "bui",
            "<TAB>",
            "<WAIT:600>",
            # Its options — and what each one does.
            " --",
            *tab,
            "<WAIT>",
            # Descend a group by its dotted address.
            *clear,
            "fm deploy.",
            *tab,
            "<WAIT>",
            out=out,
            shell=sh,
            width=80,
            height=18,
            cwd=demo,
        )
        # A cast whose keystrokes raced the shell still renders a valid SVG,
        # just a mute one. Pin the beats every shell can show; descriptions
        # are checked only where the shell has a column for them (bash has
        # none, pwsh puts them in a tooltip).
        beats = ["build", "deploy", "--release", "--jobs", "deploy.staging"]
        if sh in ("zsh", "fish", "nushell"):
            beats += ["Compileandbundle", "optimiseandstripsymbols"]
        _assert_cast_captured(out, beats)
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
_PUSHES = re.compile(r"^\s*git\s+(?:-C\s+(\S+)\s+)?push\b")
_PUSH_EXEMPT = re.compile(r"\s(?:--delete|-d|--tags)\b|\bpush\s+(?:\S+\s+)?main\b")


def _push_conflicts(repo: str | None) -> bool:
    """Whether HEAD conflicts with `origin/main` — GitHub's test-merge, run
    locally in milliseconds, before the push can create the silent state.

    Fails open on every uncertainty: an offline fetch probes whatever
    `origin/main` the clone last saw, and a repo with no such ref (a scratch
    checkout, a fresh clone of something else) is not this guard's business.
    Only a conflict — `merge-tree` exit 1, distinct from its other failures —
    speaks.
    """
    import contextlib

    git = ["git", *(("-C", repo) if repo else ())]
    with contextlib.suppress(RunFailed):
        # Offline / no remote: the last-seen origin/main still answers.
        run([*git, "fetch", "--quiet", "origin", "main"], capture=True)
    try:
        run([*git, "rev-parse", "--verify", "-q", "origin/main^{commit}"], capture=True)
    except RunFailed:
        return False  # no origin/main at all: not this guard's business
    try:
        run([*git, "merge-tree", "--write-tree", "origin/main", "HEAD"], capture=True)
    except RunFailed as exc:
        # With the ref verified, exit 1 is merge-tree's one honest meaning:
        # "merged, with conflicts". (Unverified, 1 also means "no such ref".)
        return exc.result == 1
    return False


@hooks.task
def pre_bash(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse the Bash commands that succeed while creating a silently broken
    state: a footman gate piped into tail/head, and a `git push` of a branch
    that conflicts with `origin/main`.

    **The pipe guard.** A gate's **exit code is its verdict**, and a pipe
    replaces it with the filter's — so `fm check | tail -4` reports 0
    whatever happened, and prints the parallel step summary while the failing
    step scrolls past above. This session called a red gate green exactly
    that way.

    **The push guard.** Several agent sessions share this repo, so `main`
    moves while a branch is being built — and a branch pushed from a stale
    base opens a CONFLICTING pull request, for which GitHub cannot build its
    test-merge and therefore **spawns no CI at all**: no red X, no checks,
    just an absence nothing points at (PR #304 sat exactly that way). The
    guard runs the same test-merge locally (`git merge-tree --write-tree`)
    before letting the push through. Tag pushes, deletions and pushes of
    `main` itself pass untouched.

    Both are deliberately narrow. Command separators split first, so `fm
    check && echo done | tail` stays legal; quoted spans are data, so `rg "fm
    check" | head` passes. Nudges, not a sandbox: `grep` destroys a verdict
    just as well, and `--force` is still force — widening either guard
    starts eating honest commands.
    """
    segments = re.split(r";|&&|\|\|", event.tool_input.command)
    blind = [_QUOTED.sub('""', segment) for segment in segments]
    if any(
        _TRUNCATES.search(segment[match.end() :])
        for segment in blind
        if (match := _RUNS_FM.search(segment)) is not None
    ):
        fail(
            "piping a footman command into tail/head replaces its exit code "
            "with the filter's and hides the failing step — this session "
            "reported a red gate as green that way. Run it unpiped and read "
            "the exit code; to keep the output short, redirect to a file and "
            "slice the file.",
            code=2,
        )
    for segment in blind:
        push = _PUSHES.search(segment)
        if push is None or _PUSH_EXEMPT.search(segment):
            continue
        repo = push.group(1)  # a quoted -C path was blinded; probe the cwd then
        if _push_conflicts(None if repo in (None, '""') else repo):
            fail(
                "git push refused: this branch conflicts with origin/main. A "
                "conflicting PR spawns no CI at all — GitHub cannot build its "
                "test-merge, so there is no red X, no checks, just silence "
                "(PR #304 sat that way). Rebase (git fetch origin && git "
                "rebase origin/main), re-run the gate, then push.",
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
# — the docs tasks join the local `docs` group leaf by leaf. Order doesn't
# matter (a local group() adopts a pulled one, and pulls compose with
# existing groups); pulled last only so the file's own tasks list first.
plugin("footman.docs")
plugin("footman.profile")
