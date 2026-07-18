"""Signature introspection, manifest caching, and staleness."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import pytest

from footman import _paths, manifest


def specs(fn):
    sig = manifest.resolved_signature(fn)
    return [manifest.param_spec(p) for p in sig.parameters.values()]


def test_flag():
    def f(x: bool = False): ...

    assert specs(f) == [{"name": "x", "kind": "flag"}]


def test_kwargs_is_a_spec_error():
    def f(**opts): ...

    with pytest.raises(manifest.SpecError, match=r"\*\*opts"):
        specs(f)


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

    assert specs(g) == [{"name": "opt", "kind": "option"}]
    assert specs(h) == [{"name": "req", "kind": "argument"}]


def test_typed_option():
    def f(n: int = 3, ratio: float = 1.0): ...

    assert specs(f) == [
        {"name": "n", "kind": "option", "types": ["int"]},
        {"name": "ratio", "kind": "option", "types": ["float"]},
    ]


def test_literal_choices_positional():
    def f(env: Literal["a", "b"]): ...

    assert specs(f) == [{"name": "env", "kind": "argument", "choices": ["a", "b"]}]


def test_repeatable_path_option():
    def f(paths: list[Path] | None = None): ...

    assert specs(f) == [
        {"name": "paths", "multiple": True, "kind": "option", "types": ["path"]}
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

    assert specs(f) == [{"name": "x", "kind": "option", "types": ["int"]}]
    assert specs(g) == [{"name": "y", "kind": "option", "types": ["int"]}]


def test_build_manifest_shape(tree):
    assert "check" in tree["tasks"]
    assert set(tree["groups"]) >= {"docs", "db", "docker", "workspace"}
    lint = tree["tasks"]["lint"]
    assert lint["help"] == "Run ruff over the project."
    kinds = {p["name"]: p["kind"] for p in lint["params"]}
    assert kinds == {"fix": "flag", "mode": "option", "paths": "option"}


def test_write_load_roundtrip(root, tmp_path):
    m = manifest.build_manifest(root)
    path = tmp_path / "manifest.json"
    manifest.write_manifest(m, path)
    assert manifest.load_manifest(path) == m


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
