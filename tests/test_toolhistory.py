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
