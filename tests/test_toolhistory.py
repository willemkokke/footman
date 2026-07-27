"""The option history: a base at HEAD, deltas pointing backwards.

Three properties carry the whole format, and each has a way of failing
silently — a lossy round-trip degrades stubs without erroring, a wrong delta
replays into a surface nobody notices is wrong, and an empty delta read as
"never observed" would make a release job redo work it already did.
"""

from __future__ import annotations

import itertools
import json

import pytest

from footman import _toolhistory
from footman._toolspec import Option, ToolSpec, Verb


def _spec(**over) -> ToolSpec:
    base = ToolSpec(
        name="demo",
        help="A demo tool.",
        version="1.0.0",
        verbs=(
            Verb(
                name="",
                help="The tool itself.",
                options=(Option("quiet", ("-q", "--quiet"), type_name="bool"),),
            ),
            Verb(
                name="build",
                help="Build it.",
                wraps=False,
                positional="required",
                lead="target",
                options=(
                    Option(
                        "output",
                        ("-o", "--output"),
                        help="Where to write.",
                        type_name="str",
                        default="dist",
                    ),
                    Option(
                        "clean",
                        ("--clean",),
                        negation="--dirty",
                        help="Clean first.",
                        type_name="bool",
                        default=True,
                    ),
                    Option(
                        "mode",
                        ("--mode",),
                        type_name="choice",
                        choices=("fast", "safe"),
                    ),
                ),
            ),
        ),
    )
    return ToolSpec(**{**base.__dict__, **over})


def test_a_surface_round_trips_without_losing_a_field():
    """Every field the stub renderer reads must survive the store, or a
    regenerated stub quietly loses a negation, a default or a Literal."""
    spec = _spec()
    back = _toolhistory.spec_from(
        _toolhistory.surface_of(spec),
        name=spec.name,
        version=spec.version,
        in_process=spec.in_process,
    )
    assert back == spec


def test_the_surface_leaves_out_what_is_not_the_release():
    """`version` keys the release and `in_process` is a fact about the machine
    that looked — neither describes what the tool accepts."""
    surface = _toolhistory.surface_of(_spec(version="9.9.9", in_process=True))
    blob = json.dumps(surface)
    assert "9.9.9" not in blob
    assert "in_process" not in blob


def test_a_delta_steps_back_exactly():
    """The chain's whole claim: replaying a delta reproduces the older
    surface, option for option."""
    new = _toolhistory.surface_of(_spec())
    older = _toolhistory.surface_of(
        _spec(
            help="An older demo tool.",
            verbs=(
                Verb(name="", help="The tool itself.", options=()),
                Verb(
                    name="build",
                    help="Build it, once.",
                    positional="any",
                    options=(
                        Option("output", ("-o",), help="Older help.", type_name="str"),
                    ),
                ),
            ),
        )
    )
    step = _toolhistory.delta(new, older)
    assert _toolhistory.apply(new, step) == older
    # ...and it says what moved, rather than restating the whole surface.
    assert "\tquiet" in " ".join(step["drop"])
    assert "help" in step and step["help"] == "An older demo tool."


def test_a_verb_that_arrived_is_dropped_when_stepping_back():
    new = _toolhistory.surface_of(_spec())
    older = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="", help="The tool itself.", options=()),))
    )
    step = _toolhistory.delta(new, older)
    assert _toolhistory.apply(new, step) == older
    assert step["verbs"]["build"] is None  # arrived in the newer release


def test_an_unchanged_release_records_an_empty_delta():
    """Observed and changed nothing is not the same as never looked at — the
    first is an empty delta, the second is simply absent. A release job reads
    the difference to decide whether to work."""
    surface = _toolhistory.surface_of(_spec())
    assert _toolhistory.delta(surface, surface) == {}

    doc = _toolhistory.new("demo", version="1.0.0", date="2026-01-02", surface=surface)
    doc["deltas"]["0.9.0"] = {"date": "2026-01-01", "extractor": 1}
    assert _toolhistory.at(doc, "0.9.0") == surface  # replays to the same thing
    assert _toolhistory.at(doc, "0.5.0") is None  # never observed
    assert _toolhistory.observed(doc) == ["1.0.0", "0.9.0"]


def test_replay_reaches_every_release_in_a_chain():
    """Built the way priming builds it — newest first, each older release
    appended — and every point still reconstructs."""
    surfaces = {
        f"1.{n}.0": _toolhistory.surface_of(
            _spec(
                verbs=(
                    Verb(
                        name="build",
                        help=f"Build at {n}.",
                        options=tuple(
                            Option(f"opt{i}", (f"--opt{i}",), help=f"Option {i}.")
                            for i in range(n + 1)
                        ),
                    ),
                )
            )
        )
        for n in range(5)
    }
    order = sorted(surfaces, reverse=True)  # newest first, as the prime walks
    doc = _toolhistory.new(
        "demo", version=order[0], date="2026-01-05", surface=surfaces[order[0]]
    )
    for newer, older in itertools.pairwise(order):
        doc["deltas"][older] = {
            "date": "2026-01-01",
            "extractor": _toolhistory.EXTRACTOR,
            **_toolhistory.delta(surfaces[newer], surfaces[older]),
        }
        doc["observed_from"] = older

    for version, expected in surfaces.items():
        assert _toolhistory.at(doc, version) == expected, version


