"""`fm self.*`: the runner managing its own installation."""

from __future__ import annotations

import json

import pytest

from footman import _config, _paths
from footman.context import Result
from footman.tasks import self_

RECEIPT = """\
[tool]
requirements = [
    { name = "footman" },
    { name = "uv" },
    { name = "acme-devkit" },
]
"""


@pytest.fixture
def tool_env(tmp_path, monkeypatch):
    """A uv tools directory with footman installed and one package added."""
    env = tmp_path / "tools" / "footman"
    env.mkdir(parents=True)
    (env / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    monkeypatch.setattr(self_, "_tool_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(self_, "_uv", lambda: "/fake/uv")
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "config" / "config.toml"))
    return env


@pytest.fixture
def spawned(monkeypatch):
    """Every command the tasks would run, without running any of them."""
    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return Result(0, stdout="", stderr="", shown=" ".join(map(str, cmd)))

    monkeypatch.setattr(self_, "run", fake_run)
    return calls


# --- reading what uv recorded -------------------------------------------------


def test_extras_are_the_receipt_minus_what_the_runner_needs(tool_env):
    # The distribution is the thing being installed and `uv` is bundled so
    # the handoffs work without one on PATH — neither is yours to drop.
    assert self_._receipt_requirements() == ("footman", "uv", "acme-devkit")
    assert self_._extras() == ("acme-devkit",)
    assert self_._added() == ["acme-devkit"]  # exactly what completion offers


def test_no_receipt_reads_as_nothing_installed(tmp_path, monkeypatch):
    # The state `install` and `add` exist for, not an error.
    monkeypatch.setattr(self_, "_tool_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(self_, "_uv", lambda: "/fake/uv")
    assert self_._receipt_requirements() == ()
    assert self_._added() == []


# --- install / add / remove ---------------------------------------------------


def test_install_carries_your_packages_over(tool_env, spawned):
    # uv rewrites the environment from the requirements it is given, so an
    # upgrade that forgot the extras would silently drop them.
    self_.install()
    (cmd,) = [c for c in spawned if "install" in c]
    assert cmd[:5] == ["/fake/uv", "tool", "install", "--upgrade", "footman"]
    assert cmd.count("--with") == 2
    assert "uv" in cmd and "acme-devkit" in cmd


def test_add_unions_rather_than_replaces(tool_env, spawned):
    self_.add("second-pkg")
    (cmd,) = [c for c in spawned if "install" in c]
    assert "acme-devkit" in cmd  # what was already there
    assert "second-pkg" in cmd  # and what was asked for


def test_remove_drops_only_what_was_named(tool_env, spawned):
    self_.remove("acme-devkit")
    (cmd,) = [c for c in spawned if "install" in c]
    assert "acme-devkit" not in cmd
    assert "uv" in cmd  # the bundled one stays


def test_remove_refuses_a_package_that_was_never_added(tool_env, spawned):
    from footman.context import Failed

    with pytest.raises(Failed, match="was not added"):
        self_.remove("never-installed")
    assert not spawned  # nothing spawned on a refusal


def test_add_and_remove_want_a_name(tool_env, spawned):
    from footman.context import Failed

    with pytest.raises(Failed, match="at least one"):
        self_.add()
    with pytest.raises(Failed, match="at least one"):
        self_.remove()


# --- discovery ----------------------------------------------------------------


def test_auto_records_what_the_installed_env_advertises(tool_env, spawned, monkeypatch):
    monkeypatch.setattr(self_, "_candidates_in", lambda env: ("acme_devkit",))
    self_.add("acme-devkit")
    assert _config.discovered_builtin() == ("acme_devkit",)


def test_the_other_modes_leave_the_list_alone(tool_env, spawned, monkeypatch):
    monkeypatch.setattr(self_, "_candidates_in", lambda env: ("acme_devkit",))
    _config.write_discovered(["kept_by_hand"])
    config = _paths.footman_config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    for mode in ("manual", "internal", "none"):
        config.write_text(f'[builtins]\ndiscovery_mode = "{mode}"\n', encoding="utf-8")
        assert self_._rediscover() is None
        assert _config.discovered_builtin() == ("kept_by_hand",)


def test_candidates_come_from_the_installed_interpreter(tmp_path):
    # A tool environment is a different world from the process installing
    # into it, so its entry points are only knowable by asking its python.
    # No interpreter there yet: nothing advertised, never a crash.
    assert self_._candidates_in(tmp_path / "nothing") == ()


# --- uninstall ----------------------------------------------------------------


def test_uninstall_clears_the_leavings_but_keeps_your_config(tool_env, spawned, capsys):
    cache, data = _paths.footman_cache_dir(), _paths.footman_data_dir()
    for folder in (cache, data, _paths.footman_config_dir()):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "something").write_text("x", encoding="utf-8")

    self_.uninstall()
    assert [c for c in spawned if "uninstall" in c]
    assert not cache.exists() and not data.exists()
    assert _paths.footman_config_dir().exists()  # your writing survives
    assert "kept" in capsys.readouterr().out


