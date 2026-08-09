"""footman's own `tasks.py` — the repo dogfooding its runner.

Worth testing for two reasons that are not "coverage". The **hook adapters**
are machine-called and human-invisible: nobody reads their output, so a
regression there is silent until a session is gated on the wrong tree, which
is exactly what happened. And the **docs generators** write files the site
includes, where a quiet miss ships a page with a hole in it rather than
failing a build.

The task bodies that are one call to a tool are tested as such: that they
call it, and with what. That is the whole of their behaviour — the tool's own
stub is generated and tested elsewhere.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from footman import registry as _registry

# Under capture(), or the repo's own ~30 tasks land in the process-global
# `registry.root` — which test_registry's leak guard rightly trips on when
# xdist schedules both files onto one worker.
with _registry.capture():
    import tasks

from footman.context import Failed, Result, RunFailed


def _failed_run(command: str = "ruff") -> RunFailed:
    """What `run()` raises: a RunFailed carrying the Result that failed."""
    return RunFailed(Result(1, command=command))


# --- the hook adapters -------------------------------------------------------


def _event(**over):
    """A harness payload, defaulted to the fields the hooks actually read."""
    tool_input = tasks.ToolInput(
        file_path=over.pop("file_path", ""), command=over.pop("command", "")
    )
    return tasks.HookEvent(tool_input=tool_input, **over)


def _refuses(command: str) -> bool:
    """Whether `hooks.pre-bash` would block *command*."""
    from footman import Failed

    try:
        tasks.pre_bash(_event(command=command))
    except Failed:
        return True
    return False


BLOCKED = [
    # What it exists for: the gate's verdict replaced by the filter's.
    "uv run fm check | tail -4",
    "uv run fm check 2>&1 | head",
    "fm check|tail -1",
    "cd sub && uv run fm check | tail -2",
]

ALLOWED = [
    # Bare, or redirected — both keep the exit code.
    "uv run fm check",
    "uv run fm check > /tmp/gate.log 2>&1",
    # A *mention* of footman is not a use of it. A path carrying the name and
    # an honest pipe blocked a real command before the detector was anchored
    # to a command position.
    "git show abc -- src/footman/_provision.py | head -14",
    'rg "fm check" | head -3',
    # A gate that decides nothing downstream still stands on its own.
    "fm check && echo done | tail -1",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_pre_bash_blocks_a_piped_gate(command):
    assert _refuses(command)


@pytest.mark.parametrize("command", ALLOWED)
def test_pre_bash_allows_a_mention_or_an_honest_pipe(command):
    assert not _refuses(command)


# --- the push guard ----------------------------------------------------------


def test_pre_bash_refuses_a_conflicting_push(monkeypatch):
    probed: list[str | None] = []

    def conflict(repo):
        probed.append(repo)
        return True

    monkeypatch.setattr(tasks, "_push_conflicts", conflict)
    assert _refuses("git push")
    assert _refuses("git push -u origin worktree-x")
    assert probed == [None, None]  # plain pushes probe the cwd's checkout


def test_pre_bash_hands_the_probe_a_dash_C_checkout(monkeypatch):
    probed: list[str | None] = []

    def conflict(repo):
        probed.append(repo)
        return True

    monkeypatch.setattr(tasks, "_push_conflicts", conflict)
    assert _refuses("git -C sub push")
    assert probed == ["sub"]


PUSH_EXEMPT = [
    # Deletions, tags and main itself are not "publishing a stale branch".
    "git push origin --delete worktree-x",
    "git push -d origin worktree-x",
    "git push --tags",
    "git push origin main",
    # Only the tag is pushed in the release flow.
    "git push origin v0.31.0 --tags",
    # A mention is not a push; a non-push git command is not this guard's.
    'rg "git push" | cat',
    "git mount",
]


@pytest.mark.parametrize("command", PUSH_EXEMPT)
def test_pre_bash_exempts_what_is_not_a_stale_branch_publish(command, monkeypatch):
    monkeypatch.setattr(tasks, "_push_conflicts", lambda repo: True)
    assert not _refuses(command)


def test_pre_bash_lets_a_clean_push_through(monkeypatch):
    monkeypatch.setattr(tasks, "_push_conflicts", lambda repo: False)
    assert not _refuses("git push -u origin worktree-x")


def _scratch_git(repo, *args: str) -> str:
    """Run git in the scratch repo, blind to the machine's own config — the
    global config would sign every commit through 1Password."""
    import os
    import subprocess

    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return done.stdout.strip()


def test_the_probe_is_the_real_test_merge(tmp_path):
    """`_push_conflicts` answers what GitHub's test-merge would: True only
    when the branch genuinely conflicts with the last-seen origin/main.
    The scratch repo has no remote, so the probe's fetch fails — covering
    the fail-open path — and the ref answers, as an offline clone's would."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scratch_git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _scratch_git(repo, "add", ".")
    _scratch_git(repo, "commit", "-qm", "base")
    _scratch_git(repo, "checkout", "-qb", "feature")
    (repo / "f.txt").write_text("feature\n", encoding="utf-8")
    _scratch_git(repo, "commit", "-aqm", "feature edit")
    _scratch_git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("moved\n", encoding="utf-8")
    _scratch_git(repo, "commit", "-aqm", "main moved")
    moved = _scratch_git(repo, "rev-parse", "main")
    _scratch_git(repo, "update-ref", "refs/remotes/origin/main", moved)

    _scratch_git(repo, "checkout", "-q", "feature")
    assert tasks._push_conflicts(str(repo)) is True  # both edited f.txt

    _scratch_git(repo, "checkout", "-q", "main")
    assert tasks._push_conflicts(str(repo)) is False  # HEAD is origin/main


