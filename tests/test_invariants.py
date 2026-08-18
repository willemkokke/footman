"""The two hard invariants, as tests — violating either fails the gate.

CLAUDE.md declares them; nothing in the suite enforced them (audit H50):
zero runtime dependencies, and a completion hot path that never imports
the framework or the user's code. Both are load-bearing for the pitch, so
both get teeth here. The import-scan half runs static over the source —
an `import requests` anywhere under src/footman/ fails this file before
packaging metadata would ever notice.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "footman"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# The lazily-imported optional friends a module may reach for *inside* a
# gated body: the blessed exception in CLAUDE.md — a first-party plugin task
# stacked under @requires_dep may import its optional package at call time.
# Everything named here must appear only under a function, never at module
# level; the module-level scan below stays absolute.
_OPTIONAL = frozenset({"rich"})


def _module_level_imports(path: Path) -> set[str]:
    """Top-level (module-scope) imports of *path*, by root package name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:  # module scope only: function bodies may lazy-load
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.partition(".")[0])
    return found


def test_zero_runtime_dependencies_is_declared_and_true():
    # The declaration: nothing under [project] dependencies.
    meta = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert meta["project"]["dependencies"] == []
    # The practice: every module-level import in the shipped package is the
    # standard library or footman itself. A lazy optional import inside a
    # @requires_dep-gated body is legal (the blessed exception); this walk
    # reads module scope only, so such an import failing HERE means someone
    # hoisted it to the top of the file.
    allowed = set(sys.stdlib_module_names) | {"footman"}
    # One module is imported BY its dependency, never the other way round:
    # the pytest plugin loads through the `pytest11` entry point, so pytest
    # is present by construction whenever this file executes. Nothing else
    # gets the pass — and nothing under footman/ may import pytest_plugin.
    per_file = {"pytest_plugin.py": {"pytest"}}
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        extra = per_file.get(str(path.relative_to(SRC)), set())
        for name in _module_level_imports(path):
            if name not in allowed and name not in extra:
                offenders.append(f"{path.relative_to(SRC)}: import {name}")
    assert not offenders, "\n".join(offenders)
    importers = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "pytest_plugin.py"
        and any(
            "pytest_plugin" in ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.lstrip().startswith(("import ", "from "))
        )
    ]
    assert not importers, importers  # the exemption must stay one-way


def test_the_completion_hot_path_imports_no_framework_and_no_tasks():
    # One real TAB press in a fresh project, in a fresh interpreter. The
    # process answers, then testifies about every module it loaded: no
    # framework internals (registry, _app, _split — the run machinery), and
    # never the user's tasks file. The manifest build that DOES import
    # tasks.py happens in a detached child, so this process stays clean
    # either way — which is exactly the invariant.
    probe = (
        "import json, sys\n"
        "from footman import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "loaded = sorted(\n"
        "    n for n in sys.modules\n"
        "    if n == 'footman' or n.startswith('footman.')\n"
        ")\n"
        "print('LOADED ' + json.dumps(loaded))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe, "--complete", "--", ""],
        capture_output=True,
        text=True,
        timeout=60,
    )
    line = next(
        (ln for ln in done.stdout.splitlines() if ln.startswith("LOADED ")), None
    )
    assert line is not None, done.stdout + done.stderr
    loaded = set(json.loads(line[len("LOADED ") :]))
    # The hot path's whole allowance. Growing this set is a decision about
    # the ~30 ms budget, not a test to appease — that is why it is exact.
    assert loaded <= {"footman", "footman._complete", "footman._paths"}, sorted(loaded)
    forbidden = {"footman.registry", "footman._app", "footman._split"}
    assert not (loaded & forbidden), sorted(loaded & forbidden)
