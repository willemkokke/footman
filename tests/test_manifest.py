"""Signature introspection, manifest caching, and staleness."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Literal, Optional

import pytest

from footman import _paths, manifest
from footman.params import doc


def specs(fn):
    sig = manifest.resolved_signature(fn)
    return [manifest.param_spec(p) for p in sig.parameters.values()]


def test_flag():
    def f(x: bool = False): ...

    assert specs(f) == [{"name": "x", "default": False, "kind": "flag"}]


def test_doc_marker_lands_in_spec():
    def f(fix: Annotated[bool, doc("apply fixes in place")] = False): ...

    assert specs(f) == [
        {"name": "fix", "default": False, "kind": "flag", "doc": "apply fixes in place"}
    ]


def node(fn):
    """Build one task's manifest node the way the real tree does."""
    from footman import registry

    with registry.capture() as root:
        registry.task(fn)
    return manifest.build_manifest(root)["tree"]["tasks"][fn.__name__.replace("_", "-")]


def test_docstring_params_fill_doc():
    def deploy(target: str, fix: bool = False):
        """Deploy.

        Args:
            target: where to deploy
            fix: apply fixes in place
        """

    by_name = {p["name"]: p for p in node(deploy)["params"]}
    assert by_name["target"]["doc"] == "where to deploy"
    assert by_name["fix"]["doc"] == "apply fixes in place"


def test_doc_marker_beats_docstring():
    def lint(fix: Annotated[bool, doc("the marker text")] = False):
        """Lint.

        Args:
            fix: the docstring text
        """

    (p,) = node(lint)["params"]
    assert p["doc"] == "the marker text"


def test_docstring_long_lands_in_node():
    def build():
        """Build.

        The long story,
        over two lines.
        """

    n = node(build)
    assert n["help"] == "Build."
    assert n["long"] == "The long story,\nover two lines."


def test_docstring_long_absent_when_empty():
    def plain():
        "Just the one line."

    assert "long" not in node(plain)


def test_docstring_unknown_param_warns():
    def run_it(a: int = 0):
        """Run.

        Args:
            a: real
            ghost: not a parameter
        """

    with pytest.warns(UserWarning, match="ghost"):
        node(run_it)


def test_numpy_and_sphinx_docstrings_reach_the_spec():
    def np_style(a: int = 0):
        """S.

        Parameters
        ----------
        a : int
            From numpy.
        """

    def sp_style(a: int = 0):
        """S.

        :param a: from sphinx
        """

    assert node(np_style)["params"][0]["doc"] == "From numpy."
    assert node(sp_style)["params"][0]["doc"] == "from sphinx"


def test_kwargs_is_a_spec_error():
    def f(**opts): ...

    with pytest.raises(manifest.SpecError, match=r"\*\*opts"):
        specs(f)


def test_help_option_is_a_reserved_name():
    # A flag or option named `help` would map to --help, which footman
    # intercepts anywhere on the line — so it can never bind. Reject it loudly
    # at load time rather than silently shadowing the parameter.
    def with_flag(help: bool = False): ...

    def with_option(help: str = ""): ...

    for fn in (with_flag, with_option):
        with pytest.raises(manifest.SpecError, match=r"'help' is a reserved"):
            node(fn)


def test_help_is_allowed_when_it_is_not_an_option():
    # A required positional <help> and a variadic *help never produce --help,
    # so they stay legal — the reservation is precise, not a blanket ban.
    def positional(help: str): ...

    def variadic(*help: str): ...

    assert node(positional)["params"][0]["kind"] == "argument"
    assert node(variadic)["params"][0]["kind"] == "variadic"


def test_no_default_dict_is_a_required_option():
    def f(vars: dict[str, str]): ...

    assert specs(f) == [
        {"name": "vars", "kind": "option", "mapping": True, "required": True}
    ]