def test_the_probe_stays_quiet_outside_its_jurisdiction(tmp_path):
    """No origin/main to compare against — a scratch checkout, someone
    else's clone — is not this guard's business: quiet, never a block."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scratch_git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _scratch_git(repo, "add", ".")
    _scratch_git(repo, "commit", "-qm", "base")
    assert tasks._push_conflicts(str(repo)) is False


def test_a_worktree_is_found_from_anywhere_inside_it(tmp_path):
    """A worktree's `.git` is a *file*, not a directory, so the test is
    `exists`. Getting that wrong sends every worktree's gate to the main
    checkout, which is the bug this whole path exists to avoid."""
    checkout = tmp_path / "repo"
    (checkout / "src" / "deep").mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: /elsewhere\n")  # a worktree, not a clone

    assert tasks._worktree(str(checkout)) == checkout
    assert tasks._worktree(str(checkout / "src" / "deep")) == checkout
    assert tasks._worktree("") is None


def test_a_path_outside_any_checkout_has_no_worktree(tmp_path):
    """`None` rather than a guess: the caller gates where it stands instead.

    pytest's tmp_path has no repository above it, which is the real shape of
    this — a hook fired from somewhere that is simply not a checkout.
    """
    assert tasks._worktree(str(tmp_path)) is None


def test_two_sessions_do_not_answer_for_each_other():
    """The recorded checkout keys on the session. Several agents share this
    repo through worktrees, and a shared note would let one session's edits
    decide where another's gate runs."""
    assert tasks._gate_dir("aaa") != tasks._gate_dir("bbb")
    assert "aaa" in tasks._gate_dir("aaa").name


def test_an_edit_records_the_checkout_it_landed_in(tmp_path, monkeypatch):
    """Every edit, not only the Python ones: which checkout a session is
    working in is not a fact about file extensions."""
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: /elsewhere\n")
    note = tmp_path / "note"
    monkeypatch.setattr(tasks, "_gate_dir", lambda _session: note)

    tasks.post_edit(_event(file_path=str(checkout / "README.md")))
    assert note.read_text(encoding="utf-8") == str(checkout)


def test_an_edited_python_file_is_formatted_and_linted(tmp_path, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: tmp_path / "note")
    monkeypatch.setattr(tasks, "format", lambda: ran.append("format"))
    monkeypatch.setattr(tasks, "lint", lambda: ran.append("lint"))

    tasks.post_edit(_event(file_path=str(tmp_path / "x.py")))
    assert ran == ["format", "lint"]

    ran.clear()
    tasks.post_edit(_event(file_path=str(tmp_path / "notes.md")))
    assert ran == []  # nothing to format in a markdown file


