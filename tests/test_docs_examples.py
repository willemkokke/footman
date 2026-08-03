"""Docs examples run — the page-as-session harness.

Every ```python block in the hand-written docs is real code. Blocks on a
page execute in order in one shared namespace, like a doctest session: the
first block carries the imports, later blocks stay minimal and may build on
earlier definitions. Each block runs inside a fresh `registry.capture()` so
a recipe page may redefine a task name, and under an ambient `recording()`
so a top-level `run()`/`tools.*` illustration records instead of executing.

Two tiers:

- `test_page_examples_run` executes the session: a missing import, a stale
  API spelling, an invalid signature, or a marker misuse fails at
  decoration time with the page's own line numbers in the traceback.
- `test_page_examples_resolve_names` stitches the session into one module
  (each line at its real .md line number) and runs pyflakes' undefined-name
  check over it via ruff — task *bodies* never execute above, so this is
  the tier that catches a body using a name the page never defined.

A block that is deliberately an illustration — a stub excerpt, another
runner's API, a module that only exists in prose — opts out with an HTML
comment on the line above its fence:

    <!-- example: fragment -->

A self-contained block that starts a new lesson mid-way restarts the
session (and its namespace) with:

    <!-- example: fresh-session -->

The block carries its own imports and may reuse an earlier task name; a
run-it-there link built from the page starts accumulating there.

A block that *revises* an earlier definition in place — the same task
shown again with one feature changed, leaning on the page's context —
is marked:

    <!-- example: revision -->

It still executes in the page session (fresh capture per block, so the
redefinition is fine) and is still stitched for the name check, but no
run-it-there link carries it: concatenated into one file it would be a
duplicate task name, which footman refuses.

All three markers are invisible on the rendered page and greppable in
the source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from footman import recording, registry

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

FRAGMENT = "<!-- example: fragment -->"
FRESH = "<!-- example: fresh-session -->"
REVISION = "<!-- example: revision -->"
_OPEN = re.compile(r"^(?P<indent>[ ]*)```python\s*$")


@dataclass
class Block:
    line: int  # 1-based line of the first code line
    code: str  # dedented source
    fresh: bool = False  # the session restarts at this block
    linked: bool = True  # False: a revision — runs in-session, never in a link


def _handwritten_docs() -> list[Path]:
    return sorted(
        p
        for p in DOCS.rglob("*.md")
        if "_generated" not in p.parts and "htmlcov" not in p.parts
    )


def extract_blocks(page: Path) -> list[Block]:
    """The ```python fences of a page, dedented, fragments dropped."""
    lines = page.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = _OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        indent = m.group("indent")
        prose = next((ln for ln in reversed(lines[:i]) if ln.strip()), "")
        body: list[str] = []
        i += 1
        first = i + 1
        while i < len(lines) and lines[i].strip() != "```":
            body.append(lines[i].removeprefix(indent))
            i += 1
        i += 1  # past the closing fence
        marker = prose.strip()
        if marker != FRAGMENT:
            blocks.append(
                Block(
                    first,
                    "\n".join(body),
                    fresh=marker == FRESH,
                    linked=marker != REVISION,
                )
            )
    return blocks


def sessions(page: Path) -> list[list[Block]]:
    """The page's blocks grouped into sessions — a fresh marker starts one."""
    out: list[list[Block]] = []
    for block in extract_blocks(page):
        if block.fresh or not out:
            out.append([])
        out[-1].append(block)
    return out


def stitch(blocks: list[Block]) -> str:
    """One session as one module, each line at its real .md line."""
    lines: list[str] = []
    for block in blocks:
        lines.extend([""] * (block.line - 1 - len(lines)))
        lines.extend(block.code.splitlines())
    return "\n".join(lines) + "\n"


PAGES = [p for p in _handwritten_docs() if "```python" in p.read_text(encoding="utf-8")]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_examples_run(page: Path):
    """Execute the page's blocks in order, one namespace per session."""
    for n, session in enumerate(sessions(page)):
        # A real module in sys.modules, not a bare dict: stdlib machinery
        # (dataclasses resolving string annotations, typing's eval_str)
        # looks the defining module up by name.
        module = types.ModuleType(f"docs_{page.stem.replace('-', '_')}_{n}")
        sys.modules[module.__name__] = module
        try:
            for block in session:
                # Pad so tracebacks and SyntaxErrors point at the .md line.
                padded = "\n" * (block.line - 1) + block.code
                code = compile(padded, str(page.relative_to(ROOT)), "exec")
                with registry.capture(), recording():
                    exec(code, module.__dict__)
        finally:
            del sys.modules[module.__name__]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_run_link_sessions_load(page: Path):
    """Every run-it-there link loads: each cumulative prefix of a session —
    exactly what a link hands the playground — execs under ONE capture, so
    a task name redefined without a fresh-session or revision marker (the
    playground would refuse the duplicate) fails here, with the block's
    .md line. Revisions never ride a link, so they are skipped here."""
    for full in sessions(page):
        session = [b for b in full if b.linked]
        for end in range(1, len(session) + 1):
            module = types.ModuleType(f"docs_link_{page.stem.replace('-', '_')}")
            sys.modules[module.__name__] = module
            try:
                with registry.capture(), recording():
                    for block in session[:end]:
                        padded = "\n" * (block.line - 1) + block.code
                        code = compile(padded, str(page.relative_to(ROOT)), "exec")
                        exec(code, module.__dict__)
            finally:
                del sys.modules[module.__name__]


