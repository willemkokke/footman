"""The public App entry: custom brand (name/prog/version) in all output.

The disk-backed cases run through `footman.testing.Runner` — the suite
dogfoods the same harness users are told to test their branded CLIs with.
"""

from __future__ import annotations

import os
import stat

from footman import App, Brand, __version__, _paths
from footman._executor import EX_USAGE
from footman.app import DEFAULT_BRAND
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
    # In the brand's own cache corner — `.cache/acme/`, from the config stem
    # deriving from prog. (`.cache/footman/` here used to be the accident of
    # `config_name` defaulting to the display name.)
    manifest = next(
        p
        for p in (tmp_path / ".cache" / "acme").glob("*.json")
        if not p.name.endswith(".times.json")
    )
    baked = json.loads(manifest.read_text(encoding="utf-8"))
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


def test_prefix_is_derived_from_the_command_name():
    # `prog`: the word users type, and shell-safe by construction where a
    # display name is free text.
    assert Brand(prog="acme").env("CACHE_DIR") == "ACME_CACHE_DIR"
    assert Brand(prog="acme-tool").prefix == "ACME_TOOL"
    assert Brand(prog="acme", env_prefix="ACME2").env("NO_GC") == "ACME2_NO_GC"


def test_stock_footman_pins_its_prefix():
    # Not compatibility: `FOOTMAN_CACHE_DIR` says what it belongs to and is
    # searchable, where `FM_CACHE_DIR` is opaque. A two-letter command is
    # exactly when a brand should pin a longer prefix than its prog.
    assert DEFAULT_BRAND.prefix == "FOOTMAN"
    assert DEFAULT_BRAND.env("CACHE_DIR") == "FOOTMAN_CACHE_DIR"


def test_config_name_derives_from_prog():
    # The machine word, exactly as the env prefix does — never the display
    # name, which is free text: `[tool.Acme]` from `name="Acme"` was a
    # silent misconfig for every user the docs taught `[tool.acme]`.
    b = Brand(name="Acme", prog="acme")
    assert b.config_stem == "acme"
    assert b.config_file() == "acme.toml"
    assert App(name="Acme", prog="acme").brand.config_stem == "acme"
    assert Brand(prog="acme", config_name="acme-runner").config_stem == "acme-runner"


def test_stock_footman_pins_its_config_name():
    # The config twin of the pinned prefix: `footman.toml`, not `fm.toml`.
    assert DEFAULT_BRAND.config_stem == "footman"
    assert DEFAULT_BRAND.config_file() == "footman.toml"


def test_a_bare_app_is_stock_footman():
    # Keeping the `fm` command keeps the stock pins: a test-harness
    # `App(tasks_file=...)` reads `[tool.footman]` and `FOOTMAN_*` exactly
    # like the `fm` on PATH — not a derived `[tool.fm]` nobody writes.
    brand = App(tasks_file="jobs.py").brand
    assert brand.config_stem == "footman"
    assert brand.prefix == "FOOTMAN"
    # Moving `prog` ends the pins, and the brand derives from its command.
    assert App(prog="acme").brand.config_stem == "acme"
    assert App(prog="acme").brand.prefix == "ACME"


def test_the_two_folders_are_placed_independently(tmp_path, monkeypatch):
    # The point of two fields rather than one home: a product puts its cache
    # in its own cache area and its data somewhere else entirely.
    cache, data = tmp_path / "prod" / ".cache" / "acme-cli", tmp_path / "prod" / "acme"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / "xdg-cache")
    Runner(App(name="acme", prog="acme", cache_dir=cache, data_dir=data)).invoke(
        "--list"
    )
    assert list(cache.glob("*.json"))  # its manifest landed here
    assert not (tmp_path / "xdg-cache").exists()  # and nothing under XDG


def test_stock_variables_never_move_a_branded_client(tmp_path, monkeypatch):
    # The whole point: someone debugging `fm` must not relocate a product.
    elsewhere, cache = tmp_path / "stock", tmp_path / "mine"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(elsewhere))
    Runner(App(name="acme", prog="acme", cache_dir=cache)).invoke("--list")
    assert not elsewhere.exists()
    assert list(cache.glob("*.json"))