def test_load_of_a_missing_or_broken_file_is_none(tmp_path):
    assert _toolhistory.load(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert _toolhistory.load(broken) is None


def test_save_writes_atomically_and_leaves_no_temp(tmp_path):
    doc = _toolhistory.new(
        "demo",
        version="1.0.0",
        date="2026-01-02",
        surface=_toolhistory.surface_of(_spec()),
    )
    path = tmp_path / "demo.json"
    _toolhistory.save(doc, path)
    assert _toolhistory.load(path) == doc
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("key", ["prek", "docker", "ruff"])
def test_the_checked_in_history_regenerates_its_stub(key):
    """The seeding claim, checked against what ships: rendering from the
    history reproduces the checked-in stub.

    Compared as parsed source, not as bytes. Two things in a stub file are
    nobody's business but the machine that wrote it — the header stamps which
    platform looked and whether it could import the tool, and the layout is
    the formatter's, whose isort splits an aliased import on some platforms
    and joins it on others. Neither is information the store holds. What the
    store owes is every verb, option, flag, negation, default and choice set,
    and an AST comparison is exactly that claim.

    (Byte-identity is checked where it means something: `fm tools.sync`
    rewrites a stub only when the text differs, and after this landed it
    rewrote none of the 26.)
    """
    import ast

    from footman import _stubgen
    from footman.tasks import tools as tools_tasks

    stub = tools_tasks._stub_path(key)
    doc = _toolhistory.load(tools_tasks._history_path(key))
    assert doc is not None, f"{key} has no history"

    recorded, mode = tools_tasks._header(stub)
    version, _, platform = recorded.partition(" ")
    base = doc["base"]
    assert base["version"] == version, (
        "the history and the stub disagree about which release was read"
    )

    rendered = _stubgen.render(
        # The union, as generation renders it: every option the tool has ever
        # had, so a flag it later dropped stays completable.
        _toolhistory.union(doc, name=key.replace("_", "-")),
        platform=platform.strip("()"),
        class_name=_stubgen._class_name(key),
        in_process=mode,
    )

    def classes(source: str) -> str:
        """The class tree alone — every verb, option, flag, negation, default
        and choice set. The import block is derived from the body and laid out
        by the formatter, which splits an aliased import on some platforms and
        joins it on others; that is layout, not content."""
        parsed = ast.parse(source)
        return ast.dump(
            ast.Module(
                body=[n for n in parsed.body if isinstance(n, ast.ClassDef)],
                type_ignores=[],
            )
        )

    assert classes(rendered) == classes(stub.read_text(encoding="utf-8"))


# --- priming: walking backwards, resumably ----------------------------------


def test_extend_appends_older_and_skips_what_it_has():
    """The prime's whole write pattern: append-only, and a release already in
    the chain is skipped — which is what makes an interrupted run resumable
    rather than duplicative."""
    surface = _toolhistory.surface_of(_spec())
    doc = _toolhistory.new("demo", version="1.2.0", date="2026-02-01", surface=surface)

    older = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="build", help="Older.", options=()),))
    )
    assert _toolhistory.extend(doc, version="1.1.0", date="2026-01-01", surface=older)
    assert doc["observed_from"] == "1.1.0"
    assert _toolhistory.at(doc, "1.1.0") == older
    assert _toolhistory.at(doc, "1.2.0") == surface  # the base did not move

    # A second pass over the same release adds nothing.
    assert not _toolhistory.extend(
        doc, version="1.1.0", date="2026-01-01", surface=older
    )
    assert _toolhistory.observed(doc) == ["1.2.0", "1.1.0"]


def test_releases_break_a_same_day_tie_by_version(monkeypatch):
    """Two releases on one day are common — prek shipped 0.4.7 and 0.4.8
    together. Resolved by dict order the walk skips one and a later prime
    appends it *below* its own successor, which corrupts the chain."""
    import io
    import json as _json

    from footman import _drivers, _toolfetch

    index = {
        "releases": {
            "0.4.7": [{"upload_time": "2026-07-04T10:00:00"}],
            "0.4.8": [{"upload_time": "2026-07-04T18:00:00"}],
            "0.4.9": [{"upload_time": "2026-07-11T09:00:00"}],
            "0.4.6": [],  # no files: not installable, so not a release to read
        }
    }
    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(index).encode()),
    )
    driver = _drivers.find("prek")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == [
        "0.4.9",
        "0.4.8",
        "0.4.7",
    ]


def test_only_listable_tiers_are_primed():
    """A tool footman cannot enumerate is named and skipped, never treated as
    a tool with no history."""
    from footman import _drivers, _toolfetch

    uv_tier = _drivers.find("prek")
    system_tier = _drivers.find("git")
    manual = _drivers.find("bash")
    assert uv_tier and system_tier and manual
    assert _toolfetch.can_list(uv_tier)
    assert not _toolfetch.can_list(system_tier)  # git is read from the host
    assert not _toolfetch.can_list(manual)  # hand-written stub, nothing to read


def test_the_primed_history_ships_a_contiguous_chain():
    """What is checked in must replay end to end — a hole would mean a delta
    computed against a release that is not its neighbour."""
    from footman.tasks import tools as tools_tasks

    doc = _toolhistory.load(tools_tasks._history_path("prek"))
    assert doc is not None
    chain = _toolhistory.observed(doc)
    assert len(chain) > 1, "prek's history was primed; it should carry deltas"
    for version in chain:
        assert _toolhistory.at(doc, version) is not None, version
    assert doc["observed_from"] == chain[-1]


