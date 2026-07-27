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
        _toolhistory.spec_from(
            base["surface"], name=key.replace("_", "-"), version=version
        ),
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
