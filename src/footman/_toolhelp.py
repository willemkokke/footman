"""Read a tool's own `--help` and turn it into a `ToolSpec`.

The structural extractors in `_toolspec` are better when they apply — click
and argparse hand over their parameters as data. But most of the tools a
task actually calls are Rust, Go or Node binaries with no Python parser to
introspect: ruff and uv (clap), docker (cobra), cspell and markdownlint
(commander), git (its own). For those, the tool's `--help` is the only
description it offers, and it is far more regular than it looks.

Every one of those help formats prints an option as a line that starts with
a dash, followed by help text that is either on the same line past a run of
spaces, or on the lines below indented deeper:

    clap        --fix
                    Apply fixes to resolve lint violations. Use `--no-fix`
                    to disable

    optparse    -d DIR, --directory=DIR
                        Write the output files to DIR.

    cobra       -f, --file stringArray   Compose configuration files

So one parser reads them all: find the lines that start an option, split the
flag spellings from the prose, and glue on the continuation lines. What
differs between the families is only how they spell a *default* and a
*negation*, and those are small dialects on top (`[default: 3]`,
`(default true)`, "Use `--no-fix` to disable").

Nothing here runs at task time. The extractor runs when a maintainer
regenerates the stubs, and `fm footman tools audit` compares what it finds
against what is checked in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace

from footman._toolspec import Option, ToolSpec, Verb

# An option block opens with a dash at the start of the line's content. The
# indent is captured because it decides what counts as a continuation line.
_OPTION = re.compile(r"^(?P<indent> *)(?P<body>-{1,2}[A-Za-z0-9?].*)$")

# `Options:`, `OPTIONS`, `Flags:`, `Rule selection:` — every family prints
# some variant. The colon (or the shouting) is what makes it a heading: a
# tool's one-line description is also short, unindented and capitalised.
_SECTION = re.compile(r"^(?P<title>[A-Za-z][A-Za-z /-]*):$|^(?P<caps>[A-Z][A-Z /-]+)$")
# Sections that hold something other than options.
_NOT_OPTIONS = re.compile(r"command|example|usage|argument|see also|environment")

# One spelling inside a flag block: `--select <RULE>`, `--directory=DIR`,
# `-j N`, `--fix`. Stops before the two-space gap that starts the prose.
# cobra names the value's *type* where the others print a placeholder:
# `--file string`, `--tail string`, `--label stringArray`. Spelled out
# rather than matched as "any lowercase word", so a help line that puts its
# prose one space after the flag can't be mistaken for a value.
# Compound names first: alternation is ordered, so a leading `string`
# would match `stringArray` and stop, losing the fact that it repeats.
_GO_TYPES = (
    r"(?:stringToString|stringArray|stringSlice|intSlice|uintSlice|boolSlice"
    r"|ipSlice|bytesBase64|bytesHex|duration|float32|float64"
    r"|int8|int16|int32|int64|uint8|uint16|uint32|uint64"
    r"|string|int|uint|bool|ip)"
)
_SPELLING = re.compile(
    r"(?P<flag>--?(?:\[no-\])?[A-Za-z0-9][A-Za-z0-9._-]*)"
    # git glues an optional-value placeholder to the flag with no space:
    # `--gpg-sign[=<key-id>]`, `--untracked-files[=<mode>]`. Read as one
    # attached token so the option isn't mistaken for a bare switch.
    r"(?P<attached>\[=[^\]]*\])?"
    r"(?:[= ](?P<meta>\[?<[^>]+>(?:\.\.\.)?\]?|\[[^\]]+\]|[A-Z][A-Z0-9_.,|]*(?:\.\.\.)?"
    rf"|{_GO_TYPES}))?"
)

# The dialects of "this is the default".
_DEFAULT = re.compile(
    r"\[default: (?P<clap>[^\]]*)\]|\(default:? (?P<other>[^)]*)\)", re.IGNORECASE
)
# clap and cobra both print the closed set of values they accept — inline
# when they are short, and as a bulleted list when each one has its own
# gloss. Both forms mean the same thing to a stub.
_CHOICES = re.compile(r"\[possible values: (?P<values>[^\]]+)\]")
_POSSIBLE = re.compile(r"Possible values:\s*(?P<body>.*)$", re.IGNORECASE)
_BULLET = re.compile(r"(?:^|\s)- (?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*):")
# The negation stated in prose, which is the only place some tools say it.
_PROSE_NEGATION = re.compile(
    r"(?:use|pass) [`'\"]?(?P<flag>--no-[A-Za-z0-9-]+|--[A-Za-z0-9-]+)[`'\"]?"
    r"[^.]{0,40}?(?:to disable|to turn (?:it|this) off)",
    re.IGNORECASE,
)
# git's own dialect: `--[no-]quiet` is both spellings on one line.
_INLINE_NEGATION = re.compile(r"^--\[no-\](?P<name>.+)$")
_REPEATABLE = re.compile(
    r"(?:may|can) be (?:used|repeated|specified|passed|given)"
    r"(?: multiple times| more than once| repeatedly)?",
    re.IGNORECASE,
)


def _sections(text: str) -> dict[str, list[str]]:
    """Split help output into `{section title: lines}`.

    Sections matter for two reasons: subcommands live in one of them, and
    an option's *section* is how a tool marks its global flags.
    """
    out: dict[str, list[str]] = {"": []}
    title = ""
    for line in text.splitlines():
        if not line[:1].isspace() and line.strip():
            match = _SECTION.match(line.strip())
            if match and not line.strip().startswith("-"):
                title = (match["title"] or match["caps"]).strip().lower()
                out.setdefault(title, [])
                continue
        out[title].append(line)
    return out


def _blocks(lines: Sequence[str]) -> list[tuple[str, str]]:
    """Yield `(spellings, help)` for each option in *lines*.

    The boundary between two options is the *help column*, not the flag
    column. Flags themselves sit at more than one indent — clap prints
    `  -w, --watch` but `      --fix-only`, aligning long flags past the
    short-flag column — so "indented deeper than the last flag" would read
    the second one as prose belonging to the first. Help text is always
    indented deeper still, so a dash at less than the help column opens a
    new option and anything at or past it is that option's prose.
    """
    blocks: list[tuple[str, str]] = []
    pending: tuple[str, list[str]] | None = None
    flag_indent = 0
    help_indent = 0  # 0 until the block's prose reveals the column
    for line in lines:
        match = _OPTION.match(line.rstrip())
        indent = len(match["indent"]) if match else len(line) - len(line.lstrip())
        opens = indent < help_indent if help_indent else indent <= flag_indent
        if match and (pending is None or opens):
            if pending is not None:
                blocks.append((pending[0], " ".join(pending[1]).strip()))
            flag_indent = indent
            head, _, tail = match["body"].partition("  ")
            # Learn the help column from same-line help too, not only from a
            # continuation: cobra prints `-d, --detach` at one indent and
            # `      --tail string` at a deeper one, and without the column
            # the deeper flag reads as prose belonging to the shallower one.
            help_indent = (
                indent + len(head) + 2 + len(tail) - len(tail.lstrip())
                if tail.strip()
                else 0
            )
            pending = (head.strip(), [tail.strip()] if tail.strip() else [])
        elif pending is not None:
            stripped = line.strip()
            if not stripped:
                continue  # a blank line inside a block is just formatting
            if indent <= flag_indent:
                blocks.append((pending[0], " ".join(pending[1]).strip()))
                pending = None
                continue
            help_indent = help_indent or indent
            pending[1].append(stripped)
    if pending is not None:
        blocks.append((pending[0], " ".join(pending[1]).strip()))
    return blocks


def _spellings(head: str) -> tuple[list[str], str, bool]:
    """The flags in an option's left column, its placeholder, and whether
    the value is optional (a `[=…]` glued to the flag)."""
    flags: list[str] = []
    meta = ""
    optional = False
    for match in _SPELLING.finditer(head):
        flags.append(match["flag"])
        meta = meta or (match["meta"] or "")
        optional = optional or bool(match["attached"])
    return flags, meta, optional


def _clean(text: str) -> str:
    """The tool's prose, minus the machine-readable tails it appends."""
    text = _DEFAULT.sub("", text)
    text = _CHOICES.sub("", text)
    text = _POSSIBLE.sub("", text)
    text = re.sub(r"\[env: [^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def _parse_default(text: str) -> str:
    match = _DEFAULT.search(text)
    if not match:
        return ""
    return (match["clap"] or match["other"] or "").strip().strip("\"'")


def _option(head: str, help_text: str) -> Option | None:
    """One `Option` from one parsed block, or None if it isn't one."""
    flags, meta, optional = _spellings(head)
    longs = [f for f in flags if f.startswith("--")]
    if not longs:
        return None  # a short-only option has no keyword spelling
    inline = _INLINE_NEGATION.match(longs[0])
    stem = inline["name"] if inline else longs[0].removeprefix("--")
    name = stem.replace("-", "_").replace(".", "_")
    default = _parse_default(help_text)
    choices = _choices(help_text)
    # An optional-value option (`--gpg-sign[=<key-id>]`) is neither a plain
    # switch nor a required-value option: it works bare *and* with a value.
    is_flag = not meta and not optional
    repeatable = bool(
        meta.endswith(("...", "...]", "Array", "Slice", "ToString"))
        or _REPEATABLE.search(help_text)
    )
    negation = f"--no-{stem}" if inline else ""
    prose = _PROSE_NEGATION.search(help_text)
    if not negation and is_flag and prose and prose["flag"] != longs[0]:
        negation = prose["flag"]
    return Option(
        name=name,
        flags=tuple(sorted((_spell(f, stem) for f in flags), key=len, reverse=True)),
        negation=negation,
        help=_clean(help_text),
        type_name=_kind(is_flag, repeatable, choices, optional),
        default=_coerce_default(default, is_flag),
        choices=choices,
    )


# `--help` and `--version` are on every tool and belong to no task: the
# bridge would happily emit them, but a stub that suggests them is noise.
_NOISE = frozenset({"help", "version"})


def _choices(text: str) -> tuple[str, ...]:
    """The values a tool says it accepts, from whichever form it printed."""
    inline = _CHOICES.search(text)
    if inline:
        return _values(inline["values"])
    listed = _POSSIBLE.search(text)
    if listed:
        return tuple(m["name"] for m in _BULLET.finditer(listed["body"]))
    return ()


def _spell(flag: str, stem: str) -> str:
    """`--[no-]quiet` is how git *prints* it; `--quiet` is what it takes."""
    return f"--{stem}" if _INLINE_NEGATION.match(flag) else flag


def _values(text: str) -> tuple[str, ...]:
    """The closed set a tool prints, as the stub's `Literal` members."""
    return tuple(v.strip() for v in text.split(",") if v.strip())


def _kind(
    is_flag: bool, repeatable: bool, choices: tuple[str, ...], optional: bool = False
) -> str:
    if is_flag:
        return "bool"
    if optional:
        return "optvalue"  # a switch that also accepts a value
    if choices:
        return "choice[]" if repeatable else "choice"
    return "list[str]" if repeatable else "str"


def _coerce_default(text: str, is_flag: bool) -> object:
    if not text:
        return None
    if is_flag or text in {"true", "false"}:
        return text == "true"
    return text


def _pair_negations(options: list[Option]) -> list[Option]:
    """Fold `--no-x` entries into `x`, and drop them as options of their own.

    Every family that supports negation prints both spellings, so the pair
    is right there in the help — `--fix` and `--no-fix`, `--clean` and
    `--dirty` (that one only says so in prose). Folding them means `off`
    knows the tool's real spelling and the stub stays one keyword per
    concept, the way the tool's own docs read.
    """
    by_name = {o.name: o for o in options}
    folded: list[Option] = []
    negated: set[str] = set()
    for option in options:
        if not option.name.startswith("no_"):
            continue
        positive = by_name.get(option.name.removeprefix("no_"))
        if positive is not None and positive.type_name == "bool":
            negated.add(option.name)
            by_name[positive.name] = _with_negation(positive, option.flags[0])
    for option in options:
        if option.name in negated:
            continue
        folded.append(by_name[option.name])
    return folded


def _with_negation(option: Option, negation: str) -> Option:
    if option.negation:
        return option
    return Option(
        name=option.name,
        flags=option.flags,
        negation=negation,
        help=option.help,
        type_name=option.type_name,
        default=option.default,
        choices=option.choices,
    )


def parse_help(text: str, *, name: str = "") -> Verb:
    """One verb's options, from that verb's own `--help` output."""
    sections = _sections(text)
    options: list[Option] = []
    seen: set[str] = set()
    for title, lines in sections.items():
        if _NOT_OPTIONS.search(title):
            continue  # `Commands:`, `Examples:` — dashes there aren't flags
        for head, help_text in _blocks(lines):
            option = _option(head, help_text)
            if (
                option is not None
                and option.name not in _NOISE
                and option.name not in seen
            ):
                seen.add(option.name)
                options.append(option)
    positional, lead = _usage_shape(text)
    return Verb(
        name=name,
        help=_summary(text),
        options=tuple(sorted(_pair_negations(options), key=lambda o: o.name)),
        positional=positional,
        lead=lead,
    )


# The base of a positional metavar, before any `[:TAG]` / `<...>` suffix:
# `IMAGE`, `NAME` from `NAME[:TAG|@DIGEST]`, `repo` from `<repo>`.
_METAVAR = re.compile(r"^<?[A-Za-z][A-Za-z0-9_-]*>?$")


def _is_option_token(token: str) -> bool:
    """A usage token that is an option, a separator, or the `[OPTIONS]` slot
    — not a positional argument."""
    bare = token.strip("[]<>").lower()
    return not bare or bare in {"--", "|", "options", "flags"} or bare.startswith("-")


def _top_level_positionals(usage: str) -> list[str]:
    """The positional tokens at bracket depth 0.

    A usage grammar nests option groups in brackets — `[--reason <string>]`,
    `[--separate-git-dir <git-dir>]` — and whitespace-splitting scatters
    their *values* into loose tokens (`<string>]`) that look like bare
    positionals. Tracking depth keeps those out: only a token that starts
    while no bracket is open can be a real argument.
    """
    positional: list[str] = []
    depth = 0
    for token in usage.split():
        if depth == 0 and not _is_option_token(token):
            positional.append(token)
        depth = max(0, depth + token.count("[") - token.count("]"))
    return positional


def _usage_shape(text: str) -> tuple[str, str]:
    """`(positional, lead)` from a verb's `usage:` line.

    Two confident answers, everything else `"any"`:

    * `"none"` when the argument section is *only* options — mkdocs build's
      `[OPTIONS]`. A positional there is a type error.
    * `"required"` when a single clean metavar leads — `docker run IMAGE …`,
      `git clone <repo> …`. The stub makes it positional-only.

    Ambiguity stays `"any"`, because a wrong answer *forbids a valid call*.
    An option woven into an alternation (`<PACKAGES|--requirements …>`), a
    bracketed-optional or variadic first argument, an unfamiliar token — all
    fall through, so a real command is never rejected.
    """
    usage = _usage_line(text)
    if not usage:
        return "any", ""
    positional = _top_level_positionals(usage)
    if not positional:
        return "none", ""
    first = positional[0]
    if any("--" in token for token in positional):
        return "any", ""  # a `<X|--flag>` alternation — packages OR a flag
    if first.startswith("[") or "..." in first:
        return "any", ""  # optional or variadic leading argument
    base = re.split(r"[\[:]", first.strip("[]<>"))[0]
    if not base or base[-1:].isdigit() or not _METAVAR.match(base):
        return "any", ""  # numbered (`path1`) or unrecognised — don't constrain
    return "required", base.replace("-", "_").lower()


def _usage_line(text: str) -> str:
    """The `usage:` line, minus the program name, joined if it wraps.

    A wrapped usage (git's spans several indented lines) is stitched back
    together; the program name and any leading subcommands are dropped so
    only the argument grammar remains.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lower().lstrip().startswith("usage"):
            continue
        collected = [line]
        for cont in lines[i + 1 :]:
            if not cont.strip() or not cont[:1].isspace():
                break
            # git prints alternative forms as `   or: git branch …`. Only
            # the first form is parsed — stitching the alternatives together
            # would merge incompatible grammars into nonsense.
            if cont.lstrip().lower().startswith("or:"):
                break
            collected.append(cont)
        joined = " ".join(part.strip() for part in collected)
        after = re.sub(r"(?i)^usage:?\s*", "", joined)
        # Drop the program + verbs: everything up to the first bracket or
        # metavar-looking token is the command path, not an argument.
        tokens = after.split()
        rest = []
        seen_arg = False
        for token in tokens:
            if not seen_arg and (token.startswith(("[", "<")) or token.isupper()):
                seen_arg = True
            if seen_arg:
                rest.append(token)
        return " ".join(rest)
    return ""


def _summary(text: str) -> str:
    """A tool's one-line self-description: its help's first prose line."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("usage") or stripped.startswith("-"):
            continue
        if _SECTION.match(stripped):
            continue
        return stripped
    return ""


def subcommands(text: str) -> dict[str, str]:
    """`{name: summary}` from the `Commands:` section of a tool's help."""
    found: dict[str, str] = {}
    for title, lines in _sections(text).items():
        if not re.search(r"command|subcommand", title):
            continue
        for line in lines:
            match = re.match(
                r"^\s+(?P<name>[a-z][a-z0-9-]*)(?:,\s*[a-z0-9-]+)*"
                r"(?:\s{2,}(?P<help>.*))?$",
                line.rstrip(),
            )
            if match:
                found.setdefault(match["name"], (match["help"] or "").strip())
    return found


def run_help(argv: list[str], *, flag: str = "--help", timeout: float = 30.0) -> str:
    """`<tool> ... --help`, as text. Empty when the tool isn't installed.

    Help goes to stdout for every tool footman curates, but a few print
    usage to stderr on older versions, so both are read.
    """
    if shutil.which(argv[0]) is None:
        return ""
    try:
        done = subprocess.run(
            [*argv, flag],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_wide_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if len(done.stdout) > len(done.stderr) else done.stderr


def _wide_env() -> dict[str, str]:
    """A wide terminal, so help text wraps as little as possible.

    Every family honours one of these; a narrow wrap costs nothing but
    re-joined prose, and a wide one keeps `[default: …]` on the line it
    belongs to.
    """
    import os

    return {**os.environ, "COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}


def from_help(
    name: str,
    *,
    verbs: tuple[str, ...] = (),
    version: str = "",
    in_process: bool = False,
    flag: str = "--help",
) -> ToolSpec:
    """A `ToolSpec` for *name* by asking the installed binary.

    Each verb costs one `<tool> <verb> --help`; the root call supplies the
    tool's summary and its global options (verb `""`).
    """
    root = run_help([name], flag=flag)
    if not root:
        return ToolSpec(name=name, version=version)
    root_verb = parse_help(root, name="")
    if verbs:
        # A multi-command tool's bare usage line (`docker [OPTIONS] COMMAND`)
        # describes the subcommand slot, not arguments to `docker` itself —
        # so `tools.docker(...)` must not be constrained by it. Only a
        # single-command tool's root verb carries a real positional shape.
        root_verb = replace(root_verb, positional="any", lead="")
    parsed = [root_verb]
    for verb in verbs:
        text = run_help([name, *verb.split(".")], flag=flag)
        if text:
            # `git rev-parse` is spelled `tools.git.rev_parse(...)`: the
            # bridge turns the underscore back into a dash when it calls.
            parsed.append(parse_help(text, name=verb.replace("-", "_")))
    return ToolSpec(
        name=name,
        help=_summary(root),
        version=version,
        verbs=tuple(parsed),
        in_process=in_process,
    )
