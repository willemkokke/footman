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

The marker is invisible on the rendered page and greppable in the source.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from footman import recording, registry

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

FRAGMENT = "<!-- example: fragment -->"
_OPEN = re.compile(r"^(?P<indent>[ ]*)```python\s*$")


@dataclass
class Block:
    line: int  # 1-based line of the first code line
    code: str  # dedented source


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
        if prose.strip() != FRAGMENT:
            blocks.append(Block(first, "\n".join(body)))
    return blocks


def stitch(page: Path) -> str:
    """The page's session as one module, each line at its real .md line."""
    lines: list[str] = []
    for block in extract_blocks(page):
        lines.extend([""] * (block.line - 1 - len(lines)))
        lines.extend(block.code.splitlines())
    return "\n".join(lines) + "\n"


PAGES = [p for p in _handwritten_docs() if "```python" in p.read_text(encoding="utf-8")]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_examples_run(page: Path):
    """Execute the page's blocks in order in one namespace."""
    # A real module in sys.modules, not a bare dict: stdlib machinery
    # (dataclasses resolving string annotations, typing's eval_str) looks
    # the defining module up by name.
    module = types.ModuleType(f"docs_{page.stem.replace('-', '_')}")
    sys.modules[module.__name__] = module
    try:
        for block in extract_blocks(page):
            # Pad so tracebacks and SyntaxErrors point at the .md line.
            padded = "\n" * (block.line - 1) + block.code
            code = compile(padded, str(page.relative_to(ROOT)), "exec")
            with registry.capture(), recording():
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
        (tmp_path / f"{page.stem}.py").write_text(stitch(page), encoding="utf-8")
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
        f"{Path(d['filename']).stem}.md:{d['location']['row']}: {d['message']}"
        for d in json.loads(out.stdout or "[]")
    ]
    assert not undefined, "docs examples use undefined names:\n" + "\n".join(undefined)


def test_fragment_markers_are_spent():
    """Every fragment marker sits directly above a ```python fence — a
    marker that drifted away from its fence would silently stop exempting
    anything."""
    for page in _handwritten_docs():
        lines = page.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() != FRAGMENT:
                continue
            following = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            assert _OPEN.match(following), (
                f"{page.name}:{i + 1}: fragment marker without a python fence"
            )
