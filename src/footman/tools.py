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
- `strict=off` → `--no-strict` (disable a default-on flag; `off` is the
  `footman.tools.off` sentinel — `no_strict=True` is the same thing by name)
- `output_format="github"` → `--output-format github`
- `select=["E", "F"]` → `--select E --select F` (an empty list/tuple is
  omitted entirely, so a task param's default passes straight through)
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

# Every module import is aliased private so `tools.<name>` never resolves to it:
# module attribute lookup beats module `__getattr__`, so a public `run`/`sys`
# would make `tools.run`/`tools.sys` the imported object instead of a Tool —
# typechecking as Tools (per the stub) but crashing at runtime (F50, F53).
import re as _re
import subprocess as _subprocess
import sys as _sys
import threading as _threading
from collections.abc import Iterator
from typing import Any

from footman.context import Invocation as _Invocation
from footman.context import run as _run

_version_cache: dict[str, tuple[int, ...]] = {}


class _Off:
    """The value that disables a flag: `flag=off` → `--no-flag`.

    `False`/`None` mean *omit* — so a task parameter's default flows through
    untouched — which leaves no way to spell a negation. Hence an explicit
    sentinel: `strict=off` turns a default-on flag off. Equivalent to naming
    the negation directly (`no_strict=True`), but reads as intent and lets a
    variable drive it (`strict=on_by_default and off`).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "off"


off = _Off()


# How a tool spells "off" when it is *not* `--no-<name>`. Only the
# exceptions live here, extracted from the tools themselves (click states
# it as `secondary_opts`) rather than assumed: `mkdocs build --no-clean`
# is rejected outright — the flag is `--dirty`. Regenerate with
# `fm footman tools negations`.
_NEGATIONS: dict[str, dict[str, str]] = {
    "mkdocs": {
        "clean": "--dirty",
        "use_directory_urls": "--no-directory-urls",
    },
}


def _negation(tool: str, key: str) -> str:
    """The flag that turns *key* off for *tool*."""
    known = _NEGATIONS.get(tool, {})
    if key in known:
        return known[key]
    return "--no-" + key.rstrip("_").replace("_", "-")


def _emit(kwargs: dict[str, Any], tool: str = "") -> Iterator[tuple[str, str | None]]:
    """The one translation: keyword arguments → `(flag, value)` tokens.

    `value` is None for a switch (`--fix`) or a negation (`--dirty`); a
    string for a valued option; and the pair repeats for each item of a
    list. Both the executed argv (`_flags`) and the shown command line
    (`_show_parts`) are built from this, so they can never disagree about
    what a call means — only about how to spell it.
    """
    for key, value in kwargs.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        if value is off:
            yield _negation(tool, key), None
            continue
        flag = f"-{name}" if len(name) == 1 else f"--{name}"
        if value is True:
            yield flag, None
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            yield flag, str(item)


def _flags(kwargs: dict[str, Any], tool: str = "") -> list[str]:
    """Translate keyword arguments into CLI flags, for execution."""
    argv: list[str] = []
    for flag, value in _emit(kwargs, tool):
        argv.append(flag)
        if value is not None:
            argv.append(value)
    return argv


def _show_parts(
    argv0: str, base: list[str], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[str, str], ...]:
    """The invocation as role-tagged tokens, for a readable, painted line.

    The same call the runtime executes, spelled for a human: options in
    their separated form (`--select E`, never `--select=E`), values shell-
    quoted so the line stays copy-pasteable, and every token tagged with
    its role so `_describe.paint_cli` can colour it the way `--help` colours
    a usage line. Execution can tokenise differently (attached, in-process);
    this is what footman *says* it ran.
    """
    parts: list[tuple[str, str]] = [("prog", argv0)]
    parts += [("group", verb) for verb in base]
    parts += [("req", _quote(str(a))) for a in args]
    for flag, value in _emit(kwargs, argv0):
        parts.append(("opt", flag))
        if value is not None:
            parts.append(("value", _quote(value)))
    return tuple(parts)


def _quote(text: str) -> str:
    """Shell-quote a token so the shown line round-trips through a paste."""
    import shlex

    return shlex.quote(text)


def _console_entrypoint(name: str) -> Any | None:
    """The `[console_scripts]` EntryPoint named *name*, UNLOADED, or None.

    Returning the EntryPoint rather than its target keeps the tool's import
    deferred: the module is only imported when `.load()` is called, inside
    the callable footman runs. So a dry-run — or a branch you never take —
    imports nothing, while the existence check here (pure metadata, no tool
    code) is still cheap enough to decide subprocess-vs-in-process eagerly.
    """
    from importlib.metadata import entry_points

    for ep in entry_points(group="console_scripts", name=name):
        return ep
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
_argv_lock = _threading.Lock()


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
        tail = [*self._base, *map(str, args), *_flags(kwargs, self._argv0)]
        argv = [self._argv0, *tail]
        # One structured view of the call, so the shown line reads well
        # (separated flags, role-coloured) no matter how it executes.
        show = _Invocation(
            _show_parts(self._argv0, self._base, args, kwargs), tuple(argv)
        )
        wanted = self._prefer_in_process if in_process is None else in_process
        if wanted:
            ep = _console_entrypoint(self._argv0)  # metadata only — no import
            if ep is None:
                if in_process is True:  # a demand can't be met — fail fast
                    raise ValueError(
                        f"{self._argv0}: in_process=True, but no installed "
                        f"console_scripts entry point named {self._argv0!r}"
                    )
                return _run(argv, nofail=nofail, _show=show)  # prefer → subproc

            def _invoke() -> Any:
                entry = ep.load()  # the tool's import — deferred to execution,
                # so a dry-run of this call imports nothing.
                if _accepts_args(entry):
                    return entry(tail)  # click / main(argv): lock-free, parallel
                with _argv_lock:  # legacy zero-arg main(): patch argv, serialised
                    saved = _sys.argv
                    _sys.argv = argv
                    try:
                        return entry()
                    finally:
                        _sys.argv = saved

            return _run(_invoke, nofail=nofail, _show=show)
        return _run(argv, nofail=nofail, _show=show)

    def installed_version(self) -> tuple[int, ...]:
        """The installed binary's version, as a comparable int tuple.

        Runs `<tool> --version` directly (never through the task context, so
        dry-run/recording still see the truth) and caches per process. For a
        tool that spells it differently, fall back to calling it yourself.
        """
        if self._argv0 not in _version_cache:
            # Decode as UTF-8 with replacement (F39): a tool that prints a
            # non-ASCII glyph in its --version must not crash the read on a
            # locale-encoded pipe (cp1252 on Windows).
            out = _subprocess.run(
                [self._argv0, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            match = _re.search(r"(\d+(?:\.\d+)+)", out.stdout or out.stderr)
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
            return _run(_pytest.main, list(args), title=title, nofail=nofail)
    return _run(["pytest", *args], nofail=nofail)


def python(*args: str, nofail: bool = False) -> int:
    """Run the current interpreter. `python("-m", "build")`."""
    return _run([_sys.executable, *args], nofail=nofail)


def sh(command: str, nofail: bool = False) -> int:
    """Run a command line given as a single string."""
    return _run(command, nofail=nofail)


def __getattr__(name: str) -> Tool:
    # Any executable is a tool: `tools.terraform("plan")` needs no declaration.
    if name.startswith("_"):
        raise AttributeError(name)
    return Tool(name.replace("_", "-"))
