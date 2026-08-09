"""The public App entry: custom brand (name/prog/version) in all output.

The disk-backed cases run through `footman.testing.Runner` — the suite
dogfoods the same harness users are told to test their branded CLIs with.
"""

from __future__ import annotations

from footman import App, Brand, __version__, _paths
from footman._executor import EX_USAGE
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
    assert result.exit_code == EX_USAGE
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


# --- the brand's private world -----------------------------------------------
#
# A branded CLI is a product; footman is a dependency inside it. Every
# location and every environment variable it touches must derive from the
# brand — and stock footman must be exactly what it always was.

TASKS = 'from footman import task\n\n@task\ndef ship():\n    "Ship it."\n'


def _project(tmp_path, body: str = "") -> None:
    (tmp_path / "pyproject.toml").write_text(f"[project]\nname='x'\n{body}")
    (tmp_path / "tasks.py").write_text(TASKS)


def test_prefix_is_derived_from_the_display_name():
    # `name`, not `prog`: footman's prog is `fm`, and deriving from it would
    # rename every FOOTMAN_* variable that has ever worked.
    assert Brand().prefix == "FOOTMAN"
    assert Brand(name="acme").env("CACHE_DIR") == "ACME_CACHE_DIR"
    assert Brand(name="Acme Devkit").prefix == "ACME_DEVKIT"
    assert Brand(name="acme", env_prefix="ACME2").env("NO_GC") == "ACME2_NO_GC"


def test_a_home_holds_everything_the_cli_owns(tmp_path, monkeypatch):
    home = tmp_path / "world"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / "xdg-cache")
    Runner(App(name="acme", prog="acme", home=home)).invoke("--list")
    assert list((home / "cache").glob("*.json"))  # its manifest landed here
    assert not (tmp_path / "xdg-cache").exists()  # and nothing under XDG


def test_stock_variables_never_move_a_branded_client(tmp_path, monkeypatch):
    # The whole point: someone debugging `fm` must not relocate a product.
    elsewhere, home = tmp_path / "stock", tmp_path / "world"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(elsewhere))
    Runner(App(name="acme", prog="acme", home=home)).invoke("--list")
    assert not elsewhere.exists()
    assert list((home / "cache").glob("*.json"))


def test_a_brand_reads_its_own_cache_variable(tmp_path, monkeypatch):
    theirs = tmp_path / "theirs"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CACHE_DIR", str(theirs))
    Runner(App(name="acme", prog="acme", home=tmp_path / "world")).invoke("--list")
    assert list(theirs.glob("*.json"))  # the specific variable beats the home


def test_home_env_overrides_the_home(tmp_path, monkeypatch):
    # Two installations logged in as different identities, side by side.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_RUNNER_HOME", str(tmp_path / "user-b"))
    app = App(
        name="acme", prog="acme", home=tmp_path / "user-a", home_env="ACME_RUNNER_HOME"
    )
    Runner(app).invoke("--list")
    assert list((tmp_path / "user-b" / "cache").glob("*.json"))
    assert not (tmp_path / "user-a").exists()


def test_a_brand_does_not_pick_up_a_prefix_home_by_itself(tmp_path, monkeypatch):
    # A product's own *_HOME names the product's world; the runner's corner
    # of it is the brand's to choose, so footman must never infer one.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "guessed"))
    Runner(App(name="acme", prog="acme", home=tmp_path / "world")).invoke("--list")
    assert not (tmp_path / "guessed").exists()


def test_footman_home_relocates_stock_footman(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_HOME", str(tmp_path / "fmhome"))
    Runner(App(home_env="FOOTMAN_HOME")).invoke("--list")
    assert list((tmp_path / "fmhome" / "cache").glob("*.json"))


def test_the_config_table_is_the_brands(tmp_path, monkeypatch):
    # Two branded CLIs in one repo read their own settings instead of
    # fighting over `[tool.footman]`. The `tasks` key makes the answer
    # visible: whichever table won names the file that got loaded.
    _project(
        tmp_path,
        '[tool.acme]\ntasks = "mine.py"\n[tool.footman]\ntasks = "theirs.py"\n',
    )
    (tmp_path / "mine.py").write_text(TASKS)
    (tmp_path / "theirs.py").write_text(
        'from footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_the_dedicated_config_file_is_the_brands(tmp_path, monkeypatch):
    _project(tmp_path)
    (tmp_path / "acme.toml").write_text('tasks = "mine.py"\n')
    (tmp_path / "mine.py").write_text(TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_cascade_error_names_the_brands_variable(tmp_path, monkeypatch):
    # Naming FOOTMAN_CASCADE at an acme user teaches a variable that does
    # nothing for them.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CASCADE", "sideways")
    result = Runner(App(name="acme", prog="acme")).invoke("ship")
    assert result.exit_code == EX_USAGE
    assert "ACME_CASCADE" in result.stderr and "FOOTMAN_CASCADE" not in result.stderr


def test_the_user_tasks_file_answers_where_a_project_has_none(tmp_path, monkeypatch):
    home, empty = tmp_path / "world", tmp_path / "empty"
    home.mkdir()
    empty.mkdir()
    (home / "tasks.py").write_text(TASKS)
    monkeypatch.chdir(empty)
    out = Runner(App(name="acme", prog="acme", home=home)).invoke("--list").stdout
    assert "ship" in out


def test_a_project_cascade_beats_the_user_tasks_file(tmp_path, monkeypatch):
    # A fallback, not a rung: there is one way to get tasks into a project
    # tree, and that is pulling them in a tasks file.
    home = tmp_path / "world"
    home.mkdir()
    (home / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef mine():\n    "Personal."\n'
    )
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = Runner(App(name="acme", prog="acme", home=home)).invoke("--list").stdout
    assert "ship" in out and "mine" not in out


def test_child_argv_carries_the_resolved_locations(tmp_path):
    # Children inherit the environment but not the brand, so they are told
    # where the cache is rather than re-deriving it wrongly.
    before = _paths.child_args()
    try:
        _paths.configure(prefix="ACME", home=tmp_path, config_name="acme")
        assert _paths.child_args() == ["ACME", str(tmp_path), "acme"]
        _paths.configure_child(*_paths.child_args())
        assert _paths.env_var("NO_GC") == "ACME_NO_GC"
        assert _paths.footman_cache_dir() == tmp_path / "cache"
        _paths.configure_child()  # empty words mean stock footman
        assert _paths.env_var("NO_GC") == "FOOTMAN_NO_GC"
        assert _paths.brand_home() is None
    finally:
        _paths.configure_child(*before)