def test_the_union_carries_intervals_the_history_can_prove():
    """What a stub may say about an option's life, and what it may not.

    An option already present at the oldest release read has no `since` — the
    chain never looked far enough back to claim one, and "at or before the
    floor" is not a `since`. An option the tool has dropped keeps its entry
    and gains an `until`, because a reader may be running a version that
    still has it.
    """
    old = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("doomed", ("--doomed",)),
                    ),
                ),
            )
        )
    )
    middle = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("doomed", ("--doomed",)),
                        Option("fresh", ("--fresh",)),
                    ),
                ),
            )
        )
    )
    newest = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("fresh", ("--fresh",)),
                    ),
                ),
            )
        )
    )
    doc = _toolhistory.new("demo", version="3.0.0", date="2026-03-01", surface=newest)
    _toolhistory.extend(doc, version="2.0.0", date="2026-02-01", surface=middle)
    _toolhistory.extend(doc, version="1.0.0", date="2026-01-01", surface=old)

    options = {
        o.name: o for v in _toolhistory.union(doc, name="demo").verbs for o in v.options
    }
    assert set(options) == {"ancient", "doomed", "fresh"}  # every option ever
    assert options["ancient"].since == ""  # there at the floor: nothing provable
    assert options["fresh"].since == "2.0.0"  # arrived, and the chain saw it
    assert options["doomed"].until == "3.0.0"  # the release it stopped appearing in
    assert options["doomed"].since == ""


def test_a_history_of_one_release_claims_nothing():
    """The seeded state: no chain, so no interval is provable and the stub
    says only what the tool says."""
    doc = _toolhistory.new(
        "demo",
        version="1.0.0",
        date="2026-01-01",
        surface=_toolhistory.surface_of(_spec()),
    )
    spec = _toolhistory.union(doc, name="demo")
    assert spec.verbs, "the union of one release is that release"
    assert not any(o.since or o.until for v in spec.verbs for o in v.options)


def test_an_observation_records_which_platforms_read_it():
    """A fact about the observation, like its date — and the groundwork for
    exclusions: "absent on Windows, and Windows was read" is an exclusion,
    while "absent on Windows, which never ran" is silence.

    A *list*, because a release read on three platforms is one observation of
    a merged surface. Storing it three times would triple a store whose
    options are nearly all universal, to carry the rare one that is not.
    """
    surface = _toolhistory.surface_of(_spec())
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-02-01",
        surface=surface,
        platforms=["Linux", "macOS"],
    )
    assert doc["base"]["platforms"] == ["Linux", "macOS"]  # sorted, one entry

    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=surface,
        platforms=["Windows"],
    )
    assert doc["deltas"]["1.0.0"]["platforms"] == ["Windows"]


def test_every_checked_in_observation_names_its_platforms():
    """The store must not grow observations that cannot say where they came
    from; a later multi-platform refresh reads this to decide what is an
    exclusion and what was simply never looked at."""
    from footman import _drivers
    from footman.tasks import tools as tools_tasks

    for driver in _drivers.DRIVERS:
        doc = _toolhistory.load(tools_tasks._history_path(driver.key))
        if doc is None:
            continue
        assert doc["base"].get("platforms"), f"{driver.key} base"
        for version, step in doc["deltas"].items():
            assert step.get("platforms"), f"{driver.key} {version}"


def test_priming_rewrites_the_stub_it_invalidates(monkeypatch, tmp_path):
    """A deeper history changes what a stub may say — an option that looked
    original at the old floor may turn out to have arrived. The stub is a
    rendering of the record, so extending the record rewrites it rather than
    waiting for someone to remember a `sync`."""
    from footman.tasks import tools as tools_tasks

    doc = _toolhistory.load(tools_tasks._history_path("prek"))
    assert doc is not None
    chain = _toolhistory.observed(doc)
    assert len(chain) > 5, "prek is the primed tool; this test needs its chain"

    stub = tools_tasks._stub_path("prek").read_text(encoding="utf-8")
    assert "Added in" in stub, "a primed tool's stub carries what the chain proved"
    # ...and only versions the chain actually holds.
    import re

    for claimed in set(re.findall(r"Added in ([0-9][^.\s]*(?:\.[^.\s]+)*)\.", stub)):
        assert claimed in chain, claimed


def test_an_older_reading_never_becomes_the_head(tmp_path, monkeypatch):
    """A machine with a stale tool must not rewrite the base and push the
    newer release down the chain as though it came first. Recording on any
    change did exactly that: ruff's history ended up with 0.16.0 as both the
    base and one of its own ancestors."""
    from footman import _drivers
    from footman.tasks import tools as tools_tasks

    monkeypatch.setattr(tools_tasks, "_HISTORY", tmp_path)
    driver = _drivers.find("prek")
    assert driver is not None

    def spec_at(version: str):
        return ToolSpec(name="prek", version=version, verbs=_spec().verbs)

    tools_tasks._observe(driver, spec_at("0.5.0"))
    doc = tools_tasks._observe(driver, spec_at("0.4.0"))  # a laggard machine
    assert doc["base"]["version"] == "0.5.0"  # the head stands
    assert list(doc["deltas"]) == ["0.4.0"]  # ...and the older read is history

    doc = tools_tasks._observe(driver, spec_at("0.6.0"))  # a newer release
    assert doc["base"]["version"] == "0.6.0"
    assert list(doc["deltas"]) == ["0.5.0", "0.4.0"]
    assert _toolhistory.observed(doc) == ["0.6.0", "0.5.0", "0.4.0"]


# --- the tiers a prime can read ---------------------------------------------


def _index(monkeypatch, payload):
    """Serve *payload* as the registry's JSON, whatever URL is asked for."""
    import io
    import json as _json

    from footman import _toolfetch

    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(payload).encode()),
    )