def test_a_failing_format_blocks_the_agent_with_code_two(tmp_path, monkeypatch):
    """Exit 2 is the harness's blocking verdict — anything else is advice the
    model may ignore."""

    def boom():
        raise _failed_run()

    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: tmp_path / "note")
    monkeypatch.setattr(tasks, "format", boom)
    with pytest.raises(Failed) as failed:
        tasks.post_edit(_event(file_path=str(tmp_path / "x.py")))
    assert failed.value.code == 2


def test_a_stop_gates_the_checkout_the_session_edited(tmp_path, monkeypatch):
    """The point of the whole mechanism. A hook runs in the session's *shell*
    directory, which drifts — `cd` outside the workspace and the harness
    resets it to the project root. Gating there means gating another agent's
    half-finished tree.
    """
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    (elsewhere / ".git").write_text("gitdir: /elsewhere\n")
    note = tmp_path / "note"
    note.write_text(str(elsewhere), encoding="utf-8")
    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: note)

    here: list[str] = []
    spawned: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(tasks, "check", lambda: here.append("local"))
    monkeypatch.setattr(tasks, "run", lambda argv, **kw: spawned.append((argv, kw)))

    tasks.stop(_event(session_id="s"))

    assert here == []  # not gated where the shell happens to be
    assert spawned and spawned[0][1]["cwd"] == elsewhere
    assert spawned[0][0] == ["uv", "run", "fm", "check"]


def test_a_stop_in_the_edited_checkout_gates_in_process(tmp_path, monkeypatch):
    """No subprocess when there is nowhere else to go — the common case, and
    the one that must stay fast."""
    note = tmp_path / "note"
    note.write_text(str(Path.cwd()), encoding="utf-8")
    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: note)

    ran: list[str] = []
    monkeypatch.setattr(tasks, "check", lambda: ran.append("local"))
    monkeypatch.setattr(tasks, "run", lambda *a, **k: pytest.fail("should not spawn"))

    tasks.stop(_event(session_id="s"))
    assert ran == ["local"]


def test_a_session_that_edited_nothing_gates_where_it_stands(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: tmp_path / "absent")
    ran: list[str] = []
    monkeypatch.setattr(tasks, "check", lambda: ran.append("local"))
    tasks.stop(_event(session_id="s"))
    assert ran == ["local"]


def test_a_red_gate_elsewhere_says_where(tmp_path, monkeypatch):
    """A failure in another directory must not read as a failure here, or the
    agent tries to fix a tree it never touched."""
    elsewhere = tmp_path / "other-tree"
    elsewhere.mkdir()
    (elsewhere / ".git").write_text("gitdir: /elsewhere\n")
    note = tmp_path / "note"
    note.write_text(str(elsewhere), encoding="utf-8")
    monkeypatch.setattr(tasks, "_gate_dir", lambda _s: note)

    def boom(*_a, **_k):
        raise _failed_run("fm check")

    monkeypatch.setattr(tasks, "run", boom)
    with pytest.raises(Failed) as failed:
        tasks.stop(_event(session_id="s"))
    assert failed.value.code == 2
    assert "other-tree" in failed.value.reason


def test_a_retrying_stop_never_pings_back(monkeypatch):
    """The harness re-runs Stop after a blocking verdict. Gating again would
    loop forever."""
    monkeypatch.setattr(tasks, "check", lambda: pytest.fail("should not re-gate"))
    tasks.stop(_event(stop_hook_active=True))


# --- the generators the docs site includes -----------------------------------


def test_the_home_page_quotes_the_newest_release(tmp_path, monkeypatch):
    """Rolling the changelog for a release updates the home page by
    construction — there is no second copy to forget."""
    monkeypatch.chdir(tmp_path)
    Path("CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Pending.\n\n"
        "## [0.23.0] — 2026-07-27\n\n### Added\n\n- The newest thing.\n\n"
        "## [0.22.0] — 2026-07-01\n\n- Older.\n",
        encoding="utf-8",
    )
    tasks._write_latest_changes()

    out = Path("docs/_generated/latest-changes.md").read_text(encoding="utf-8")
    assert "Latest release: 0.23.0 — 2026-07-27" in out
    assert "The newest thing." in out
    assert "Older." not in out  # only the newest section
    assert "Pending." not in out  # and never the unreleased one