def test_a_brands_own_variables_win_over_its_declared_folders(tmp_path, monkeypatch):
    # The environment beats the brand — that is what lets two installations
    # run side by side under different identities.
    theirs = tmp_path / "theirs"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CACHE_DIR", str(theirs))
    app = App(name="acme", prog="acme", cache_dir=tmp_path / "declared")
    Runner(app).invoke("--list")
    assert list(theirs.glob("*.json"))
    assert not (tmp_path / "declared").exists()


def test_data_dir_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    # ~/.local/share, where footman's completion scripts already live.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    before = _paths.child_args()
    try:
        _paths.configure(prefix="ACME", config_name="acme")
        assert _paths.footman_data_dir() == tmp_path / "share" / "acme"
        monkeypatch.setenv("ACME_DATA_DIR", str(tmp_path / "elsewhere"))
        assert _paths.footman_data_dir() == tmp_path / "elsewhere"
    finally:
        _paths.configure_child(*before)


def test_the_accessors_create_the_directory(tmp_path, monkeypatch):
    # A task writing into these should not have to mkdir first.
    import footman

    before = _paths.child_args()
    try:
        _paths.configure(
            cache_dir=tmp_path / "c" / "deep", data_dir=tmp_path / "d" / "deep"
        )
        assert footman.cache_dir().is_dir()
        assert footman.data_dir().is_dir()
        if os.name == "posix":
            # The data directory holds credentials, so its leaf is created
            # owner-only, like `~/.ssh`. Creation-time only — an existing
            # directory keeps whatever its owner gave it.
            assert stat.S_IMODE(footman.data_dir().stat().st_mode) == 0o700
    finally:
        _paths.configure_child(*before)


def test_the_cache_and_data_directories_must_differ(tmp_path, monkeypatch):
    # The collector deletes from the cache by age; pointed at the data
    # directory it would eventually delete credentials. Refuse, don't warn.
    same = tmp_path / "both"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    app = App(name="acme", prog="acme", cache_dir=same, data_dir=same)
    result = Runner(app).invoke("--list")
    assert result.exit_code == EX_USAGE
    assert "must differ" in result.stderr
    assert "ACME_CACHE_DIR" in result.stderr and "ACME_DATA_DIR" in result.stderr


def test_footman_cache_dir_relocates_stock_footman(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "fmcache"))
    Runner(App(env_prefix="FOOTMAN")).invoke("--list")
    assert list((tmp_path / "fmcache").glob("*.json"))


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


def test_the_brands_tasks_file_marks_a_project_root(tmp_path, monkeypatch):
    # The mirror of `acme.toml` marking one. A project whose *only* marker is
    # the brand's tasks file — no pyproject.toml, no checkout, no acme.toml —
    # is the "Docker context with .git ignored" shape, and before this the
    # literal `tasks.py` in PROJECT_MARKERS meant an `acmetasks.py` root went
    # unrecognised and the cascade started in the wrong directory.
    before = _paths.child_args()
    try:
        _paths.configure(config_name="acme", tasks_file="acmetasks.py")
        (tmp_path / "acmetasks.py").write_text(TASKS)
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert _paths.find_project_root(deep) == tmp_path.resolve()
        assert "acmetasks.py" in _paths.project_markers()
        assert "tasks.py" not in _paths.project_markers()
    finally:
        _paths.configure_child(*before)


def test_a_brands_tasks_file_reaches_the_cascade_ceiling(tmp_path, monkeypatch):
    # End to end: the ceiling found above is what the cascade walks down from,
    # so a task defined at that root is visible from a subdirectory.
    (tmp_path / "acmetasks.py").write_text(TASKS)
    deep = tmp_path / "pkg"
    deep.mkdir()
    monkeypatch.chdir(deep)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    app = App(name="acme", prog="acme", tasks_file="acmetasks.py")
    assert "ship" in Runner(app).invoke("--list").stdout