def test_npm_releases_come_from_the_time_map(monkeypatch):
    """npm keeps publication dates in `time`, alongside two entries that are
    not versions at all."""
    from footman import _drivers, _toolfetch

    _index(
        monkeypatch,
        {
            "versions": {"9.0.0": {}, "10.0.0": {}, "10.0.1": {}},
            "time": {
                "created": "2020-01-01T00:00:00Z",
                "modified": "2026-05-31T00:00:00Z",
                "9.0.0": "2026-01-05T00:00:00Z",
                "10.0.0": "2026-05-30T00:00:00Z",
                "10.0.1": "2026-05-31T00:00:00Z",
                "10.0.2": "2026-06-01T00:00:00Z",  # in `time`, not in `versions`
            },
        },
    )
    driver = _drivers.find("cspell")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["10.0.1", "10.0.0", "9.0.0"]
    assert got[0].date == "2026-05-31"


def test_github_releases_normalise_the_tag_and_drop_the_unreleased(monkeypatch):
    """A tag is `v2.96.0` on one project and `2.96.0` on the next, while the
    binary reports the bare number — and the history keys on what the binary
    says, or a primed release never matches the base it belongs under."""
    from footman import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v2.96.0", "published_at": "2026-07-02T00:00:00Z"},
            {"tag_name": "v2.95.0", "published_at": "2026-06-01T00:00:00Z"},
            {
                "tag_name": "v3.0.0-rc1",
                "published_at": "2026-07-20T00:00:00Z",
                "prerelease": True,
            },
            {
                "tag_name": "v2.97.0",
                "published_at": "2026-07-10T00:00:00Z",
                "draft": True,
            },
        ],
    )
    driver = _drivers.find("gh")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["2.96.0", "2.95.0"]


def test_gitlab_releases_read_their_own_field_names(monkeypatch):
    from footman import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v0.6.0-wk.5", "released_at": "2026-07-07T00:00:00Z"},
            {"tag_name": "v0.6.0-wk.4", "released_at": "2026-06-07T00:00:00Z"},
        ],
    )
    driver = _drivers.find("eclint")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.6.0-wk.5", "0.6.0-wk.4"]


def test_an_unreadable_index_is_not_an_empty_one(monkeypatch):
    """The distinction the release gate rests on.

    "Is there anything new" is answered "no" by a throttled registry exactly
    as it is by a tool that has genuinely not moved — and one of those means
    stop, while the other means nobody looked. Sharing the empty list would
    let a rate limit read as "nothing to release".

    A prime still skips such a tool rather than failing the run, but it has
    to *choose* to, which is the point of raising.
    """
    from footman import _drivers, _toolfetch

    def boom(*a, **k):
        raise _toolfetch.urllib.error.URLError("no network")

    monkeypatch.setattr(_toolfetch.urllib.request, "urlopen", boom)
    driver = _drivers.find("prek")
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def test_which_tiers_can_be_listed():
    """`system` is absent because git and docker are read from the host with
    no fetch source, and a hand-written stub has nothing to read at all."""
    from footman import _drivers, _toolfetch

    expected = {
        "prek": True,  # uv
        "cspell": True,  # node
        "gh": True,  # github
        "eclint": True,  # gitlab
        "bun": True,  # bun's own releases
        "python": True,  # uv carries CPython's own download index
        "git": False,  # system
        "docker": False,  # system
        "bash": False,  # manual stub
    }
    for key, listable in expected.items():
        driver = _drivers.find(key)
        assert driver is not None, key
        assert _toolfetch.can_list(driver) is listable, key
        if not listable:
            assert _toolfetch.releases(driver) == [], key


def test_installing_an_unlistable_tier_declines(tmp_path):
    from footman import _drivers, _toolfetch

    driver = _drivers.find("git")
    assert driver is not None
    assert _toolfetch.install(driver, "2.50.0", tmp_path / "git") is None


def test_the_npm_tier_needs_bun_and_says_so(tmp_path, monkeypatch):
    """bun is how the node tier is provisioned, so priming borrows it. Without
    it the walk stops — and reports why, because a scheduled job reading '+0'
    cannot tell that from 'nothing left to read'."""
    import shutil

    from footman import _drivers, _toolfetch

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    driver = _drivers.find("cspell")
    assert driver is not None
    assert _toolfetch.install(driver, "10.0.0", tmp_path / "cspell") is None


# --- CPython: a tool with more than one release line at a time ---------------


def _uv_listing(monkeypatch, entries):
    """Serve *entries* as `uv python list --output-format json` would."""
    import json as _json

    from footman import _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: _json.dumps(entries))


def _cpython(version, day, **over):
    """One entry of uv's listing, defaulting to a downloadable stable build."""
    return {
        "version": version,
        "implementation": "cpython",
        "variant": "default",
        "path": None,
        "url": f"https://example.invalid/releases/download/{day}/cpython-{version}.tar.gz",
        **over,
    }


def test_the_python_listing_keeps_only_what_is_a_release(monkeypatch):
    """A pre-release is not something to claim an option arrived in, a
    free-threaded build is a build of a release rather than one of its own,
    and pypy is a different tool."""
    from footman import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.14.6", "20260718"),
            _cpython("3.15.0a7", "20260801"),
            _cpython("3.13.14", "20260718", variant="freethreaded"),
            _cpython("3.12.0", "20231002", implementation="pypy"),
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["3.14.6"]