def test_no_default_bool_is_a_required_flag():
    def f(prod: bool): ...

    assert specs(f) == [{"name": "prod", "kind": "flag", "required": True}]


def test_str_option_and_required_argument():
    def g(opt: str = "a"): ...

    def h(req): ...

    assert specs(g) == [{"name": "opt", "default": "a", "kind": "option"}]
    assert specs(h) == [{"name": "req", "kind": "argument"}]


def test_keyword_only_without_default_is_a_required_option():
    # Python's `*` already says "must be named" — the grammar honours it,
    # the same shape defaultless dicts and flags take.
    def f(*, out: Path): ...

    def g(*args: str, dest: str): ...

    def h(*, plain): ...  # un-annotated keyword-only: same rule

    assert specs(f) == [
        {"name": "out", "kind": "option", "required": True, "types": ["path"]}
    ]
    assert specs(g)[1] == {"name": "dest", "kind": "option", "required": True}
    assert specs(h) == [{"name": "plain", "kind": "option", "required": True}]


def test_keyword_only_with_default_stays_a_plain_option():
    def f(*args: str, title: str = ""): ...

    assert specs(f)[1] == {"name": "title", "default": "", "kind": "option"}


def test_typed_option():
    def f(n: int = 3, ratio: float = 1.0): ...

    assert specs(f) == [
        {"name": "n", "default": 3, "kind": "option", "types": ["int"]},
        {"name": "ratio", "default": 1.0, "kind": "option", "types": ["float"]},
    ]


def test_literal_choices_positional():
    def f(env: Literal["a", "b"]): ...

    assert specs(f) == [{"name": "env", "kind": "argument", "choices": ["a", "b"]}]


def test_repeatable_path_option():
    def f(paths: list[Path] | None = None): ...

    assert specs(f) == [
        {
            "name": "paths",
            "default": None,
            "multiple": True,
            "kind": "option",
            "types": ["path"],
        }
    ]


def test_variadic():
    def f(*cmd: str): ...

    assert specs(f) == [{"name": "cmd", "kind": "variadic"}]


def test_underscore_becomes_hyphen():
    def f(fail_under: int = 80): ...

    assert specs(f)[0]["name"] == "fail-under"


def test_optional_is_unwrapped():
    def f(x: Optional[int] = None): ...  # noqa: UP045 - exercises typing.Optional

    def g(y: int | None = None): ...

    assert specs(f) == [
        {"name": "x", "default": None, "kind": "option", "types": ["int"]}
    ]
    assert specs(g) == [
        {"name": "y", "default": None, "kind": "option", "types": ["int"]}
    ]


def test_build_manifest_shape(tree):
    assert "check" in tree["tasks"]
    assert set(tree["groups"]) >= {"docs", "db", "docker", "workspace"}
    lint = tree["tasks"]["lint"]
    assert lint["help"] == "Run ruff over the project."
    kinds = {p["name"]: p["kind"] for p in lint["params"]}
    assert kinds == {"fix": "flag", "mode": "option", "paths": "option"}


def test_footman_cache_dir_overrides_every_cache_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert _paths.manifest_path(tmp_path).parent == tmp_path / "elsewhere"
    assert _paths.times_path(tmp_path).parent == tmp_path / "elsewhere"
    assert _paths.times_path(tmp_path).name.endswith(".times.json")


def test_write_load_roundtrip(root, tmp_path):
    m = manifest.build_manifest(root)
    path = tmp_path / "manifest.json"
    manifest.write_manifest(m, path)
    assert manifest.load_manifest(path) == m


