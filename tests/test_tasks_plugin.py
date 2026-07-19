"""The first-party `footman` plugin: mounting, page/site tasks, hot path."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from footman import _app, _paths


@pytest.fixture
def plugin_project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nplugins = ['footman']\n"
    )
    (tmp_path / "tasks.py").write_text(
        "from footman import task, group\n"
        "\n"
        "@task\n"
        "def greet(name: str = 'world'):\n"
        '    "Say hello."\n'
        "\n"
        "docs = group('docs', help='Documentation')\n"
        "\n"
        "@docs.task\n"
        "def serve(port: int = 8000):\n"
        '    "Serve the docs."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_bare_import_never_loads_first_party_tasks():
    # Hot-path guard: `import footman` must not pull the plugin package.
    probe = "import footman, sys; print('footman.tasks' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_plugin_mounts_under_footman(plugin_project, capsys):
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "footman docs page" in out and "footman docs site" in out


def test_page_prints_the_tree_to_stdout(plugin_project, capsys):
    assert _app.run(["footman", "docs", "page"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# fm tasks\n")
    assert "## greet" in out and "### docs serve" in out
    assert "footman" not in out.replace("# fm tasks", "")  # the documenter is absent


def test_page_all_includes_the_documenter(plugin_project, capsys):
    assert _app.run(["footman", "docs", "page", "--all"]) == 0
    assert "footman docs page" in capsys.readouterr().out


def test_page_scoped_and_written_to_a_file(plugin_project, capsys):
    dest = plugin_project / "build" / "serve.md"
    line = ["footman", "docs", "page", "--target", "docs.serve", "--out", str(dest)]
    collected: list = []
    assert _app.run(line, collect=collected) == 0
    text = dest.read_text()
    assert text.startswith("# docs serve\n")
    assert "greet" not in text  # scoped away
    assert collected[0].returned == [str(dest)]
    assert "wrote" in capsys.readouterr().out  # task output (streams merge)


def test_page_unknown_target_is_a_task_failure(plugin_project, capsys):
    assert _app.run(["footman", "docs", "page", "--target", "nope"]) == 1
    assert "no task or group named 'nope'" in capsys.readouterr().err


def test_site_writes_indexes_and_pages(plugin_project, capsys):
    collected: list = []
    assert _app.run(["footman", "docs", "site", "pages"], collect=collected) == 0
    root = plugin_project / "pages"
    assert (root / "index.md").exists()
    assert (root / "greet.md").exists()
    assert (root / "docs" / "index.md").exists()
    assert (root / "docs" / "serve.md").exists()
    index = (root / "index.md").read_text()
    assert "[`greet`](greet.md)" in index and "[`docs`](docs/index.md)" in index
    assert "!!! example" in (root / "greet.md").read_text()  # material default
    returned = sorted(Path(p).resolve() for p in collected[0].returned)
    assert returned == sorted(p.resolve() for p in root.rglob("*.md"))


def test_branded_cli_documents_itself(plugin_project):
    # A branded CLI's pages carry its own name with no flag at all: the
    # invoking brand rides the task context, and --prog stays the override.
    from footman import App
    from footman.testing import Runner

    acme = Runner(App(name="Acme", prog="acme", version="1.0"))
    result = acme.invoke("footman docs page")
    assert result.ok
    assert result.stdout.startswith("# acme tasks\n")
    assert "acme greet" in result.stdout
    overridden = acme.invoke("footman docs page --prog other")
    assert overridden.stdout.startswith("# other tasks\n")


def test_page_rides_the_json_envelope(plugin_project, capsys):
    assert _app.run(["--json", "footman", "docs", "page"]) == 0
    payload = json.loads(capsys.readouterr().out)
    (entry,) = payload["results"]
    assert entry["task"] == "footman.docs.page"
    assert "# fm tasks" in entry["output"]  # the markdown, captured


def test_globals_task_prints_the_grammar(plugin_project, capsys):
    assert _app.run(["footman", "docs", "globals"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| option")
    assert "`--json`" in out
    assert "help for fm" in out  # {prog} filled with the invoking CLI


def test_globals_task_writes_out(plugin_project, capsys):
    dest = plugin_project / "docs" / "_generated" / "globals.md"
    assert _app.run(["footman", "docs", "globals", "--out", str(dest)]) == 0
    assert dest.read_text(encoding="utf-8").startswith("| option")