def test_the_python_listing_asks_only_for_downloads(monkeypatch):
    """Installing a version replaces its download entry with the local path
    and drops the URL the date is read from. Asking for anything but downloads
    would therefore make the index answer differently on every machine — and a
    prime would erase releases from the listing it is walking."""
    from footman import _drivers, _toolfetch

    seen: list[list[str]] = []
    monkeypatch.setattr(_toolfetch, "_capture", lambda argv: seen.append(argv) or "[]")
    driver = _drivers.find("python")
    assert driver is not None
    _toolfetch.releases(driver)
    assert "--only-downloads" in seen[0]


def test_a_uv_that_will_not_answer_is_unreachable_not_empty(monkeypatch):
    """uv carries the index inside itself, so "no uv" is "nothing seen" — and
    emphatically not "CPython has no releases"."""
    from footman import _drivers, _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: "")
    driver = _drivers.find("python")
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def test_a_prime_never_appends_a_release_newer_than_the_floor(tmp_path, monkeypatch):
    """The walk goes backwards, so a newer release must never land below the
    floor — it would record the new surface as the old one and invert every
    interval derived from it.

    A base carries the date it was *observed*, not the date it was published,
    so on a first prime the floor is dated today and no date test can order
    it against the index. The listing's own order can.
    """
    from footman import _toolfetch
    from footman.tasks import tools

    surface = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="", options=(Option("quiet", ("-q",)),)),))
    )
    doc = _toolhistory.new(
        "demo", version="3.13.1", date=tools._today(), surface=surface
    )
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _driver: [
            _toolfetch.Release(version=v, date=d)
            for v, d in [
                ("3.14.6", "2026-07-18"),  # newer than the base, and published
                ("3.13.1", "2026-01-05"),  # older than "today" by every measure
                ("3.12.13", "2026-07-18"),
            ]
        ],
    )
    installed: list[str] = []
    monkeypatch.setattr(
        _toolfetch, "install", lambda _d, version, _into: installed.append(version)
    )

    added, stopped = tools._prime_one(
        _drivers_find("python"), doc, 10, tmp_path, _toolfetch
    )
    assert added == 0 and stopped  # install returned None: the walk stops there
    assert installed == ["3.12.13"]  # never 3.14.6, which sits above the floor


def test_a_floor_the_index_cannot_place_stops_the_walk(tmp_path, monkeypatch):
    """A stub synced from an outdated binary leaves a floor no listing holds.
    Priming from the top of the index would append the newest release as the
    oldest, so it says so instead of guessing."""
    from footman import _toolfetch
    from footman.tasks import tools

    surface = _toolhistory.surface_of(_spec(verbs=(Verb(name=""),)))
    doc = _toolhistory.new("demo", version="3.13.1", date="2026-07-27", surface=surface)
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _driver: [_toolfetch.Release(version="3.14.6", date="2026-07-18")],
    )
    added, stopped = tools._prime_one(
        _drivers_find("python"), doc, 10, tmp_path, _toolfetch
    )
    assert added == 0
    assert "3.13.1" in stopped and "sync" in stopped


def _drivers_find(key):
    from footman import _drivers

    driver = _drivers.find(key)
    assert driver is not None, key
    return driver


def test_a_chain_is_ordered_by_version_not_by_publication_date(monkeypatch):
    """Three curated tools keep more than one series alive at once — cmake
    3.31.x beside 4.x, pytest's 4.6 LTS beside 5.x, CPython's five — so the
    newest release is not the most recently published one.

    Ordered by date, a walk back from 3.14.6 steps to 3.13.14 and records
    every 3.14 option as dropped, then re-adds them lower down; every
    interval derived from that chain is then wrong. The history answers a
    version question, so version is what orders it.
    """
    from footman import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.13.14", "20260718"),  # same build date as 3.14.6...
            _cpython("3.14.6", "20260718"),
            _cpython("3.12.13", "20260718"),
            _cpython("3.14.5", "20260611"),  # ...and published before 3.13.14
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    found = [r.version for r in _toolfetch.releases(driver)]
    assert found == ["3.14.6", "3.14.5", "3.13.14", "3.12.13"]


def test_a_tie_the_comparator_cannot_break_leaves_the_base_alone(tmp_path, monkeypatch):
    """`0.6.0-wk.3` and `0.6.0-wk.5` are two builds of one base, and the
    comparator reduces both to `(0, 6, 0)` — a build tail says nothing about
    which flags exist. A chain breaks that tie on publication date, but a
    fresh reading is stamped today whatever build it holds, so the snapshot
    guard has nothing to break it with.

    It must therefore decline. Treating the tie as "not older" is what let a
    stale checkout promote `wk.3` over the recorded `wk.5` and push the newer
    build down the chain — the exact rewrite the guard exists to refuse.
    """
    from footman.tasks import tools

    surface = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="", options=(Option("fix", ("--fix",)),)),))
    )
    doc = _toolhistory.new(
        "eclint", version="0.6.0-wk.5", date="2026-07-01", surface=surface
    )
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    _toolhistory.save(doc, tmp_path / "eclint.json")

    driver = _drivers_find("eclint")
    spec = _toolhistory.spec_from(surface, name="eclint", version="0.6.0-wk.3")
    with pytest.raises(tools._Ambiguous) as raised:
        tools._observe(driver, spec)
    assert raised.value.reading == "0.6.0-wk.3"
    assert raised.value.base == "0.6.0-wk.5"
    # and the file is untouched
    stored = _toolhistory.load(tmp_path / "eclint.json")
    assert stored is not None
    assert stored["base"]["version"] == "0.6.0-wk.5"


# --- the forward walk: catching a history up to its index --------------------