def test_a_changelog_with_no_release_yet_writes_nothing(tmp_path, monkeypatch):
    """A fresh fork has only `[Unreleased]`. Skipped quietly rather than
    failing a docs build over a file that is simply young."""
    monkeypatch.chdir(tmp_path)
    Path("CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n", encoding="utf-8"
    )
    tasks._write_latest_changes()
    assert Path("docs/_generated/latest-changes.md").read_text(encoding="utf-8") == ""


def test_a_cast_that_lost_its_interaction_fails_the_build(tmp_path):
    """A recording whose answer landed too early still renders a valid SVG —
    just a mute one. Without this the site ships a cast that shows a prompt
    and no reply, and no test would notice."""
    svg = tmp_path / "demo.svg"
    svg.write_text(
        "<svg><text>project name?</text><text>&#160;demo</text></svg>", encoding="utf-8"
    )
    tasks._assert_cast_captured(svg, ["project name?", "demo"])

    with pytest.raises(RuntimeError, match=r"dropped \['Scaffolding'\]"):
        tasks._assert_cast_captured(svg, ["Scaffolding"])


# --- the task bodies that are one call to a tool -----------------------------


@pytest.mark.parametrize(
    ("call", "attr", "expected"),
    [
        (lambda: tasks.lint(), "ruff", (("check", (".",)), {"fix": False})),
        (lambda: tasks.lint(fix=True), "ruff", (("check", (".",)), {"fix": True})),
        (lambda: tasks.format(), "ruff_format", ((None, (".",)), {"check": False})),
        # `warnings=True`: basedpyright exits 0 on warnings, so the gate
        # would pass over one — and two had been passing over for as long as
        # they had existed.
        (
            lambda: tasks.typecheck(),
            "basedpyright",
            ((None, ()), {"warnings": True}),
        ),
    ],
)
def test_a_wrapper_task_calls_its_tool_over_the_whole_repo(
    call, attr, expected, monkeypatch
):
    """`SRC` is the whole repo, as CI lints it. Anything narrower lets a
    tracked file outside src/tests pass the gate and fail the build — which
    tracking `notes/` proved within minutes."""
    seen: list[tuple[object, ...]] = []

    class Recorder:
        def __init__(self, verb=None):
            self._verb = verb

        def __getattr__(self, name):
            return Recorder(name)

        def __call__(self, *args, **kwargs):
            seen.append(((self._verb, args), kwargs))

    monkeypatch.setattr(tasks, attr, Recorder())
    call()
    assert seen == [expected]


def test_the_test_task_forwards_its_arguments_verbatim(monkeypatch):
    """`fm test -k thing -x` has to reach pytest unchanged, or the escape
    hatch is not one."""
    seen: list[tuple[str, object]] = []

    class Pytest:
        def opts(self, **kwargs):
            seen.append(("opts", kwargs))
            return lambda *args: seen.append(("call", args))

    monkeypatch.setattr(tasks, "pytest", Pytest())
    tasks.test("-k", "thing", "-x")
    assert seen == [("opts", {"in_process": False}), ("call", ("-k", "thing", "-x"))]


def test_the_gate_gives_every_run_its_own_coverage_file(monkeypatch):
    """Two `fm check` runs sharing the repo's .coverage — a hook racing a
    manual run — clobber the SQLite file mid-write, and the reporter then
    reports a bogus partial total with every test passing."""
    import contextlib

    runs: list[dict[str, Any]] = []

    class Block:
        # The block form the migrated gate uses: swallow the queued task
        # calls, run the one lifted step item so its run() is observed.
        def __call__(self, item):
            item()

    @contextlib.contextmanager
    def fake_parallel():
        yield Block()

    monkeypatch.setattr(tasks, "run", lambda cmd, **kw: runs.append({"cmd": cmd, **kw}))
    monkeypatch.setattr(tasks, "parallel", fake_parallel)
    monkeypatch.setattr(tasks, "format", lambda check=False: None)
    monkeypatch.setattr(tasks, "lint", lambda: None)
    monkeypatch.setattr(tasks, "typecheck", lambda: None)
    monkeypatch.setattr(tasks, "typecomplete", lambda: None)

    tasks.check()
    tasks.check()

    files = [r["env"]["COVERAGE_FILE"] for r in runs]
    assert len(files) == 2
    assert files[0] != files[1]  # never the repo's own .coverage, never shared


