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
    # git add --all turns off as --ignore-removal (aka --no-all).
    "git": {
        "all": "--ignore-removal",
    },
}


def _negation(tool: str, key: str, *, single_dash: bool = False) -> str:
    """The flag that turns *key* off for *tool*.

    A single-dash tool (Go's `flag` package: `-fix`, not `--fix`) negates with a
    single dash too (`-no-fix`); a tool that spells it otherwise lives in
    `_NEGATIONS`, which wins here regardless of dash style.
    """
    known = _NEGATIONS.get(tool, {})
    if key in known:
        return known[key]
    dash = "-no-" if single_dash else "--no-"
    return dash + key.rstrip("_").replace("_", "-")


# Verbs that run *another* command: a wrapper's flags belong before the
# child's argv, or they leak past the tool into the child — `uv run
# --frozen pytest`, not `uv run pytest --frozen` (which hands `--frozen`
# to pytest). Dotted for nesting; extracted from each verb's usage line
# and checked by `fm footman tools audit`.
_WRAPPERS: dict[str, frozenset[str]] = {
    "uv": frozenset({"run", "tool.run"}),
    "coverage": frozenset({"run"}),
    "docker": frozenset({"run", "exec", "compose.run", "compose.exec"}),
    # python's own root is a wrapper: `python -v script.py` puts the
    # interpreter's options before the script, whose own args follow it.
    "python": frozenset({""}),
}


def _is_wrapper(argv0: str, base: list[str]) -> bool:
    """Whether the verb reached by *base* forwards to a wrapped command."""
    verbs = ".".join(token for token in base if not token.startswith("-"))
    return verbs in _WRAPPERS.get(argv0, frozenset())


def _emit(
    kwargs: dict[str, Any], tool: str = "", *, single_dash: bool = False
) -> Iterator[tuple[str, str | None]]:
    """The one translation: keyword arguments → `(flag, value)` tokens.

    `value` is None for a switch (`--fix`) or a negation (`--dirty`); a
    string for a valued option; and the pair repeats for each item of a
    list. Both the executed argv (`_flags`) and the shown command line
    (`_show_parts`) are built from this, so they can never disagree about
    what a call means — only about how to spell it.

    *single_dash* spells every long flag with one dash (`-fix`, not `--fix`) for
    Go-style tools whose `flag` package rejects the double-dash form.
    """
    for key, value in kwargs.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        if value is off:
            yield _negation(tool, key, single_dash=single_dash), None
            continue
        flag = f"-{name}" if single_dash or len(name) == 1 else f"--{name}"
        if value is True:
            yield flag, None
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            yield flag, str(item)


def _spell(flag: str, value: str | None, *, attach_long: bool) -> list[str]:
    """One option as argv tokens — the shared placement rule.

    A long option and its value can be one token (`--select=E`) or two
    (`--select E`). Two reads better, but three cases force *one*:

    * a value that starts with a dash would be read as the next option —
      `--format -%h` fails, `--format=-%h` works;
    * an optional-value option can't tell its value from the next
      positional across a space — `--abbrev 4` is ambiguous, `--abbrev=4`
      is not;
    * a short option's value, when it starts with a dash, must be
      concatenated (`-k-expr`), never `-k=expr`, which most tools reject.

    Execution attaches every long option (`attach_long=True`) so the second
    case is covered for tools footman can't inspect; the shown line only
    attaches where a space would actually break it, staying readable.
    """
    if value is None:
        return [flag]
    long = flag.startswith("--")
    dash = value.startswith("-")
    if long and (attach_long or dash):
        return [f"{flag}={value}"]
    if not long and dash:
        return [f"{flag}{value}"]
    return [flag, value]


def _flags(
    kwargs: dict[str, Any], tool: str = "", *, single_dash: bool = False
) -> list[str]:
    """Translate keyword arguments into CLI flags, for execution.

    Long options attach their value (`--select=E`) so an optional-value or
    dash-leading value can never be misread; short options stay separated
    unless the value forces concatenation. The shown line (`_show_parts`)
    spells the same call more readably; only the tokens differ.
    """
    argv: list[str] = []
    for flag, value in _emit(kwargs, tool, single_dash=single_dash):
        argv += _spell(flag, value, attach_long=True)
    return argv