def test_purge_takes_the_config_too(tool_env, spawned):
    config = _paths.footman_config_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text("", encoding="utf-8")
    self_.uninstall(purge=True)
    assert not config.exists()


# --- path ---------------------------------------------------------------------


def test_path_named_prints_one_bare_line(tool_env, capsys):
    self_.path("data")
    assert capsys.readouterr().out.strip() == str(_paths.footman_data_dir())


def test_path_takes_several(tool_env, capsys):
    self_.path("data", "config-file")
    printed = capsys.readouterr().out.strip().splitlines()
    assert printed == [
        str(_paths.footman_data_dir()),
        str(_paths.footman_config_file()),
    ]


def test_path_bare_answers_everything_and_returns_the_mapping(tool_env, capsys):
    places = self_.path()
    assert places is not None
    assert set(places) == {
        "cache",
        "data",
        "config-dir",
        "config-file",
        "user-tasks",
        "builtins",
        "project-root",
        "tool-dir",
    }
    out = capsys.readouterr().out
    for name in places:
        assert name in out


def test_a_location_that_does_not_apply_is_empty_not_absent(tool_env, capsys):
    # Outside a project `project-root` has no answer — but the question was
    # asked and answered, so a script reading the mapping finds the key.
    places = self_.path()
    assert places is not None
    assert "project-root" in places


def test_the_public_accessors_answer_the_same_places(tool_env):
    import footman

    assert footman.config_dir() == _paths.footman_config_dir()
    assert footman.config_file() == _paths.footman_config_file()
    assert footman.user_tasks_file() == _paths.user_tasks_file(_paths.tasks_file_name())


# --- the group as a CLI -------------------------------------------------------


def test_the_group_answers_inside_a_project(tmp_path, monkeypatch):
    # The whole reason built-ins joined the cascade: a self-management
    # command that vanished inside a checkout would be useless exactly
    # where you reach for it.
    from footman.app import App
    from footman.testing import Runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef build():\n    """Build."""\n'
    )
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    runner = Runner(App(dist="footman", builtin=("footman.self",)))
    listed = runner.invoke("--list", cwd=tmp_path)
    assert listed.ok, listed.stderr
    for verb in ("self.install", "self.add", "self.remove", "self.path"):
        assert verb in listed.stdout


def test_path_reports_through_json(tmp_path, monkeypatch):
    from footman.app import App
    from footman.testing import Runner

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    runner = Runner(App(dist="footman", builtin=("footman.self",)))
    result = runner.invoke("--json self.path", cwd=tmp_path)
    assert result.ok, result.stderr
    envelope = json.loads(result.stdout)
    (item,) = [i for i in envelope["items"] if i.get("task") == "self.path"]
    assert "cache" in item["returned"]
