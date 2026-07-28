"""PEP 723 inline script metadata: reading the block, and the uv commands."""

from __future__ import annotations

import textwrap
from pathlib import Path

from footman import _script

BLOCK = """\
# /// script
# requires-python = ">=3.11"
# dependencies = ["footman", "rich"]
# ///
from footman import task

@task
def build(): ...
"""


def _write(tmp_path: Path, text: str, name: str = "tasks.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_a_block_reads_as_metadata(tmp_path):
    meta, warning = _script.read_block(_write(tmp_path, BLOCK))
    assert warning is None
    assert meta == {"requires-python": ">=3.11", "dependencies": ["footman", "rich"]}


def test_find_uv_falls_back_to_path(tmp_path, monkeypatch):
    # No uv beside this runner's own scripts directory → the PATH answer.
    monkeypatch.setattr("sysconfig.get_path", lambda name: str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: "/somewhere/uv")
    assert _script.find_uv() == "/somewhere/uv"


def test_no_block_and_no_file_are_both_simply_not_scripts(tmp_path):
    assert _script.read_block(_write(tmp_path, "x = 1\n")) == (None, None)
    assert _script.read_block(tmp_path / "ghost.py") == (None, None)


def test_a_bare_hash_line_inside_the_block_is_content(tmp_path):
    # PEP 723: `#` alone is an empty line, `# ` prefixes the rest.
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        #
        # requires-python = ">=3.11"
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert warning is None
    assert meta == {"dependencies": ["footman"], "requires-python": ">=3.11"}


def test_a_non_script_block_is_not_ours(tmp_path):
    # The fence names its type; only `script` is this feature's.
    path = _write(
        tmp_path,
        """\
        # /// pyproject
        # [tool.whatever]
        # x = 1
        # ///
        """,
    )
    assert _script.read_block(path) == (None, None)


def test_malformed_toml_warns_and_declines(tmp_path):
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = [oops
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None
    assert "tasks.py" in warning and "running without it" in warning


def test_two_script_blocks_warn_and_decline(tmp_path):
    # Separated by code: two real blocks. (Back to back, the PEP's own
    # regex reads them as one malformed block — warned either way.)
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        # ///
        x = 1
        # /// script
        # dependencies = ["rich"]
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None and "2 script blocks" in warning


def test_back_to_back_blocks_are_a_read_failure_not_a_silent_drop(tmp_path):
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        # ///
        # /// script
        # dependencies = ["rich"]
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None and "can't read" in warning


def test_declares_normalizes_the_requirement_name():
    def meta(*deps):
        return {"dependencies": list(deps)}

    assert _script.declares(meta("footman"), "footman")
    assert _script.declares(meta("Footman"), "footman")  # case
    assert _script.declares(meta("foot_man"), "foot-man")  # separators
    assert _script.declares(meta("footman[docs]>=0.25"), "footman")  # extras+pin
    assert _script.declares(meta("footman @ file:///tmp/x"), "footman")  # direct
    assert _script.declares(meta("rich", "footman ; python_version>'3.10'"), "footman")
    assert not _script.declares(meta("footmanx"), "footman")  # a prefix is not a name
    assert not _script.declares(meta(), "footman")
    assert not _script.declares({}, "footman")
    assert not _script.declares({"dependencies": "footman"}, "footman")  # not a list


def _fake_uv_calls(monkeypatch, *, sync_code=0, find_code=0, python="/env/bin/python"):
    ran: list[list[str]] = []

    class Done:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(cmd, **kwargs):
        ran.append(list(cmd))
        return Done(sync_code) if cmd[1] == "sync" else Done(find_code, python + "\n")

    monkeypatch.setattr(_script.subprocess, "run", fake_run)
    monkeypatch.setattr(_script, "find_uv", lambda: "/fake/uv")
    return ran


def test_child_python_never_reaches_for_the_network(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    ran = _fake_uv_calls(monkeypatch)
    assert _script.child_python(path) == "/env/bin/python"
    assert "--offline" in ran[0]  # a keystroke builds nothing it must download


def test_child_python_declines_an_unmaterialized_environment(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    ran = _fake_uv_calls(monkeypatch, sync_code=1)  # offline sync can't build it
    assert _script.child_python(path) is None
    assert len(ran) == 1  # and it never went on to ask for an interpreter


def test_child_python_declines_when_it_is_already_here(tmp_path, monkeypatch):
    import sys

    path = _write(tmp_path, BLOCK)
    _fake_uv_calls(monkeypatch, python=sys.executable)
    assert _script.child_python(path) is None  # re-execing would only loop


def test_child_python_declines_without_a_block_or_deps(tmp_path, monkeypatch):
    _fake_uv_calls(monkeypatch)
    assert _script.child_python(_write(tmp_path, "x = 1\n")) is None
    bare = _write(
        tmp_path, '# /// script\n# requires-python = ">=3.11"\n# ///\n', "b.py"
    )
    assert _script.child_python(bare) is None


def test_child_python_honours_the_belt_and_the_optout(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    _fake_uv_calls(monkeypatch)
    monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1")
    assert _script.child_python(path) is None
    monkeypatch.delenv("FOOTMAN_UV_REEXEC")
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")
    assert _script.child_python(path) is None


def test_reexec_child_carries_the_belt(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        _script.os, "execv", lambda f, a: calls.append((f, list(a))) or None
    )
    monkeypatch.setenv("VIRTUAL_ENV", "/some/other/venv")
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    _script.reexec_child("/env/bin/python", ["-c", "pass"])
    assert calls == [("/env/bin/python", ["/env/bin/python", "-c", "pass"])]
    import os

    assert os.environ["FOOTMAN_UV_REEXEC"] == "1"  # the child never loops
    assert "VIRTUAL_ENV" not in os.environ  # a script env is not the active one


def test_the_children_reexec_the_same_entry_they_are_spawned_with():
    # Drift guard: the refresh child re-runs *itself* inside the script
    # environment, so the one-liner it re-execs must be the one-liner the
    # hot path spawns. Two places, one string, asserted rather than hoped.
    import inspect

    from footman import _complete, _refresh

    spawn = inspect.getsource(_complete._spawn_refresh)
    child = inspect.getsource(_refresh)
    for entry in ("_refresh.refresh_cwd()", "_refresh.refresh_source(sys.argv[1])"):
        assert entry in spawn, entry
        assert entry in child, entry


def test_the_uv_command_lines():
    file = Path("/tmp/tasks.py")
    assert _script.sync_argv("/fake/uv", file) == [
        "/fake/uv",
        "sync",
        "--script",
        str(file),
        "--quiet",
    ]
    assert _script.sync_argv("/fake/uv", file, quiet=False, offline=True) == [
        "/fake/uv",
        "sync",
        "--script",
        str(file),
        "--offline",
    ]
    assert _script.find_argv("/fake/uv", file) == [
        "/fake/uv",
        "python",
        "find",
        "--script",
        str(file),
    ]