def _show_parts(
    argv0: str,
    base: list[str],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    single_dash: bool = False,
) -> tuple[tuple[str, str], ...]:
    """The invocation as role-tagged tokens, for a readable, painted line.

    The same call the runtime executes, spelled for a human: options in
    their separated form (`--select E`, not `--select=E`) where a space is
    safe, attached only where separating would break the paste; values
    shell-quoted; every token tagged with its role so `_describe.paint_cli`
    can colour it the way `--help` colours a usage line.
    """
    parts: list[tuple[str, str]] = [("prog", argv0)]
    for token in base:
        # `base` holds the verb path, and — from `.opts()` — global flags
        # bound before a verb. A flag reads back in separated form so the
        # shown line stays readable (`--host tcp://x`, not `--host=tcp://x`).
        if token.startswith("--") and "=" in token:
            flag, _, value = token.partition("=")
            parts.append(("opt", flag))
            parts.append(("value", _quote(value)))
        elif token.startswith("-"):
            parts.append(("opt", token))
        else:
            parts.append(("group", token))
    arg_parts = [("req", _quote(str(a))) for a in args]
    flag_parts: list[tuple[str, str]] = []
    for flag, value in _emit(kwargs, argv0, single_dash=single_dash):
        if value is None:
            flag_parts.append(("opt", flag))
            continue
        # Decide placement on the raw value (a dash leads), quote for the
        # shown text. Readable where a space is safe; attached only where
        # separating would produce a line that doesn't run.
        quoted = _quote(value)
        if value.startswith("-"):
            glue = "=" if flag.startswith("--") else ""
            flag_parts.append(("opt", f"{flag}{glue}{quoted}"))
        else:
            flag_parts.append(("opt", flag))
            flag_parts.append(("value", quoted))
    # A wrapper's flags come before the wrapped argv, mirroring execution.
    if _is_wrapper(argv0, base):
        parts += flag_parts + arg_parts
    else:
        parts += arg_parts + flag_parts
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

    def __init__(
        self,
        name: str,
        *base: str,
        in_process: bool = False,
        path: str = "",
        entry: str = "",
        single_dash: bool = False,
    ) -> None:
        self._argv0 = name  # the name shown, and the console script looked up
        self._base = list(base)
        self._prefer_in_process = in_process
        # The executable actually run, when it isn't the name: `tools.python`
        # runs `sys.executable`, not whatever `python` is on PATH.
        self._path = path or name
        # An in-process callable to prefer over the console script, spelled
        # `module:attr`. pytest's console entry is a zero-arg `_console_main`
        # (serialised), but `pytest.main` takes the argument list and stays
        # parallel — so it is recorded here.
        self._entry = entry
        # A Go-style tool whose `flag` package wants one dash on long flags
        # (`eclint -fix`, not `--fix`). Tool-wide: Go's flag package is uniform,
        # so this rides every flag the tool emits, chained subcommands included.
        self._single_dash = single_dash

    def _sub(self, *tail: str) -> Tool:
        """A chained tool sharing this one's executable, entry, and mode."""
        return Tool(
            self._argv0,
            *self._base,
            *tail,
            in_process=self._prefer_in_process,
            path=self._path,
            entry=self._entry,
            single_dash=self._single_dash,
        )

    def __getattr__(self, verb: str) -> Tool:
        if verb.startswith("_"):
            raise AttributeError(verb)
        return self._sub(verb.replace("_", "-"))

    def opts(self, **kwargs: Any) -> Tool:
        """Bind options *before* the next subcommand — a tool's globals.

        Some options belong to the tool, not the verb, and must precede it:
        `docker --host tcp://x ps` works, `docker ps --host tcp://x` does
        not. `opts` places them at the current point in the chain, so they
        land ahead of whatever verb follows and ahead of its arguments:

            tools.docker.opts(host="tcp://x").ps(all=True)
            #  -> docker --host=tcp://x ps --all

        The flags are translated by the same rules as any call, and the
        returned tool keeps chaining, so `.opts(...)` composes mid-stream.
        """
        return self._sub(*_flags(kwargs, self._argv0, single_dash=self._single_dash))

    def __call__(
        self,
        *args: Any,
        nofail: bool = False,
        in_process: bool | None = None,
        **kwargs: Any,
    ) -> int:
        flags = _flags(kwargs, self._argv0, single_dash=self._single_dash)
        positionals = list(map(str, args))
        # A wrapper verb (`uv run`, `docker exec`) forwards everything after
        # its own arguments to a child, so this call's flags must precede the
        # positionals — otherwise `--frozen` lands on `pytest`, not `uv`.
        if _is_wrapper(self._argv0, self._base):
            tail = [*self._base, *flags, *positionals]
        else:
            tail = [*self._base, *positionals, *flags]
        # Execution runs the real executable (`python` → sys.executable); the
        # shown line keeps the name, so the painted command reads `python …`.
        argv = [self._path, *tail]
        show = _Invocation(
            _show_parts(
                self._argv0, self._base, args, kwargs, single_dash=self._single_dash
            ),
            tuple(argv),
        )
        wanted = self._prefer_in_process if in_process is None else in_process
        if wanted:
            loader = self._inprocess_loader()  # metadata only — no import
            if loader is None:
                if in_process is True:  # a demand can't be met — fail fast
                    raise ValueError(
                        f"{self._argv0}: in_process=True, but no importable "
                        f"in-process entry ({self._entry or self._argv0!r})"
                    )
                return _run(argv, nofail=nofail, _show=show)  # prefer → subproc

            def _invoke() -> Any:
                entry = loader()  # the tool's import — deferred to execution,
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

    def _inprocess_loader(self) -> Any | None:
        """A callable that imports and returns the in-process target — or None
        when there is nothing to run in-process (so the call spawns instead).

        A recorded `entry` override wins over the console script: pytest's
        console entry is the zero-arg `_console_main`, but `pytest.main` takes
        the argument list, so it is recorded as `pytest:main` and stays
        parallel. Availability is checked without importing (a dry-run of the
        call must import nothing); the import itself is deferred to the loader.
        """
        if self._entry:
            import importlib.util

            module = self._entry.partition(":")[0]
            try:
                if importlib.util.find_spec(module) is None:
                    return None
            except (ImportError, ValueError):
                return None

            def load() -> Any:
                import importlib

                mod, _, attr = self._entry.partition(":")
                return getattr(importlib.import_module(mod), attr)

            return load
        ep = _console_entrypoint(self._argv0)
        return ep.load if ep is not None else None

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
gh = Tool("gh")
eclint = Tool("eclint", single_dash=True)  # Go flag package: `-fix`, not `--fix`
mypy = Tool("mypy")
ty = Tool("ty")
twine = Tool("twine")
git_changelog = Tool("git-changelog")
git_cliff = Tool("git-cliff")
build = Tool("pyproject-build")  # the `build` package's console script
cmake = Tool("cmake")
ninja = Tool("ninja")