def test_page_examples_resolve_names(tmp_path: Path):
    """Every name a page's session uses — in bodies too — is defined by the
    page itself. One ruff F821 pass over the stitched sessions."""
    ruff = shutil.which("ruff")
    if ruff is None:  # pragma: no cover - always present under `uv run`
        pytest.skip("ruff not on PATH")
    for page in PAGES:
        for n, session in enumerate(sessions(page)):
            (tmp_path / f"{page.stem}__{n}.py").write_text(
                stitch(session), encoding="utf-8"
            )
    out = subprocess.run(
        [
            ruff,
            "check",
            "--select",
            "F821",
            "--no-cache",
            "--isolated",
            "--output-format",
            "json",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    undefined = [
        f"{Path(d['filename']).stem.rsplit('__', 1)[0]}.md:"
        f"{d['location']['row']}: {d['message']}"
        for d in json.loads(out.stdout or "[]")
    ]
    assert not undefined, "docs examples use undefined names:\n" + "\n".join(undefined)


def test_playground_module_evaluates(tmp_path: Path):
    """docs/assets/playground.js is a browser ES module built around large
    template literals holding Python source. A stray backtick or `${…}`
    inside one is invisible to every Python-side rehearsal — the Python
    extracts and runs fine — but terminates the literal in JS and kills
    the whole module at load: no run links on any page, a dead playground.
    Evaluate the module the way a browser import does, minimal DOM stubbed.
    """
    # bun is the repo's JS runtime; node is the fallback because GitHub
    # runners preinstall it (and not bun), so the guard still runs in CI.
    runtime = shutil.which("bun") or shutil.which("node")
    if runtime is None:  # pragma: no cover - node is present on every CI runner
        pytest.skip("no JS runtime (bun or node) on PATH")
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        'import { pathToFileURL } from "node:url";\n'
        "globalThis.window ="
        " { document$: { subscribe(fn) { globalThis.cb = fn; } } };\n"
        "globalThis.document = { getElementById: () => null,"
        " querySelector: () => null, addEventListener: () => {},"
        ' readyState: "complete" };\n'
        "globalThis.Node = { TEXT_NODE: 3, COMMENT_NODE: 8 };\n"
        "await import(pathToFileURL(process.argv[2]));\n"
        "globalThis.cb();\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [runtime, str(probe), str(DOCS / "assets" / "playground.js")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr


def _js_source() -> str:
    return (DOCS / "assets" / "playground.js").read_text(encoding="utf-8")


def _js_bootstrap() -> str:
    """The driver: the text of the top-level `const BOOTSTRAP = \\`…\\`;`."""
    match = re.search(r"^const BOOTSTRAP = `(.*?)`;$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines BOOTSTRAP"
    return match.group(1)


def _js_default_files() -> dict[str, str]:
    """The editor's opening tabs: `const DEFAULT_FILES = {"name": \\`…\\`, …}`."""
    match = re.search(r"^const DEFAULT_FILES = \{(.*?)^\};$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines DEFAULT_FILES"
    return dict(re.findall(r'"([^"]+)": `(.*?)`,', match.group(1), re.S))


def _playground_invoke(tmp_path: Path, line: str) -> tuple[int, str]:
    """Drive the shipped browser driver over the shipped default files.

    The exact BOOTSTRAP text runs in CPython under `_FM_PLAYGROUND_SIM`, in
    a subprocess because it monkeypatches `subprocess.Popen` process-wide.
    That sandbox tracks footman's own call surface — a `run()` that grows a
    keyword the simulated child does not take breaks every task in the page
    — and nothing else exercises it, since Pyodide is not a test dependency.
    """
    files = _js_default_files()
    assert set(files) == {"tasks.py", "test_demo.py"}, files
    probe = tmp_path / "probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport json, sys\n"
        + "print(_fm_invoke(sys.argv[1], sys.argv[2]))\n",
        encoding="utf-8",
    )
    work = Path(tempfile.mkdtemp(dir=tmp_path))  # a fresh cwd per invocation
    out = subprocess.run(
        [sys.executable, str(probe), json.dumps(files), line],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "PYTHONPATH": str(work)},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    # The page prints both streams into the one output pane; so does this.
    return int(result["exit_code"]), str(result["stdout"]) + str(result["stderr"])


# The browser's own `-s` (one thread, so the page always runs sequentially)
# is injected off `sys.platform == "emscripten"`, which no rehearsal can
# reach — spell it here instead.
def test_playground_default_sample_runs(tmp_path: Path):
    """`fm check` on the default sample: lint green through the simulated
    child, then the deliberately wrong test failing with pytest's own diff —
    the first thing a visitor sees, and what the page's prose promises."""
    code, output = _playground_invoke(tmp_path, "-s check")
    assert code == 1, output
    assert re.search(r"^ok\s+lint", output, re.M), output
    assert re.search(r"^FAIL\s+test", output, re.M), output
    assert "assert '4' == 'fizz'" in output, output
    assert "1 failed, 2 passed" in output, output


def test_playground_default_sample_spells_its_tools(tmp_path: Path):
    """The sample's `ruff.check("src", fix=fix)` builds the command a reader
    would write by hand, in both states of the flag."""
    for line, command in (
        ("-s --dry-run lint", "$ ruff check src"),
        ("-s --dry-run lint --fix", "$ ruff check src --fix"),
    ):
        _, output = _playground_invoke(tmp_path, line)
        assert command in output, output


def test_example_markers_are_spent():
    """Every example marker sits directly above a ```python fence — a
    marker that drifted away from its fence would silently stop exempting
    (or restarting) anything."""
    for page in _handwritten_docs():
        lines = page.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() not in (FRAGMENT, FRESH, REVISION):
                continue
            following = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            assert _OPEN.match(following), (
                f"{page.name}:{i + 1}: example marker without a python fence"
            )
