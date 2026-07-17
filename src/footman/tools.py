"""Typed bridges to command-line tools, built on `footman.run`.

Every call runs through the current task context, so it inherits capture,
replay-on-failure, dry-run, recording, and `--json` steps.

footman deliberately does **not** transcribe each tool's flags into Python
parameters. Transcription drifts: the wrapper pins the flag-set its author
copied, the tool moves on, and one day `show_source=True` emits a flag the
installed binary rejects. Instead, keyword arguments translate
*mechanically* — the installed tool's own CLI stays the single source of
truth, at whatever version it is:

- `fix=True` → `--fix` (`False`/`None` → omitted entirely)
- `output_format="github"` → `--output-format github`
- `select=["E", "F"]` → `--select E --select F`
- `x=1` (single letter) → `-x 1`
- a trailing underscore escapes Python keywords: `import_="x"` → `--import x`

Attribute access chains subcommands (`tools.docker.compose.up(detach=True)`
→ `docker compose up --detach`), positional strings pass through verbatim,
and *any* executable works without being declared here:
`tools.terraform("plan")` just runs `terraform plan`.

`tool.installed_version()` returns the installed binary's version as an int
tuple (cached per process, resolved outside the task context so dry-run and
recording can't lie to it) — for the rare case where a task must branch on
the tool's actual CLI generation.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from typing import Any

from footman.context import run

_version_cache: dict[str, tuple[int, ...]] = {}


def _flags(kwargs: dict[str, Any]) -> list[str]:
    """Translate keyword arguments into CLI flags, mechanically."""
    argv: list[str] = []
    for key, value in kwargs.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        flag = f"-{name}" if len(name) == 1 else f"--{name}"
        if value is True:
            argv.append(flag)
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            argv += [flag, str(item)]
    return argv


def _console_entry(name: str) -> Any | None:
    """The installed `[console_scripts]` entry named *name*, loaded, or None."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="console_scripts", name=name):
        try:
            return ep.load()
        except Exception:
            return None
    return None


def _accepts_args(entry: Any) -> bool:
    """Can *entry* take the argument list directly (no sys.argv patching)?

    Click commands (`cli(args)`) and argv-parameter mains
    (`main(argv=None)`) both can — their first parameter is positional. Only
    a true zero-arg `main()` needs `sys.argv` patched, which is process-
    global and therefore serialised.
    """
    import inspect

    try:
        sig = inspect.signature(entry)
    except (ValueError, TypeError):
        return False
    positional = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    )
    return any(p.kind in positional for p in sig.parameters.values())


# Only the sys.argv-patching fallback needs serialising; argument-accepting
# entries (the overwhelming majority) run fully in parallel.
_argv_lock = threading.Lock()


class Tool:
    """One command-line tool; see the module docstring for the grammar.

    `in_process` (a reserved keyword, like `nofail`) runs a Python tool
    inside footman's process instead of spawning: the tool's own
    `[console_scripts]` entry point is resolved and invoked — the same
    no-transcription contract, minus the interpreter spawn. Beyond speed
    this matters for correctness: on macOS, SIP strips `DYLD_*` from child
    processes, so a tool that needs Homebrew's native libraries (mkdocs
    with cairo, say) can only see them in-process, where an env var set
    before the import sticks. Tools constructed with `in_process=True`
    default to it and fall back to a subprocess when no entry point is
    installed; `in_process=True` at the *call* is a demand and errors if
    the entry can't be found. Parallelism survives: entries that accept an
    argument list (click commands, `main(argv=None)` — detected from the
    signature) are called directly and capture through the per-task stdout
    router; only a legacy zero-arg `main()` needs `sys.argv` patched, and
    only those serialise.
    """

    def __init__(self, name: str, *base: str, in_process: bool = False) -> None:
        self._argv0 = name
        self._base = list(base)
        self._prefer_in_process = in_process

    def __getattr__(self, verb: str) -> Tool:
        if verb.startswith("_"):
            raise AttributeError(verb)
        sub = Tool(self._argv0, *self._base, verb.replace("_", "-"))
        sub._prefer_in_process = self._prefer_in_process
        return sub

    def __call__(
        self,
        *args: Any,
        nofail: bool = False,
        in_process: bool | None = None,
        **kwargs: Any,
    ) -> int:
        tail = [*self._base, *map(str, args), *_flags(kwargs)]
        wanted = self._prefer_in_process if in_process is None else in_process
        if wanted:
            entry = _console_entry(self._argv0)
            if entry is None and in_process is True:
                raise ValueError(
                    f"{self._argv0}: in_process=True, but no installed "
                    f"console_scripts entry point named {self._argv0!r}"
                )
            if entry is not None:
                title = " ".join([self._argv0, *tail])
                if _accepts_args(entry):
                    # Direct call — no global state, fully parallel.
                    return run(lambda: entry(tail), title=title, nofail=nofail)

                def _invoke() -> Any:  # zero-arg main: patch argv, serialised
                    with _argv_lock:
                        saved = sys.argv
                        sys.argv = [self._argv0, *tail]
                        try:
                            return entry()
                        finally:
                            sys.argv = saved

                return run(_invoke, title=title, nofail=nofail)
        return run([self._argv0, *tail], nofail=nofail)

    def installed_version(self) -> tuple[int, ...]:
        """The installed binary's version, as a comparable int tuple.

        Runs `<tool> --version` directly (never through the task context, so
        dry-run/recording still see the truth) and caches per process. For a
        tool that spells it differently, fall back to calling it yourself.
        """
        if self._argv0 not in _version_cache:
            out = subprocess.run(
                [self._argv0, "--version"], capture_output=True, text=True, timeout=30
            )
            match = re.search(r"(\d+(?:\.\d+)+)", out.stdout or out.stderr)
            if out.returncode != 0 or match is None:
                raise ValueError(
                    f"could not read a version from `{self._argv0} --version`"
                )
            _version_cache[self._argv0] = tuple(
                int(part) for part in match[1].split(".")
            )
        return _version_cache[self._argv0]


# Curated instances — the ones with a non-obvious executable name live here;
# everything else works through the module fallback below.
ruff = Tool("ruff")
ruff_format = Tool("ruff", "format")
basedpyright = Tool("basedpyright")
uv = Tool("uv")
git = Tool("git")
docker = Tool("docker")
bun = Tool("bun")
mkdocs = Tool("mkdocs", in_process=True)  # macOS: DYLD_* only survives in-process
zensical = Tool("zensical", in_process=True)
coverage = Tool("coverage", in_process=True)
cspell = Tool("cspell")
prek = Tool("prek")
markdownlint = Tool("markdownlint-cli2")


def pytest(*args: str, in_process: bool = True, nofail: bool = False) -> int:
    """Run pytest — in-process via `pytest.main` when available (no subprocess)."""
    if in_process:
        try:
            import pytest as _pytest
        except ImportError:
            pass
        else:
            title = " ".join(["pytest", *args])
            return run(_pytest.main, list(args), title=title, nofail=nofail)
    return run(["pytest", *args], nofail=nofail)


def python(*args: str, nofail: bool = False) -> int:
    """Run the current interpreter. `python("-m", "build")`."""
    return run([sys.executable, *args], nofail=nofail)


def sh(command: str, nofail: bool = False) -> int:
    """Run a command line given as a single string."""
    return run(command, nofail=nofail)


def __getattr__(name: str) -> Tool:
    # Any executable is a tool: `tools.terraform("plan")` needs no declaration.
    if name.startswith("_"):
        raise AttributeError(name)
    return Tool(name.replace("_", "-"))
