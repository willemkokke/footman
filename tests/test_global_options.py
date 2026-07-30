"""A plugin's own global options: registration, parsing, value, completion."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Annotated

import pytest

from footman import _manifest as manifest
from footman import registry
from footman._complete import _DYNAMIC, _FILES, complete
from footman.params import suggest
from footman.registry import GlobalOption, Group, RegistrationError
from footman.testing import Runner


def _adopt(reg: Group, *args, **kwargs) -> GlobalOption:
    """Construct an option and move it onto *reg* — the test-local stand-in
    for the pull that carries a provider's contributions into the tree."""
    opt = GlobalOption(*args, **kwargs)
    registry.root.contributions["globals"].remove(opt)
    reg.contributions["globals"].append(opt)
    return opt


def test_a_global_option_parses_freezes_and_answers(tmp_path):
    # The real path, cascade and all: constructing the option in a tasks
    # file registers it; the leading global binds it; the task reads it.
    src = tmp_path / "tasks.py"
    src.write_text(
        textwrap.dedent(
            """
            from typing import Literal
            from footman import GlobalOption, task

            MODE = GlobalOption(
                "lint-mode", Literal["loose", "strict"],
                default="loose", help="how hard to lint",
            )
            AUDIT = GlobalOption("audit", help="report, change nothing")

            @task(uses=[MODE])
            def build():
                print(f"mode={MODE.value} audit={AUDIT.value}")
            """
        )
    )
    result = Runner().invoke("--lint-mode=strict build", tasks=src)
    assert result.ok, result.stderr
    assert "mode=strict audit=False" in result.stdout

    result = Runner().invoke("--audit build", tasks=src)
    assert result.ok, result.stderr
    assert "mode=loose audit=True" in result.stdout  # defaults fill the rest


def test_a_bad_value_is_a_taught_refusal():
    reg = Group("root")
    _adopt(reg, "level", int, default=1)

    @reg.task
    def build(): ...

    result = Runner().invoke("--level=deep build", tasks=reg)
    assert not result.ok
    assert "--level" in result.stderr


def test_an_unpulled_option_is_an_unknown_global():
    reg = Group("root")  # nothing pulled: the name reaches no run

    @reg.task
    def build(): ...

    result = Runner().invoke("--lint-mode=strict build", tasks=reg)
    assert not result.ok
    assert "unknown global option --lint-mode" in result.stderr


def test_a_core_name_collision_is_refused_naming_the_owner():
    reg = Group("root")
    _adopt(reg, "jobs", int, default=1)

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "--jobs" in result.stderr
    assert "footman's own" in result.stderr


def test_two_plugins_one_name_is_refused_naming_both():
    reg = Group("root")
    first = _adopt(reg, "cache-dir", Path)
    second = _adopt(reg, "cache-dir", Path)
    first.owner, second.owner = "acme.devkit", "other.kit"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "acme.devkit" in result.stderr and "other.kit" in result.stderr


def test_an_undeclared_read_is_noted_a_declared_one_is_not():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def declared():
        assert opt.value == "eu"

    @reg.task
    def sneaky():
        assert opt.value == "eu"

    result = Runner().invoke("declared", tasks=reg)
    assert result.ok
    assert "uses=" not in result.stderr  # declared: no note

    result = Runner().invoke("sneaky", tasks=reg)
    assert result.ok
    assert "reads --region without declaring it" in result.stderr


def test_uses_takes_only_singletons():
    reg = Group("root")
    with pytest.raises(RegistrationError, match="GlobalOption singletons"):

        @reg.task(uses=["--region"])  # type: ignore[list-item]
        def build(): ...


def test_reading_outside_a_run_is_taught():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")
    with pytest.raises(RuntimeError, match="inside a task or lifecycle hook"):
        _ = opt.value  # the read is the assertion


def test_the_manifest_bakes_globals_and_uses(tmp_path):
    reg = Group("root")
    opt = _adopt(
        reg,
        "env-file",
        Path,
        help="load this .env file",
    )
    opt.owner = "footman.env_files"

    @reg.task(uses=[opt])
    def build(): ...

    data = manifest.build_manifest(reg)
    assert data["schema"] == 2
    (entry,) = data["tree"]["globals"]
    assert entry["name"] == "env-file"
    assert entry["kind"] == "option"
    assert "path" in entry["types"]  # typed: completion hands off to files
    assert entry["help"] == "load this .env file"
    assert entry["owner"] == "footman.env_files"
    assert data["tree"]["tasks"]["build"]["uses"] == ["env-file"]