def test_sync_goes_through_the_projects_own_uv(monkeypatch):
    """A mismatched system uv silently rewriting the lock is the source of
    the one-line churn this avoids."""
    seen: list[str] = []

    class Uv:
        def sync(self):
            seen.append("sync")

    monkeypatch.setattr(tasks, "uv", Uv())
    tasks.sync()
    assert seen == ["sync"]


def test_the_dist_tasks_build_and_clean(monkeypatch):
    seen: list[tuple[str, ...] | str] = []
    monkeypatch.setattr(tasks, "uv", lambda *args: seen.append(args))
    monkeypatch.setattr(tasks, "run", lambda cmd, **kw: seen.append(cmd))
    tasks.build()
    tasks.clean()
    assert seen == [("build",), "rm -rf dist"]


def test_the_docs_tasks_regenerate_before_they_serve(monkeypatch):
    """`serve` writes llms.txt first: a live-reload session that served a
    stale index would show one, and nobody would think to look."""
    order: list[str] = []
    monkeypatch.setattr(tasks, "_write_llms_txt", lambda: order.append("llms"))
    monkeypatch.setattr(tasks, "run", lambda cmd, **kw: order.append(cmd))
    tasks.serve()
    assert order == ["llms", "zensical serve"]


def test_coverage_returns_the_totals_it_measures(monkeypatch):
    """`docs.coverage` is the structured-returns dogfood: the same pytest run
    that writes the HTML also writes a JSON totals file, and the task returns
    them as its declared `CoverageReport` rather than throwing them away."""
    import json
    import re

    seen: list[str] = []

    def fake_run(cmd: str, **kw: Any) -> None:
        seen.append(cmd)
        where = re.search(r"json:(\S+)", cmd)
        assert where is not None
        target = Path(where[1])
        totals = {"percent_covered": 92.034, "num_statements": 9000}
        target.write_text(
            json.dumps({"totals": {**totals, "missing_lines": 717}}), "utf-8"
        )

    monkeypatch.setattr(tasks, "run", fake_run)
    report = tasks.coverage()
    assert seen[0].startswith("pytest --cov --cov-report=html:docs/htmlcov ")
    assert seen[0].endswith(" -q")
    assert report == tasks.CoverageReport(
        percent=92.03,
        statements=9000,
        missing=717,
        report=Path("docs/htmlcov/index.html"),
    )


def test_the_coverage_report_shape_stays_describable():
    """The declared contract must actually bake: if `CoverageReport` (or an
    import it needs, like a runtime `Path`) ever drifts outside the
    describable set, the schema would silently vanish from the manifest,
    the envelope, and `--describe` — this is the tripwire."""
    from footman._manifest import returned_spec

    assert returned_spec(tasks.CoverageReport) == {
        "kind": "object",
        "name": "CoverageReport",
        "fields": {
            "percent": {"kind": "float"},
            "statements": {"kind": "int"},
            "missing": {"kind": "int"},
            "report": {"kind": "path"},
        },
    }


# --- the scratch projects the documentation casts are recorded against -------


def test_the_suggest_demo_is_the_documented_example_verbatim(tmp_path, monkeypatch):
    """Extracted from typing.md rather than retyped, so the recording
    exercises the documented code by construction and the two cannot drift."""
    monkeypatch.setattr(tasks, "run", lambda *a, **k: None)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    demo = Path(tasks._scaffold_suggest_demo())

    written = (demo / "tasks.py").read_text(encoding="utf-8")
    section = Path("docs/typing.md").read_text(encoding="utf-8")
    section = section.split("## Dynamic completion", 1)[1]
    assert written and written in section  # verbatim, not paraphrased
    assert (demo / "pyproject.toml").exists()


def test_the_interactive_demo_covers_each_interactive_shape(tmp_path, monkeypatch):
    """One task per shape the docs claim — an `ask()` parameter, a `confirm=`
    gate, an `interactive=True` wizard — so a cast exists for each."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    demo = Path(tasks._scaffold_interactive_demo())
    body = (demo / "tasks.py").read_text(encoding="utf-8")
    assert "ask()" in body
    assert 'confirm="Deploy to production?"' in body
    assert "interactive=True" in body


# --- llms.txt: the agent-facing index, derived from the nav ------------------


def _toml_nav(items) -> str:
    """The nav as zensical spells it: an array of single-key inline tables,
    a section's value being another such array."""
    out = []
    for item in items:
        for title, value in item.items():
            rendered = _toml_nav(value) if isinstance(value, list) else f'"{value}"'
            out.append(f'{{ "{title}" = {rendered} }}')
    return "[" + ", ".join(out) + "]"