def test_a_refresh_reads_every_release_it_missed_not_just_the_newest(
    tmp_path, monkeypatch
):
    """Attribution is the whole point of the log. If a tool is three releases
    behind and only the newest is read, a flag that arrived in 0.16.1 is
    recorded as having arrived in 0.16.3 — and the stub then tells a reader
    on 0.16.2 that an option they have does not exist yet.

    An unchanged release still gets read, and records an empty delta: that is
    "observed, changed nothing", which is not the same as never looked.
    """
    from footman import _drivers, _toolfetch
    from footman.tasks import tools

    def surface_with(*names):
        return _toolhistory.surface_of(
            _spec(
                verbs=(
                    Verb(
                        name="",
                        options=tuple(Option(n, (f"--{n}",)) for n in names),
                    ),
                )
            )
        )

    by_version = {
        "0.16.0": surface_with("quiet"),
        "0.16.1": surface_with("quiet", "fix"),  # the event
        "0.16.2": surface_with("quiet", "fix"),  # nothing changed
        "0.16.3": surface_with("quiet", "fix"),  # nor here
    }
    doc = _toolhistory.new(
        "ruff", version="0.16.0", date="2026-01-01", surface=by_version["0.16.0"]
    )
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    monkeypatch.setattr(tools, "_STUBS", tmp_path)
    monkeypatch.setattr(tools, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    _toolhistory.save(doc, tmp_path / "ruff.json")

    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _d: [
            _toolfetch.Release(version=v, date=f"2026-02-0{i}")
            for i, v in enumerate(["0.16.3", "0.16.2", "0.16.1", "0.16.0"], start=1)
        ],
    )
    monkeypatch.setattr(_toolfetch, "install", lambda _d, _v, into: into)

    installed: list[str] = []

    def extract(driver):
        version = installed[-1]
        return _toolhistory.spec_from(by_version[version], name=driver.name)

    def install(_driver, version, into):
        installed.append(version)
        return into

    monkeypatch.setattr(_toolfetch, "install", install)
    monkeypatch.setattr(_drivers, "extract", extract)

    found = tools.refresh(only="ruff")

    # every missed release, oldest first — not a jump to the newest
    assert installed == ["0.16.1", "0.16.2", "0.16.3"]
    assert found.read["ruff"] == ["0.16.1", "0.16.2", "0.16.3"]
    # ...and only the one that moved the surface counts as an event
    assert found.events["ruff"] == ["0.16.1"]
    assert found.release is True

    stored = _toolhistory.load(tmp_path / "ruff.json")
    assert stored is not None
    assert stored["base"]["version"] == "0.16.3"
    assert stored["base"]["date"] == "2026-02-01"  # the index's date, not today's
    assert _toolhistory.observed(stored) == ["0.16.3", "0.16.2", "0.16.1", "0.16.0"]
    # the two quiet releases are recorded as observed-and-unchanged
    assert not any(
        k not in ("date", "platforms", "extractor") for k in stored["deltas"]["0.16.2"]
    )


def test_a_refresh_with_nothing_new_warrants_no_release(tmp_path, monkeypatch):
    """The exit condition. A tool that has not released is not a tool that
    changed nothing — there is simply nothing to look at."""
    from footman import _toolfetch
    from footman.tasks import tools

    surface = _toolhistory.surface_of(_spec(verbs=(Verb(name=""),)))
    doc = _toolhistory.new("ruff", version="0.16.3", date="2026-02-01", surface=surface)
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    monkeypatch.setattr(tools, "_STUBS", tmp_path)
    monkeypatch.setattr(tools, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    _toolhistory.save(doc, tmp_path / "ruff.json")
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _d: [_toolfetch.Release(version="0.16.3", date="2026-02-01")],
    )

    found = tools.refresh(only="ruff")
    assert found.read == {} and found.events == {}
    assert found.release is False


def test_a_refresh_that_could_not_look_does_not_report_nothing_new(
    tmp_path, monkeypatch
):
    """The two answers a release job must never confuse. An index that would
    not answer is recorded as unreachable and the task fails, so a rename or
    a moved repo surfaces instead of making a tool silently untracked while
    the job keeps reporting success."""
    from footman import _toolfetch
    from footman.context import Failed
    from footman.tasks import tools

    surface = _toolhistory.surface_of(_spec(verbs=(Verb(name=""),)))
    doc = _toolhistory.new("ruff", version="0.16.3", date="2026-02-01", surface=surface)
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    monkeypatch.setattr(tools, "_STUBS", tmp_path)
    monkeypatch.setattr(tools, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    _toolhistory.save(doc, tmp_path / "ruff.json")

    def throttled(_driver):
        raise _toolfetch.Unreachable("https://pypi.org/pypi/ruff/json", "429")

    monkeypatch.setattr(_toolfetch, "releases", throttled)

    with pytest.raises(Failed) as failed:
        tools.refresh(only="ruff")
    assert failed.value.code == 75  # EX_TEMPFAIL: look again, not "nothing to do"
    assert "ruff" in failed.value.reason


# --- the CHANGELOG entry the events write ------------------------------------


def _chain(*surfaces):
    """A history built newest-last, the way a refresh promotes into one."""
    versions = [f"1.0.{n}" for n in range(len(surfaces))]
    doc = _toolhistory.new(
        "demo", version=versions[0], date="2026-01-01", surface=surfaces[0]
    )
    for n, surface in enumerate(surfaces[1:], start=1):
        _toolhistory.promote(
            doc, version=versions[n], date=f"2026-01-0{n + 1}", surface=surface
        )
    return doc, versions


def _with(*options, verb=""):
    return _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name=verb,
                    options=tuple(
                        Option(n, (f"-{n[0]}", f"--{n.replace('_', '-')}"))
                        for n in options
                    ),
                ),
            )
        )
    )


