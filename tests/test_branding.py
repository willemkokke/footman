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


def test_help_globals_row_uses_brand(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef hi():\n    print('hi')\n"
    )
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("--help", cwd=tmp_path)
    assert "help for acme" in result.stdout
    assert "help for fm" not in result.stdout


def test_brand_renames_the_default_tasks_file(tmp_path, monkeypatch):
    """A brand's `tasks_file` sets the filename its users write, and the
    cascade honours it without any per-project config."""
    from footman import App, _paths
    from footman.testing import Runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "acmetasks.py").write_text(
        'from footman import task\n\n@task\ndef ship():\n    "Ship it."\n'
    )
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(
        App(name="Acme", prog="acme", version="1.0", tasks_file="acmetasks.py")
    )
    out = acme.invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_brand_tasks_file_rides_in_the_manifest(tmp_path, monkeypatch):
    """The background refresh child can't know the brand — so the filename
    is baked into the manifest it rebuilds from."""
    import json

    from footman import App, _paths
    from footman.testing import Runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "acmetasks.py").write_text(
        'from footman import task\n\n@task\ndef ship():\n    "Ship it."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    Runner(App(prog="acme", tasks_file="acmetasks.py")).invoke("--list")
    baked = json.loads(_paths.manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert baked["tasks_file"] == "acmetasks.py"
