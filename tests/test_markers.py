"""The validation markers: exists/isfile/isdir, between/range, env(), check()."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest

from footman import manifest
from footman.executor import run_chain
from footman.params import between, check, env, exists, isdir, isfile
from footman.registry import Group
from footman.split import ChainError, split_chain


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


# --- path requirements (eager) -------------------------------------------------


def test_isfile_accepts_a_real_file(tmp_path):
    real = tmp_path / "cfg.toml"
    real.write_text("x = 1\n")
    seen = {}

    def tasks(reg):
        @reg.task
        def render(template: Annotated[Path, isfile]):
            seen["t"] = template

    run(tasks, f"render {real}")
    assert seen["t"] == real


def test_path_requirements_teach_eagerly(tmp_path):
    # Markers must be module-level names: `from __future__ import annotations`
    # stringifies these, and eval_str resolves them in module globals.
    def tasks(reg):
        @reg.task
        def rm(target: Annotated[Path, exists]): ...

        @reg.task
        def render(template: Annotated[Path, isfile]): ...

        @reg.task
        def clean(build_dir: Annotated[Path, isdir]): ...

    _, tree = build_tree(tasks)
    missing = str(tmp_path / "missing")
    with pytest.raises(ChainError, match="existing path"):
        split_chain(tree, ["rm", missing])
    with pytest.raises(ChainError, match="existing file"):
        split_chain(tree, ["render", missing])
    with pytest.raises(ChainError, match="existing directory"):
        split_chain(tree, ["clean", missing])


def test_isdir_rejects_a_file(tmp_path):
    f = tmp_path / "afile"
    f.write_text("")

    def tasks(reg):
        @reg.task
        def clean(build_dir: Annotated[Path, isdir]): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="existing directory"):
        split_chain(tree, ["clean", str(f)])


# --- numeric bounds (eager) ------------------------------------------------------


def test_between_bounds_are_inclusive():
    seen = {}

    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32)] = 4):
            seen["jobs"] = jobs

    run(tasks, "test- --jobs 32")
    assert seen["jobs"] == 32
    run(tasks, "test- --jobs 1")
    assert seen["jobs"] == 1


def test_between_teaches_out_of_range():
    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32)] = 4): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 1 and 32"):
        split_chain(tree, ["test-", "--jobs", "99"])


def test_bare_range_is_half_open():
    def tasks(reg):
        @reg.task
        def shard(index: Annotated[int, range(0, 8)] = 0): ...

    _, tree = build_tree(tasks)
    split_chain(tree, ["shard", "--index", "7"])  # ok: 0..7
    with pytest.raises(ChainError, match="between 0 and 7"):
        split_chain(tree, ["shard", "--index", "8"])


def test_open_ended_bound():
    def tasks(reg):
        @reg.task
        def retry(times: Annotated[int, between(1, None)] = 1): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="at least 1"):
        split_chain(tree, ["retry", "--times", "0"])


def test_bounds_apply_to_positionals_and_lists():
    def tasks(reg):
        @reg.task
        def pick(shards: Annotated[list[int], between(0, 3)] | None = None): ...

    _, tree = build_tree(tasks)
    split_chain(tree, ["pick", "--shards", "0,3"])
    with pytest.raises(ChainError, match="between 0 and 3"):
        split_chain(tree, ["pick", "--shards", "0,4"])


# --- env() fallback (late, validated) --------------------------------------------


def test_env_fallback_precedence(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(target: Annotated[str, env("DEPLOY_ENV")] = "staging"):
            seen["target"] = target

    run(tasks, "deploy")
    assert seen["target"] == "staging"  # no env, no flag -> default

    monkeypatch.setenv("DEPLOY_ENV", "prod")
    run(tasks, "deploy")
    assert seen["target"] == "prod"  # env beats default

    run(tasks, "deploy --target edge")
    assert seen["target"] == "edge"  # CLI beats env


def test_env_value_is_coerced_and_bounded(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32), env("JOBS")] = 4):
            seen["jobs"] = jobs

    monkeypatch.setenv("JOBS", "8")
    run(tasks, "test-")
    assert seen["jobs"] == 8  # coerced to int

    monkeypatch.setenv("JOBS", "99")
    results = run(tasks, "test-")
    assert not results[0].ok  # bounds enforced for env values too
    assert "between 1 and 32" in str(results[0].error)


def test_env_list_comma_splits(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: Annotated[list[str], env("TAGS")] | None = None):
            seen["tags"] = tags

    monkeypatch.setenv("TAGS", "a,b,c")
    run(tasks, "build")
    assert seen["tags"] == ["a", "b", "c"]


def test_env_without_default_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def deploy(target: Annotated[str, env("DEPLOY_ENV")]): ...

    with pytest.raises(manifest.SpecError, match="needs a default"):
        build_tree(tasks)


def test_env_on_dict_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def deploy(opts: Annotated[dict[str, str], env("OPTS")] | None = None): ...

    with pytest.raises(manifest.SpecError, match="not supported on dict"):
        build_tree(tasks)


# --- check() validators (late) ----------------------------------------------------


def _semver(value: str) -> None:
    parts = value.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{value!r} is not MAJOR.MINOR.PATCH")


def test_check_accepts_and_rejects():
    seen = {}

    def tasks(reg):
        @reg.task
        def tag(version: Annotated[str, check(_semver)] = "0.0.0"):
            seen["v"] = version

    run(tasks, "tag --version 1.2.3")
    assert seen["v"] == "1.2.3"

    results = run(tasks, "tag --version nope")
    assert not results[0].ok
    assert "is not MAJOR.MINOR.PATCH" in str(results[0].error)


def test_check_runs_per_element():
    def tasks(reg):
        @reg.task
        def tag(versions: Annotated[list[str], check(_semver)] | None = None): ...

    results = run(tasks, "tag --versions 1.2.3,bad")
    assert not results[0].ok
    assert "bad" in str(results[0].error)


def test_check_applies_to_env_values(monkeypatch):
    def tasks(reg):
        @reg.task
        def tag(version: Annotated[str, check(_semver), env("VERSION")] = "0.0.0"): ...

    monkeypatch.setenv("VERSION", "not-semver")
    results = run(tasks, "tag")
    assert not results[0].ok
    assert "VERSION" in str(results[0].error)


# --- manifest spec keys are additive ----------------------------------------------


def test_marker_manifest_keys():
    def tasks(reg):
        @reg.task
        def deploy(
            config: Annotated[Path, isfile],
            jobs: Annotated[int, between(1, 32)] = 4,
            target: Annotated[str, env("DEPLOY_ENV")] = "staging",
            version: Annotated[str, check(_semver)] = "0.0.0",
        ): ...

    _, tree = build_tree(tasks)
    by_name = {p["name"]: p for p in tree["tasks"]["deploy"]["params"]}
    assert by_name["config"]["path"] == "file"
    assert by_name["jobs"]["min"] == 1 and by_name["jobs"]["max"] == 32
    assert by_name["target"]["env"] == "DEPLOY_ENV"
    assert "check" not in by_name["version"]  # functions never serialize


# --- opaque annotations warn -------------------------------------------------------


def test_unresolvable_annotation_warns():
    def tasks(reg):
        def go(x="d"): ...

        # Set post-hoc so no linter trips: the string never resolves to a
        # type, exactly like a typo'd annotation under PEP 563.
        go.__annotations__ = {"x": "NoSuchType"}
        reg.task(go)

    with pytest.warns(UserWarning, match="not a usable type"):
        build_tree(tasks)
