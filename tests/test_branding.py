"""The public App entry: custom brand (name/prog/version) in all output.

The disk-backed cases run through `footman.testing.Runner` — the suite
dogfoods the same harness users are told to test their branded CLIs with.
"""

from __future__ import annotations

from footman import App, Brand, __version__
from footman.testing import Runner


def test_brand_defaults_to_footman():
    b = Brand()
    assert (b.name, b.prog, b.version) == ("footman", "fm", __version__)


def test_default_app_version(capsys):
    assert App().run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == f"footman {__version__}"


def test_custom_brand_version(capsys):
    app = App(name="Acme", prog="acme", version="1.4.0")
    assert app.run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == "Acme 1.4.0"


def test_custom_brand_error_prefix():
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("-f /nope/tasks.py whatever")
    assert result.exit_code == 2
    assert result.stderr.startswith("acme: ")


def test_custom_version_defaults_to_footman_when_omitted(capsys):
    # prog/name can differ while version falls back to footman's own
    App(name="Acme", prog="acme").run(["-V"])
    assert capsys.readouterr().out.strip() == f"Acme {__version__}"


def test_app_complete_dispatches(capsys):
    # the --complete hot path returns cleanly even with nothing cached
    assert App().run(["--complete", "--", ""]) == 0


def test_default_app_runs_tasks_like_fm(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef hi():\n    print('hello')\n"
    )
    result = Runner().invoke("hi", cwd=tmp_path)
    assert result.ok
    assert "hello" in result.stdout


def test_custom_brand_runs_tasks_from_cascade(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef ship():\n    print('shipped')\n"
    )
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("ship", cwd=tmp_path)  # cascade discovery, rebranded
    assert result.ok
    assert "shipped" in result.stdout
