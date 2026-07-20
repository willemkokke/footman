"""The cache collector: both rules, the rails, and the daily trigger."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from footman import _app, _gc, _paths


def _pair(cache: Path, stem: str, cwd: str | None, age_days: float = 0) -> None:
    """A manifest + times pair, optionally aged and optionally cwd-less."""
    manifest: dict = {"schema": 1, "hash": stem, "tree": {}}
    if cwd is not None:
        manifest["cwd"] = cwd
    (cache / f"{stem}.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cache / f"{stem}.times.json").write_text('{"schema": 1}', encoding="utf-8")
    if age_days:
        then = time.time() - age_days * 86400
        for name in (f"{stem}.json", f"{stem}.times.json"):
            os.utime(cache / name, (then, then))


def test_collect_deletes_pairs_whose_directory_is_gone(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "dead", str(tmp_path / "no-such-project"))
    _pair(cache, "alive", str(tmp_path))  # tmp_path exists: kept, any age
    removed = _gc.collect(cache)
    assert removed == 2
    assert not (cache / "dead.json").exists()
    assert not (cache / "dead.times.json").exists()
    assert (cache / "alive.json").exists()


def test_collect_ages_out_idle_pairs_without_a_cwd(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "old", None, age_days=_gc.IDLE_DAYS + 5)
    _pair(cache, "recent", None, age_days=1)
    _gc.collect(cache)
    assert not (cache / "old.json").exists()
    assert (cache / "recent.json").exists()


def test_collect_never_touches_the_invoking_pair_or_the_stamp(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "current", str(tmp_path / "gone"), age_days=400)
    (cache / _gc.STAMP).write_text("", encoding="utf-8")
    then = time.time() - 400 * 86400
    os.utime(cache / _gc.STAMP, (then, then))
    _gc.collect(cache, skip_stem="current")
    assert (cache / "current.json").exists()
    assert (cache / "current.times.json").exists()
    assert (cache / _gc.STAMP).exists()  # not a *.json; asserted anyway


def test_collect_ages_orphan_times_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for stem, days in (("stale", _gc.IDLE_DAYS + 5), ("warm", 1)):
        p = cache / f"{stem}.times.json"
        p.write_text('{"schema": 1}', encoding="utf-8")
        then = time.time() - days * 86400
        os.utime(p, (then, then))
    _gc.collect(cache)
    assert not (cache / "stale.times.json").exists()
    assert (cache / "warm.times.json").exists()


def test_collect_tolerates_garbage_manifests(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "junk.json").write_text("not json", encoding="utf-8")
    _gc.collect(cache)  # unreadable: judged by age alone, and it's fresh
    assert (cache / "junk.json").exists()


# --- the trigger --------------------------------------------------------------


def _trigger(tmp_path, monkeypatch, cfg=None):
    cache = tmp_path / "cache"
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    monkeypatch.delenv("FOOTMAN_NO_GC", raising=False)
    spawns: list[tuple[Path, str]] = []
    monkeypatch.setattr(_app, "_spawn_gc", lambda c, s: spawns.append((c, s)))
    _app._maybe_collect(cfg or {})
    return cache, spawns


def test_trigger_plants_the_stamp_on_a_fresh_cache(tmp_path, monkeypatch):
    cache, spawns = _trigger(tmp_path, monkeypatch)
    assert (cache / _gc.STAMP).exists()  # scheduled for tomorrow
    assert spawns == []  # short-lived caches never spawn


def test_trigger_spawns_once_the_stamp_has_aged(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    stamp = cache / _gc.STAMP
    stamp.touch()
    then = time.time() - 2 * 86400
    os.utime(stamp, (then, then))
    _, spawns = _trigger(tmp_path, monkeypatch)
    assert len(spawns) == 1
    assert stamp.stat().st_mtime > then + 86400  # re-touched before spawning


def test_trigger_respects_a_young_stamp(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / _gc.STAMP).touch()
    _, spawns = _trigger(tmp_path, monkeypatch)
    assert spawns == []


def test_trigger_off_switches(tmp_path, monkeypatch):
    _, spawns = _trigger(tmp_path, monkeypatch, cfg={"gc": False})
    assert spawns == []
    monkeypatch.setenv("FOOTMAN_NO_GC", "1")
    cache = tmp_path / "cache2"
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    _app._maybe_collect({})
    assert not cache.exists()  # fully off: not even a stamp


def test_collector_runs_for_real_as_a_detached_child(tmp_path, monkeypatch):
    """The collector end to end: the actual child `_maybe_collect` spawns,
    doing the actual deleting. Everything above this drives `collect()` in
    process or fakes the spawn — this is the only test that proves the
    spawned command line, `_gc.main()`'s argv handling, and the deletion
    all agree, and it is the reason CI ever executes the collector at all.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "dead", str(tmp_path / "no-such-project"))
    _pair(cache, "keep", str(tmp_path))  # a living project: must survive
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    stamp = cache / _gc.STAMP
    stamp.touch()
    then = time.time() - 2 * 86400  # yesterday's stamp: due for collection
    os.utime(stamp, (then, then))

    _app._maybe_collect({})  # spawns the real detached child

    deadline = time.time() + 30
    while (cache / "dead.json").exists() and time.time() < deadline:
        time.sleep(0.1)
    assert not (cache / "dead.json").exists()  # the child really collected
    assert not (cache / "dead.times.json").exists()
    assert (cache / "keep.json").exists()  # and left the living project alone