def _site(tmp_path, nav, pages):
    """A miniature docs site: a nav, and the markdown it points at."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "zensical.toml").write_text(
        f"[project]\nnav = {_toml_nav(nav)}\n", encoding="utf-8"
    )
    for name, body in pages.items():
        (tmp_path / "docs" / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_llms_txt_lists_every_page_in_nav_order(tmp_path, monkeypatch):
    """Nav order, not directory order — the index reads as the site reads.
    A nested section contributes its pages, not itself: there is no page to
    link a section to."""
    _site(
        tmp_path,
        [{"Home": "index.md"}, {"Guides": [{"Typing": "typing.md"}]}],
        {
            "index.md": "# footman\n\nA task runner. Second sentence here.\n",
            "typing.md": "# Typing\n\nTypes become flags.\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    tasks._write_llms_txt()

    index = (tmp_path / "docs" / "llms.txt").read_text(encoding="utf-8")
    lines = [ln for ln in index.splitlines() if ln.startswith("- [")]
    assert lines == [
        "- [Home](https://willemkokke.github.io/footman/): A task runner.",
        "- [Typing](https://willemkokke.github.io/footman/typing/):"
        " Types become flags.",
    ]


def test_the_description_is_the_first_prose_sentence_and_nothing_else(
    tmp_path, monkeypatch
):
    """Headings, badges, admonitions, block quotes, snippet includes and
    fenced code are all skipped: a description taken from a badge line reads
    as noise, and one taken from inside a fence is not prose at all."""
    _site(
        tmp_path,
        [{"Page": "page.md"}],
        {
            "page.md": (
                "---\ntitle: Front matter\n---\n"
                "# Heading\n\n"
                "[![badge](x)](y)\n\n"
                "!!! note\n\n"
                "> quoted\n\n"
                "--8<-- 'snippet.md'\n\n"
                "```python\nnot_prose = 1\n```\n\n"
                "The real first sentence. And a second one.\n"
                "\n"
                "A second paragraph, which the description must not reach.\n"
            )
        },
    )
    monkeypatch.chdir(tmp_path)
    tasks._write_llms_txt()

    index = (tmp_path / "docs" / "llms.txt").read_text(encoding="utf-8")
    assert "): The real first sentence." in index
    for noise in ("badge", "quoted", "snippet", "not_prose", "Front matter"):
        assert noise not in index
    assert "second paragraph" not in index  # the description stops at the first


def test_llms_full_inlines_the_changelog_and_skips_the_coverage_report(
    tmp_path, monkeypatch
):
    """`changelog.md` is a snippet include, so the index would otherwise
    describe the include directive rather than the changelog. `coverage.md`
    is an embedded HTML report — there is nothing in it for a reader."""
    _site(
        tmp_path,
        [{"Changelog": "changelog.md"}, {"Coverage": "coverage.md"}],
        {
            "changelog.md": "--8<-- 'CHANGELOG.md'\n",
            "coverage.md": "# Coverage\n\n<iframe src=htmlcov></iframe>\n",
        },
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\nEverything notable is here.\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    tasks._write_llms_txt()

    index = (tmp_path / "docs" / "llms.txt").read_text(encoding="utf-8")
    full = (tmp_path / "docs" / "llms-full.txt").read_text(encoding="utf-8")
    assert "Everything notable is here." in index  # the changelog, not the include
    assert "Coverage" not in index and "iframe" not in full
    assert "Everything notable is here." in full


def test_a_page_with_no_prose_still_gets_a_link(tmp_path, monkeypatch):
    """A page that is all headings has no sentence to quote, and is listed
    without one rather than omitted."""
    _site(tmp_path, [{"Bare": "bare.md"}], {"bare.md": "# Bare\n\n## Only headings\n"})
    monkeypatch.chdir(tmp_path)
    tasks._write_llms_txt()
    index = (tmp_path / "docs" / "llms.txt").read_text(encoding="utf-8")
    assert "- [Bare](https://willemkokke.github.io/footman/bare/)\n" in index