def test_an_entry_names_what_changed_and_counts_what_it_will_not_list():
    """Added and dropped options are what a reader acts on, so they are named
    — by their command-line spelling, which is what they recognise, and not
    by the Python-side key the surface happens to store them under.

    Rewordings are counted. A release can restate half a dozen descriptions
    without changing what the tool accepts, and listing those turns a release
    note into a diff dump.
    """
    from footman.tasks import tools

    doc, versions = _chain(
        _with("quiet", "install_hooks"),
        _with("quiet", "prepare_hooks"),  # one added, one dropped
    )
    entry = tools._entry_for("prek", doc, versions[1:])

    assert entry.startswith("- **prek 1.0.1**")
    assert "`--prepare-hooks`" in entry  # the spelling, not `prepare_hooks`
    assert "drops `--install-hooks`" in entry


def test_an_entry_spans_from_the_release_before_the_first_change():
    """A release compared against itself is empty by construction, so the
    span has to start one earlier or the first change is invisible — which
    is exactly what a bullet saying "changes its option surface" looked
    like."""
    from footman.tasks import tools

    doc, versions = _chain(_with("quiet"), _with("quiet", "fix"))
    assert "adds `--fix`" in tools._entry_for("demo", doc, [versions[1]])


def test_an_entry_names_a_flag_once_however_many_verbs_carry_it():
    """The same flag on the bare command and on one of its verbs is two keys
    in the surface and one thing to tell a reader about."""
    from footman.tasks import tools

    def both(*names):
        return _toolhistory.surface_of(
            _spec(
                verbs=tuple(
                    Verb(
                        name=verb,
                        options=tuple(Option(n, (f"--{n}",)) for n in names),
                    )
                    for verb in ("", "run")
                )
            )
        )

    doc, versions = _chain(both("quiet"), both("quiet", "glob"))
    entry = tools._entry_for("prek", doc, [versions[1]])
    assert entry.count("`--glob`") == 1


def test_the_entry_lands_under_unreleased_changed(tmp_path):
    """`### Changed`, because a tool gaining a flag changes footman's *stub*
    — footman itself added nothing. And under `[Unreleased]`, never the
    released section above it, which is where a careless insert lands."""
    from footman.tasks import tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something.\n\n"
        "### Fixed\n\n- Something else.\n\n## [0.23.0] — 2026-07-27\n\n"
        "### Changed\n\n- Already released.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is True

    written = path.read_text(encoding="utf-8")
    unreleased, released = written.split("## [0.23.0]")
    assert "- **prek 0.4.11** adds `--glob`." in unreleased
    assert "- **prek 0.4.11**" not in released  # not swept into a shipped version
    # Keep a Changelog's order: Changed sits above Fixed, not appended anywhere.
    assert unreleased.index("### Changed") < unreleased.index("### Fixed")


def test_an_entry_joins_a_changed_section_that_already_exists(tmp_path):
    from footman.tasks import tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- An earlier note.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **ruff 0.16.1** adds `--fix`."], path) is True
    written = path.read_text(encoding="utf-8")
    assert written.count("### Changed") == 1
    assert "- **ruff 0.16.1** adds `--fix`." in written
    assert "- An earlier note." in written


def test_a_changelog_with_nowhere_to_write_says_so(tmp_path):
    """Reported rather than guessed at: a caller must not read "no entry
    written" as "there was nothing to write"."""
    from footman.tasks import tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [0.23.0]\n\n- Released.\n", encoding="utf-8")
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is False
    assert tools._write_changelog(["- x"], tmp_path / "absent.md") is False


def test_a_refresh_writes_its_own_events_into_the_changelog(tmp_path, monkeypatch):
    """The job edits `tool-history/` and the stubs and lands through a PR
    either way, so the release note rides along in the same diff. A
    scheduled run that printed notes to stdout would be producing them for
    nobody."""
    from footman import _drivers, _toolfetch
    from footman.tasks import tools

    _, versions = _chain(_with("quiet"), _with("quiet", "fix"))
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    monkeypatch.setattr(tools, "_STUBS", tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    monkeypatch.setattr(tools, "_CHANGELOG", changelog)

    surfaces = dict(zip(versions, [_with("quiet"), _with("quiet", "fix")]))
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version=versions[0],
            date="2026-01-01",
            surface=surfaces[versions[0]],
        ),
        tmp_path / "ruff.json",
    )
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _d: [
            _toolfetch.Release(version=v, date="2026-01-02") for v in reversed(versions)
        ],
    )
    installed: list[str] = []
    monkeypatch.setattr(
        _toolfetch,
        "install",
        lambda _d, version, into: (installed.append(version), into)[1],
    )
    monkeypatch.setattr(
        _drivers,
        "extract",
        lambda d: _toolhistory.spec_from(surfaces[installed[-1]], name=d.name),
    )

    found = tools.refresh(only="ruff")
    assert found.release is True
    assert found.wrote_changelog is True
    assert "adds `--fix`" in changelog.read_text(encoding="utf-8")


def test_a_refresh_with_no_events_writes_no_note(tmp_path, monkeypatch):
    """No events, no release, and nothing to say about one."""
    from footman import _toolfetch
    from footman.tasks import tools

    surface = _with("quiet")
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    monkeypatch.setattr(tools, "_STUBS", tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    monkeypatch.setattr(tools, "_CHANGELOG", changelog)
    _toolhistory.save(
        _toolhistory.new("ruff", version="1.0.0", date="2026-01-01", surface=surface),
        tmp_path / "ruff.json",
    )
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _d: [_toolfetch.Release(version="1.0.0", date="2026-01-01")],
    )

    found = tools.refresh(only="ruff")
    assert found.release is False and found.wrote_changelog is False
    assert "###" not in changelog.read_text(encoding="utf-8")