# pytest runs in-process through the arg-accepting `pytest.main` (parallel),
# not its zero-arg `_console_main` console script. python always targets the
# running interpreter, whatever `python`/`python3` is (or isn't) on PATH; its
# stub is read from provisioned interpreters. There is no `sh`: a command as a
# single string is `run("…")` — footman splits and runs it (no shell). For a
# real shell, invoke one: `tools.bash("-c", "…")`.
pytest = Tool("pytest", in_process=True, entry="pytest:main")
python = Tool("python", path=_sys.executable)

# The shells footman autocompletes for, invoked to run a command *string*:
# `tools.bash("echo $X | grep y")` runs `bash -c "…"`, so pipes, redirects,
# globbing and `$VAR` all work — the deliberate "I want a shell" escape hatch,
# where `run(...)` stays shell-free. `-c` is the run-a-string flag for every
# one of them (pwsh takes it as an alias for -Command).
bash = Tool("bash", "-c")
zsh = Tool("zsh", "-c")
fish = Tool("fish", "-c")
pwsh = Tool("pwsh", "-c")
nu = Tool("nu", "-c")


def __getattr__(name: str) -> Tool:
    # Any executable is a tool: `tools.terraform("plan")` needs no declaration.
    if name.startswith("_"):
        raise AttributeError(name)
    return Tool(name.replace("_", "-"))
