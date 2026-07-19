"""Duration history, the estimator, and (further down) the status line."""

from __future__ import annotations

from pathlib import Path

from footman import _paths, _progress
from footman.split import Segment

# --- estimator ----------------------------------------------------------------


def test_too_few_samples_is_indeterminate():
    assert _progress.estimate([]) is None
    assert _progress.estimate([4.0, 4.1, 3.9, 4.2]) is None  # 4 < MIN_SAMPLES


def test_tight_history_is_determinate():
    est = _progress.estimate([4.0, 4.1, 3.9, 4.2, 4.0, 4.1])
    assert est is not None
    assert 3.9 <= est.typical <= 4.2  # ~p50
    assert est.scale >= est.typical  # bar fills against the high quantile


def test_long_right_tail_is_indeterminate():
    # A task that sometimes takes 4s and sometimes 40s has no honest bar.
    assert _progress.estimate([4.0, 4.1, 3.9, 4.2, 40.0, 4.1, 39.0]) is None


def test_zero_durations_are_indeterminate():
    assert _progress.estimate([0.0] * 10) is None


# --- chain key ----------------------------------------------------------------


def _seg(**kw) -> Segment:
    base = {"task": "test", "path": ["test"]}
    return Segment(**{**base, **kw})


def test_chain_key_is_stable_and_shape_sensitive():
    a = _progress.chain_key([_seg()], sequential=False, jobs=4)
    assert a == _progress.chain_key([_seg()], sequential=False, jobs=4)  # stable
    assert a != _progress.chain_key([_seg()], sequential=True, jobs=4)  # -s differs
    assert a != _progress.chain_key([_seg()], sequential=False, jobs=2)  # -j too
    assert a != _progress.chain_key(
        [_seg(passthrough=["-k", "one"])], sequential=False, jobs=4
    )  # passthrough is part of the shape
    assert a != _progress.chain_key(
        [_seg(values={"fix": True})], sequential=False, jobs=4
    )  # values too


# --- store --------------------------------------------------------------------


def test_record_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    _progress.record(tmp_path, "abc", 4.2)
    _progress.record(tmp_path, "abc", 4.0)
    assert _progress.load_runs(tmp_path, "abc") == [4.2, 4.0]
    assert _progress.load_runs(tmp_path, "other") == []


def test_window_caps_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    for i in range(_progress.WINDOW + 7):
        _progress.record(tmp_path, "k", float(i))
    runs = _progress.load_runs(tmp_path, "k")
    assert len(runs) == _progress.WINDOW
    assert runs[-1] == float(_progress.WINDOW + 6)  # newest kept


def test_corrupt_store_reads_empty_and_heals_on_write(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    path = _paths.times_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert _progress.load_runs(tmp_path, "k") == []
    _progress.record(tmp_path, "k", 1.0)  # doesn't raise; rewrites clean
    assert _progress.load_runs(tmp_path, "k") == [1.0]


def test_unwritable_store_never_raises(tmp_path, monkeypatch):
    # The cache dir path is a *file*: mkdir/write must fail — silently.
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / "blocked")
    (tmp_path / "blocked").write_text("i am a file")
    _progress.record(tmp_path, "k", 1.0)  # best-effort by contract


def test_idle_chains_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    _progress.record(tmp_path, "old", 1.0)
    # Age the entry beyond the horizon by editing the store directly.
    path = _paths.times_path(tmp_path)
    import json

    data = json.loads(path.read_text())
    data["chains"]["old"]["last"] = 1.0  # 1970: long idle
    path.write_text(json.dumps(data))
    _progress.record(tmp_path, "fresh", 2.0)  # write triggers the prune
    assert _progress.load_runs(tmp_path, "old") == []
    assert _progress.load_runs(tmp_path, "fresh") == [2.0]


def test_key_count_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    for i in range(_progress.MAX_KEYS + 5):
        _progress.record(tmp_path, f"k{i}", 1.0)
    import json

    data = json.loads(_paths.times_path(tmp_path).read_text())
    assert len(data["chains"]) == _progress.MAX_KEYS


def test_store_lives_beside_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    times = _paths.times_path(Path.cwd())
    manifest = _paths.manifest_path(Path.cwd())
    assert times.parent == manifest.parent
    assert times.stem.startswith(manifest.stem)


# --- the status line ----------------------------------------------------------

import io  # noqa: E402


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_fmt_secs():
    assert _progress.fmt_secs(4.06) == "4.1s"
    assert _progress.fmt_secs(42.4) == "42s"
    assert _progress.fmt_secs(70) == "1m10s"


def test_status_line_paints_clears_and_respects_the_column():
    err = _Tty()
    st = _progress.StatusLine(err, None, color=False)
    st.unit_added(2)
    st.unit_started("alpha")
    first = err.getvalue()
    assert "\r\x1b[K[" in first and "alpha" in first  # painted a frame

    st.notify("partial")  # someone is mid-line on the terminal
    assert err.getvalue().endswith("\x1b[K")  # cleared, out of the way
    before = err.getvalue()
    st.paint()
    assert err.getvalue() == before  # column isn't 0: painting refused

    st.notify("done\n")  # the line completed
    st.paint()
    assert "alpha" in err.getvalue()[len(before) :]  # painting resumed

    st.close()
    assert err.getvalue().endswith("\x1b[K")  # the line never outlives the run


def test_status_line_counts_and_failures():
    err = _Tty()
    st = _progress.StatusLine(err, None, color=False)
    st.unit_added(3)
    st.unit_started("a")
    st.unit_finished("a", ok=False)
    st.unit_skipped("b")
    line = st._render()
    assert "2/3" in line
    assert "1 failed" in line


def test_determinate_bar_never_fills_while_running():
    # An "overdue" run clamps at 98% — the bar must not lie about done-ness.
    est = _progress.Estimate(typical=4.0, scale=0.0001)
    st = _progress.StatusLine(_Tty(), est, color=False)
    line = st._render()
    assert "░" in line  # still some empty cells
    assert "~4.0s" in line


def test_indeterminate_pulse_moves():
    st = _progress.StatusLine(_Tty(), None, color=False)
    st.ticks = 1
    one = st._render()
    st.ticks = 4
    two = st._render()
    assert one != two  # the pulse wanders
    assert "~" not in one  # no fake estimate