def test_an_entry_tells_commands_apart_from_their_descriptions():
    """Three things hide in one delta field. A verb the newer release added
    steps back as `None`; one it withdrew steps back as a whole verb; one
    whose description merely moved steps back as a couple of fields. Only the
    first two are news — the third is a rewording, and counting it as a lost
    command would be a lie in both directions.
    """
    from footman.tasks import tools

    def tree(*verbs):
        return _toolhistory.surface_of(
            _spec(
                verbs=tuple(
                    Verb(name=name, help=help_, options=()) for name, help_ in verbs
                )
            )
        )

    doc, versions = _chain(
        tree(("", "The tool."), ("run", "Run it."), ("clean", "Clean up.")),
        tree(
            ("", "The tool."),
            ("run", "Run the hooks."),  # reworded, not lost
            ("autoupdate", "Update."),  # gained
        ),  # `clean` withdrawn
    )
    entry = tools._entry_for("prek", doc, versions[1:])

    assert "gains the `autoupdate` command" in entry
    assert "withdraws the `clean` command" in entry
    assert "rewords 1 description" in entry
    assert "`run`" not in entry  # a reworded verb is not a lost one


def test_a_bullet_joins_several_names_and_clauses_readably():
    from footman.tasks import tools

    assert tools._names(["--a"]) == "`--a`"
    assert tools._names(["--a", "--b"]) == "`--a` and `--b`"
    assert tools._names(["--a", "--b", "--c"]) == "`--a`, `--b` and `--c`"
    assert tools._and(["one"]) == "one"
    assert tools._and(["one", "two", "three"]) == "one, two and three"
    assert tools._plural("release", 1) == "release"
    assert tools._plural("release", 2) == "releases"


# --- what a prime leaves behind ----------------------------------------------


def test_a_prime_keeps_uv_downloads_inside_its_own_scratch(tmp_path):
    """uv writes to two places of its own accord — a wheel cache, and the
    store holding the interpreters this machine actually runs. Neither is a
    prime's to fill, and a walk of CPython's releases put 90 interpreters in
    that store and left them there.

    Pointed inside the scratch directory, the cleanup is structural: one
    rmtree removes every byte the walk caused, and the python you develop
    against is never a candidate for deletion.
    """
    import os

    from footman.tasks import tools

    was = {k: os.environ.get(k) for k in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")}
    with tools._sandboxed(tmp_path):
        assert os.environ["UV_CACHE_DIR"].startswith(str(tmp_path))
        assert os.environ["UV_PYTHON_INSTALL_DIR"].startswith(str(tmp_path))
    assert {k: os.environ.get(k) for k in was} == was  # and put back


def test_an_overlay_restores_a_variable_that_was_not_set(tmp_path):
    """Restoring must remove what it added, not write an empty string —
    an empty `UV_CACHE_DIR` is a cache directory, not the absence of one."""
    import os

    from footman.tasks import tools

    os.environ.pop("FOOTMAN_TEST_ABSENT", None)
    with tools._overlay(FOOTMAN_TEST_ABSENT="x"):
        assert os.environ["FOOTMAN_TEST_ABSENT"] == "x"
    assert "FOOTMAN_TEST_ABSENT" not in os.environ


def test_a_release_is_discarded_once_its_surface_is_read(tmp_path):
    """Peak disk is one release rather than all of them. Without this a prime
    holds everything it ever fetched until the run ends — ruff alone would
    stand up 416 environments at once."""
    from footman.tasks import tools

    release = tmp_path / "1.2.3"
    (release / "bin").mkdir(parents=True)
    (release / "bin" / "tool").write_text("x")
    tools._discard(release / "bin")
    assert not release.exists()
    tools._discard(release / "bin")  # gone already: not an error


def test_a_prime_does_not_hold_every_release_it_fetched(tmp_path, monkeypatch):
    """The claim measured rather than asserted: at no point does more than one
    release exist on disk."""
    from footman import _drivers, _toolfetch
    from footman.tasks import tools

    monkeypatch.setattr(tools, "_HISTORY", tmp_path / "history")
    monkeypatch.setattr(tools, "_STUBS", tmp_path / "stubs")
    (tmp_path / "history").mkdir()
    (tmp_path / "stubs").mkdir()

    surface = _toolhistory.surface_of(_spec(verbs=(Verb(name="", options=()),)))
    doc = _toolhistory.new("ruff", version="1.0.9", date="2026-02-09", surface=surface)
    _toolhistory.save(doc, tmp_path / "history" / "ruff.json")

    # newest first, as every tier returns them
    monkeypatch.setattr(
        _toolfetch,
        "releases",
        lambda _d: [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n}")
            for n in range(9, -1, -1)
        ],
    )

    live: list[int] = []

    def install(_driver, version, into):
        (into / "bin").mkdir(parents=True, exist_ok=True)
        (into / "bin" / "ruff").write_text("x" * 1000)
        live.append(len(list(into.parent.iterdir())))
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)
    monkeypatch.setattr(
        _drivers, "extract", lambda d: _toolhistory.spec_from(surface, name=d.name)
    )

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    added, _stopped = tools._prime_one(
        _drivers_find("ruff"), doc, 9, scratch, _toolfetch
    )
    assert added == 9
    # One at a time: the directory never holds a second release.
    assert max(live) == 1
    assert list(scratch.iterdir()) == []