def test_the_user_tasks_file_answers_where_a_project_has_none(tmp_path, monkeypatch):
    # Beside the user's config file, because both are the user's own writing
    # — not somewhere the brand placed.
    cfg, empty = tmp_path / "cfg", tmp_path / "empty"
    (cfg / "acme").mkdir(parents=True)
    empty.mkdir()
    (cfg / "acme" / "tasks.py").write_text(TASKS)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(empty)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_config_dir_moves_the_users_writing_together(tmp_path, monkeypatch):
    # `ACME_CONFIG_DIR` relocates the brand's config corner — config file and
    # user tasks file travel together. The narrow, brand-scoped alternative
    # to `XDG_CONFIG_HOME`, which would move every other application's config
    # along with it: a task runner relocates its own corner, not the desktop.
    identity = tmp_path / "identity-b"
    identity.mkdir()
    # The config file renames the tasks file; the tasks file defines `ship`.
    # `ship` listing proves BOTH were read from the relocated directory.
    (identity / "config.toml").write_text('tasks = "mine.py"\n')
    (identity / "mine.py").write_text(TASKS)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("ACME_CONFIG_DIR", str(identity))
    monkeypatch.chdir(empty)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_the_config_file_variable_is_finer_than_the_dir(tmp_path, monkeypatch):
    # `ACME_CONFIG` names one file and wins over `ACME_CONFIG_DIR`'s
    # config.toml — the specific beats the general, same as everywhere else.
    d = tmp_path / "dir"
    d.mkdir()
    (d / "config.toml").write_text('tasks = "wrong.py"\n')
    finer = tmp_path / "finer.toml"
    finer.write_text('tasks = "mine.py"\n')
    (tmp_path / "mine.py").write_text(TASKS)
    (tmp_path / "wrong.py").write_text(
        'from footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.setenv("ACME_CONFIG_DIR", str(d))
    monkeypatch.setenv("ACME_CONFIG", str(finer))
    monkeypatch.chdir(tmp_path)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_the_cache_and_config_directories_must_differ(tmp_path, monkeypatch):
    # Same reasoning as cache vs data: the collector deletes from the cache
    # by age, and the config dir holds the user's own files.
    same = tmp_path / "both"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CONFIG_DIR", str(same))
    app = App(name="acme", prog="acme", cache_dir=same)
    result = Runner(app).invoke("--list")
    assert result.exit_code == EX_USAGE
    assert "must differ" in result.stderr
    assert "ACME_CACHE_DIR" in result.stderr and "ACME_CONFIG_DIR" in result.stderr


def test_the_user_rung_merges_and_the_project_shadows(tmp_path, monkeypatch):
    # The cascade's outermost rung: personal tasks ride into a project, and
    # a project task shadows a same-named one — project > user, the
    # nearest-wins reading the cascade always had, one rung further out.
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef mine():\n    "Personal."\n\n'
        '@task\ndef ship():\n    "Shadowed by the project."\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "mine" in out  # the personal task is present in the project tree
    assert "Ship it." in out  # the project's ship wins the name
    assert "Shadowed by the project." not in out


def test_the_user_rung_claims_no_root(tmp_path, monkeypatch):
    # Outside a project, inv.root stays "" — footman invents no root — and
    # the cwd policies read the one-cwd rule: `root` exhausts the ladder to
    # the invocation directory, `taskfile` is the file's real home (the
    # config dir). The shipped fallback accidentally made the config dir the
    # root, which no personal task ever meant.
    import textwrap as _tw

    cfg, somewhere = tmp_path / "cfg", tmp_path / "somewhere"
    (cfg / "acme").mkdir(parents=True)
    somewhere.mkdir()
    (cfg / "acme" / "tasks.py").write_text(
        _tw.dedent(
            """
            import footman
            from footman import pre_tasks, task

            @pre_tasks
            def show(inv):
                print(f"root={inv.root!r}")

            @task(cwd="root")
            def where_root():
                print(f"root-cwd={footman.cwd()}")

            @task(cwd="taskfile")
            def where_file():
                print(f"file-cwd={footman.cwd()}")
            """
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(somewhere)
    acme = Runner(App(name="acme", prog="acme"))
    rooted = acme.invoke("where-root")
    assert rooted.ok, rooted.stderr
    assert "root=''" in rooted.stdout
    assert f"root-cwd={somewhere.resolve()}" in rooted.stdout
    filed = acme.invoke("where-file")
    assert f"file-cwd={(cfg / 'acme').resolve()}" in filed.stdout


def test_builtin_tasks_answer_where_nothing_else_does(tmp_path, monkeypatch):
    # Global mode: no project, no user file — the brand's built-ins are the
    # tree. `footman.docs` is a real installed entry point, so this is the
    # whole path: resolve, mount, list.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    out = acme.invoke("--list").stdout
    assert "docs" in out


def test_no_builtin_and_no_files_keeps_todays_refusal(tmp_path, monkeypatch):
    # `builtin=()` is byte-identical to before the feature existed.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    result = Runner(App(name="acme", prog="acme")).invoke("whatever")
    assert result.exit_code == EX_USAGE
    assert "no tasks file found" in result.stderr.lower()


def test_a_project_ignores_the_builtin_base(tmp_path, monkeypatch):
    # Nothing is privileged: inside a project, the tasks file mounts what
    # it wants and the base is simply not there.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    out = acme.invoke("--list").stdout
    assert "ship" in out and "docs" not in out


def test_an_unknown_builtin_teaches_the_mount(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    result = acme.invoke("docs.page")
    assert result.exit_code == EX_USAGE
    assert "no task named 'docs.page'" in result.stderr
    assert "built into acme via 'footman.docs'" in result.stderr
    assert "plugin('footman.docs')" in result.stderr


def test_the_user_rung_overlays_the_builtins(tmp_path, monkeypatch):
    # The bottom two rungs of project > user > built-in.
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef mine():\n    "Personal."\n'
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    out = acme.invoke("--list").stdout
    assert "mine" in out and "docs" in out


def test_an_uninstalled_builtin_refuses_naming_the_brand(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    result = Runner(App(name="acme", prog="acme", builtin=["acme.nope"])).invoke(
        "--list"
    )
    assert result.exit_code == EX_USAGE
    assert "acme declares built-in tasks from 'acme.nope'" in result.stderr


def test_the_plugins_report_shows_built_in(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    result = acme.invoke("--plugins")
    assert result.ok, result.stderr
    assert "built in" in result.stdout


def test_a_builtin_describes_itself_inside_a_project(tmp_path, monkeypatch):
    # In a project the built-in is not landed, so its line comes from the
    # tree it advertises — the brand vouches for importing its own — never
    # from the distribution summary repeated beside every sibling.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.new"]))
    result = acme.invoke("--plugins")
    assert result.ok, result.stderr
    assert "built in" in result.stdout
    assert "Scaffold a tasks file" in result.stdout


def test_two_projectless_directories_share_one_manifest(tmp_path, monkeypatch):
    # Hash-keyed by the brand, never the cwd: cold once per brand version,
    # not once per directory — and the file bakes no cwd (the collector's
    # idle sweep owns it) but does bake the builtin names the refresh child
    # rebuilds from.
    import json as _json

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    # Through the real door (`App.run` syncs manifests; the in-memory
    # `Runner` path deliberately writes nothing).
    app = App(name="acme", prog="acme", version="1.0", builtin=["footman.docs"])
    monkeypatch.chdir(a)
    assert app.run(["--list"]) == 0
    monkeypatch.chdir(b)
    assert app.run(["--list"]) == 0
    cache = tmp_path / ".cache" / "acme"
    manifests = [p for p in cache.glob("*.json") if not p.name.endswith(".times.json")]
    assert len(manifests) == 1
    assert manifests[0].name.startswith("global-")
    data = _json.loads(manifests[0].read_text(encoding="utf-8"))
    assert "cwd" not in data
    assert data["builtin"] == ["footman.docs"]


def test_the_brand_version_changes_the_global_key(tmp_path, monkeypatch):
    # An upgrade can change the tree, so the version is part of the key.
    empty = tmp_path / "e"
    empty.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.chdir(empty)
    one = App(name="acme", prog="acme", version="1.0", builtin=["footman.docs"])
    two = App(name="acme", prog="acme", version="2.0", builtin=["footman.docs"])
    assert one.run(["--list"]) == 0
    assert two.run(["--list"]) == 0
    cache = tmp_path / ".cache" / "acme"
    assert len(list(cache.glob("global-*.json"))) == 2


def test_tab_reads_the_global_manifest_outside_a_project(tmp_path, monkeypatch, capsys):
    # The hot path's one walk on a cwd-manifest miss: no project files means
    # the shared global manifest answers — the same file the run just wrote.
    empty = tmp_path / "e"
    empty.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.chdir(empty)
    app = App(name="acme", prog="acme", version="1.0", builtin=["footman.docs"])
    assert app.run(["--list"]) == 0  # writes the global manifest, warm read
    capsys.readouterr()  # drop the listing; the completion output is the test
    from footman._complete import complete_cli

    # `App.run` configured the brand's world and never restores (by design);
    # the autouse fixture restores after the test. The hot path walks once,
    # finds no project files, and serves the shared global manifest.
    assert complete_cli(["do"]) == 0
    assert "docs" in capsys.readouterr().out


def test_new_scaffolds_the_brands_file_and_the_scaffold_runs(tmp_path, monkeypatch):
    # `fm new`, brand-aware: writes the brand's own filename, teaches the
    # brand's own command, and what it writes genuinely loads and runs.
    empty = tmp_path / "e"
    empty.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.chdir(empty)
    app = App(
        name="acme", prog="acme", tasks_file="acmetasks.py", builtin=["footman.new"]
    )
    result = Runner(app).invoke("new")
    assert result.ok, result.stderr
    assert (empty / "acmetasks.py").is_file()
    assert "acme hello" in result.stdout
    ran = Runner(app).invoke("hello")
    assert ran.ok, ran.stderr
    assert "hello world" in ran.stdout


def test_new_mounted_in_a_project_refuses_to_overwrite(tmp_path, monkeypatch):
    # Inside a project the built-in is absent by design; the ordinary mount
    # offers it — and then a directory that already has its file is refused,
    # not clobbered.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from footman import plugin\n\nplugin("footman.new")\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    result = Runner(App(name="acme", prog="acme")).invoke("new")
    assert not result.ok
    assert "already exists" in result.stderr


def test_a_user_tasks_cwd_root_means_the_project_it_landed_in(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        "import footman\nfrom footman import task\n\n"
        '@task(cwd="root")\ndef whereami():\n    print(f"at={footman.cwd()}")\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    _project(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = Runner(App(name="acme", prog="acme")).invoke("whereami")
    assert result.ok, result.stderr
    assert f"at={tmp_path.resolve()}" in result.stdout


def test_child_argv_carries_the_resolved_locations(tmp_path):
    # Children inherit the environment but not the brand, so they are told
    # where the folders are rather than re-deriving them wrongly.
    cache, data = tmp_path / "c", tmp_path / "d"
    before = _paths.child_args()
    try:
        _paths.configure(
            prefix="ACME",
            cache_dir=cache,
            data_dir=data,
            config_name="acme",
            tasks_file="acme.py",
        )
        assert _paths.child_args() == [
            "ACME",
            str(cache),
            str(data),
            "acme",
            "acme.py",
            "fm",
            "",
            "",
        ]
        _paths.configure_child(*_paths.child_args())
        assert _paths.env_var("NO_GC") == "ACME_NO_GC"
        assert _paths.footman_cache_dir() == cache
        assert _paths.footman_data_dir() == data
        _paths.configure_child()  # empty words mean stock footman
        assert _paths.env_var("NO_GC") == "FOOTMAN_NO_GC"
        assert _paths.footman_cache_dir() == _paths.cache_home() / "footman"
    finally:
        _paths.configure_child(*before)