def test_write_retries_when_a_reader_holds_the_destination(root, tmp_path, monkeypatch):
    """Windows refuses to replace a file another process has open, and a
    reader holding it open is the design: completion polls this manifest
    every few milliseconds while a detached refresh rewrites it. Losing that
    race silently means the rebuild never lands (the child swallows its
    errors), so the write retries instead."""
    m = manifest.build_manifest(root)
    path = tmp_path / "manifest.json"
    real_replace = os.replace
    denials = [PermissionError(5, "being used by another process")] * 3

    def flaky(src, dst):
        if denials:
            raise denials.pop()
        return real_replace(src, dst)

    monkeypatch.setattr(manifest, "_REPLACE_PAUSE", 0)
    monkeypatch.setattr(os, "replace", flaky)
    manifest.write_manifest(m, path)
    assert manifest.load_manifest(path) == m
    assert not denials  # every denial was actually met with a retry


def test_write_gives_up_without_littering_the_cache(root, tmp_path, monkeypatch):
    """A reader that never lets go must not leave `<name>.<pid>.tmp` behind:
    nothing ever reads those, and the cache directory is not a graveyard."""
    m = manifest.build_manifest(root)
    path = tmp_path / "manifest.json"

    def always_denied(src, dst):
        raise PermissionError(5, "being used by another process")

    monkeypatch.setattr(manifest, "_REPLACE_PAUSE", 0)
    monkeypatch.setattr(os, "replace", always_denied)
    with pytest.raises(PermissionError):
        manifest.write_manifest(m, path)
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_missing_or_corrupt_returns_none(tmp_path):
    assert manifest.load_manifest(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert manifest.load_manifest(bad) is None


def test_sync_rewrites_only_on_hash_change(root, tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    writes = []
    real_write = manifest.write_manifest
    monkeypatch.setattr(
        manifest,
        "write_manifest",
        lambda m, p: writes.append(p) or real_write(m, p),
    )

    manifest.sync_manifest(root, project)
    manifest.sync_manifest(root, project)  # identical tree -> no rewrite
    assert len(writes) == 1

    @root.task
    def brand_new_task():  # changes the hash
        """A new task."""

    manifest.sync_manifest(root, project)
    assert len(writes) == 2


def test_sync_bakes_the_cwd_and_upgrades_manifests_without_it(
    root, tmp_path, monkeypatch
):
    # The collector's gone-directory rule reads it; pre-existing manifests
    # lacking the key are rewritten once so they gain it.
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    manifest.sync_manifest(root, project)
    path = _paths.manifest_path(project)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cwd"] == str(project)

    del data["cwd"]  # simulate a manifest from before the key existed
    path.write_text(json.dumps(data), encoding="utf-8")
    manifest.sync_manifest(root, project)  # same tree, but cwd-less: rewrite
    fresh = json.loads(path.read_text(encoding="utf-8"))
    assert fresh["cwd"] == str(project)


def test_infinite_task_carries_the_note_key():
    from footman import registry

    with registry.capture() as root:

        @registry.task(infinite=True)
        def serve():
            "Serve forever."

        @registry.task
        def plain():
            "Ends."

    tree = manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["serve"]["infinite"] is True
    assert "infinite" not in tree["tasks"]["plain"]  # additive: absent means no


def test_confirm_and_interactive_carry_note_keys():
    from footman import registry

    with registry.capture() as root:

        @registry.task(confirm="to prod?", interactive=True)
        def deploy():
            "Deploy."

        @registry.task
        def plain():
            "Plain."

    tree = manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["deploy"]["interactive"] is True
    assert tree["tasks"]["deploy"]["confirm"] == "to prod?"
    assert "interactive" not in tree["tasks"]["plain"]
    assert "confirm" not in tree["tasks"]["plain"]


def test_serial_and_exclusive_carry_the_lane_key():
    from footman import registry

    with registry.capture() as root:

        @registry.task(serial=True)
        def legacy():
            "Owns the globals."

        @registry.task(exclusive=True)
        def bench():
            "Owns the machine."

        @registry.task
        def plain():
            "Plain."

    tree = manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["legacy"]["lane"] == "serial"
    assert tree["tasks"]["bench"]["lane"] == "exclusive"
    assert "lane" not in tree["tasks"]["plain"]