def test_completion_offers_and_completes_plugin_globals():
    from typing import Literal

    reg = Group("root")
    _adopt(reg, "lint-mode", Literal["loose", "strict"], default="loose")
    _adopt(reg, "env-file", Path)

    @reg.task
    def build(): ...

    tree = manifest.build_manifest(reg)["tree"]
    names = complete(tree, ["--l"])
    assert "--lint-mode" in names
    values = complete(tree, ["--lint-mode=s"])
    assert values == ["--lint-mode=strict"] or values == ["strict"]
    assert complete(tree, ["--env-file=x"]) == [_FILES]


def test_the_json_catalog_carries_the_globals():
    reg = Group("root")
    _adopt(reg, "audit", help="report, change nothing")

    @reg.task
    def build(): ...

    result = Runner().invoke("--json", tasks=reg)
    assert result.ok, result.stderr
    envelope = json.loads(result.stdout)
    (entry,) = envelope["tree"]["globals"]
    assert entry["name"] == "audit" and entry["kind"] == "flag"


# --- dynamic completion: a global's suggest() recomputes fresh ----------------


def _demo_targets():
    return ["prod", "preview"]


def test_a_dynamic_global_signals_recompute():
    reg = Group("root")
    _adopt(reg, "target", Annotated[str, suggest(_demo_targets)], default="")

    @reg.task
    def build(): ...

    tree = manifest.build_manifest(reg)["tree"]
    # the value is dynamic → defer to a fresh recompute, carrying the partial,
    # the emission prefix (whole-token shells re-attach `--target=`; bash
    # completes the bare value) and the option's name — no segment path: an
    # empty path is what addresses a global.
    assert complete(tree, ["--target=p"]) == [_DYNAMIC, "p", "--target=", "target"]
    assert complete(tree, ["--target", "=", "p"]) == [_DYNAMIC, "p", "", "target"]


def test_fresh_dynamic_addresses_a_global_by_name(monkeypatch):
    import subprocess

    from footman import _complete

    captured: dict[str, list[str]] = {}

    def ok(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "prod\npreview\n", "")

    monkeypatch.setattr(_complete.subprocess, "run", ok)
    assert _complete._fresh_dynamic("target", [], ["--target=", ""]) == [
        "prod",
        "preview",
    ]
    cmd = captured["cmd"]
    assert cmd[cmd.index("--global") + 1] == "target"
    assert "--param" not in cmd and "--path" not in cmd


def test_suggest_answers_a_global_by_name(tmp_path, monkeypatch):
    from footman import _suggest

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            from typing import Annotated

            from footman import GlobalOption, task
            from footman.params import suggest

            def _targets():
                return Path("targets.txt").read_text().split()

            TARGET = GlobalOption(
                "target", Annotated[str, suggest(_targets)], default=""
            )
            AUDIT = GlobalOption("audit")

            @task
            def build(): ...
            """
        )
    )
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("prod preview\n")
    assert _suggest._global_values("target", {}) == ["prod", "preview"]
    # a miss — no completer, or no such option — is empty, never an error
    assert _suggest._global_values("audit", {}) == []
    assert _suggest._global_values("ghost", {}) == []


# --- the wiring advisories ----------------------------------------------------


def test_an_orphan_global_is_warned_and_a_wired_one_is_not():
    reg = Group("root")
    _adopt(reg, "dead-switch", help="nothing reads this")
    (warning,) = registry.orphan_global_options(reg)
    assert "--dead-switch" in warning and "no lifecycle hook" in warning

    used = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[used])
    def build(): ...

    warnings = registry.orphan_global_options(reg)
    assert len(warnings) == 1  # region is declared; dead-switch still orphaned

    def hook(inv): ...

    reg.contributions["pre_tasks"].append(hook)  # the owner now contributes
    assert registry.orphan_global_options(reg) == []


def test_an_orphan_global_warns_on_the_run():
    reg = Group("root")
    _adopt(reg, "dead-switch")

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert "warning: --dead-switch" in result.stderr


def test_a_declared_unread_global_is_advised_only_under_verbose():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def build():
        print("built")

    quiet = Runner().invoke("build", tasks=reg)
    assert quiet.ok and "never read it" not in quiet.stderr
    loud = Runner().invoke("--verbose build", tasks=reg)
    assert loud.ok, loud.stderr
    assert "declares --region in uses= but never read it" in loud.stderr


def test_a_declared_and_read_global_is_not_advised():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def build():
        assert opt.value == "eu"

    result = Runner().invoke("--verbose build", tasks=reg)
    assert result.ok, result.stderr
    assert "never read it" not in result.stderr


# --- help shows the declaration ----------------------------------------------


def test_task_help_lists_declared_globals():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")
    opt.owner = "acme.devkit"

    @reg.task(uses=[opt])
    def deploy(): ...

    result = Runner().invoke("--help deploy", tasks=reg)
    assert result.ok, result.stderr
    assert "reads --region (from acme.devkit)" in result.stdout
