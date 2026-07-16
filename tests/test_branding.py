"""The public App entry: custom brand (name/prog/version) in all output."""

from __future__ import annotations

from footman import App, Brand, __version__, _paths


def test_brand_defaults_to_footman():
    b = Brand()
    assert (b.name, b.prog, b.version) == ("footman", "fm", __version__)


def test_default_app_version(capsys):
    assert App().run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == f"footman {__version__}"


def test_custom_brand_version(capsys):
    app = App(name="HSE", prog="hse", version="1.4.0")
    assert app.run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == "HSE 1.4.0"


def test_custom_brand_error_prefix(capsys):
    app = App(name="HSE", prog="hse", version="1.4.0")
    code = app.run(["-f", "/nope/tasks.py", "whatever"])
    assert code == 2
    assert capsys.readouterr().err.startswith("hse: ")


def test_custom_version_defaults_to_footman_when_omitted(capsys):
    # prog/name can differ while version falls back to footman's own
    App(name="HSE", prog="hse").run(["-V"])
    assert capsys.readouterr().out.strip() == f"HSE {__version__}"


def test_app_complete_dispatches(capsys):
    # the --complete hot path returns cleanly even with nothing cached
    assert App().run(["--complete", "--", ""]) == 0


def test_default_app_runs_tasks_like_fm(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef hi():\n    print('hello')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert App().run(["hi"]) == 0
    assert "hello" in capsys.readouterr().out


def test_custom_brand_runs_tasks_from_cascade(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from footman import task\n@task\ndef ship():\n    print('shipped')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    app = App(name="HSE", prog="hse", version="1.4.0")
    assert app.run(["ship"]) == 0  # cascade discovery, just rebranded
    assert "shipped" in capsys.readouterr().out
