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
regenerates the stubs, and `fm tools.audit` compares what it finds
against what is checked in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from footman._toolspec import Option, ToolSpec, Verb

# An option block opens with a dash at the start of the line's content. The
# indent is captured because it decides what counts as a continuation line;
# it also absorbs a leading `- ` bullet — markdownlint-cli2 (and other
# minimist/meow tools) print options as a bulleted list, `- --fix  updates …`,
# where the flag itself is what follows the bullet.
_OPTION = re.compile(r"^(?P<indent> *(?:- )?)(?P<body>-{1,2}[A-Za-z0-9?].*)$")

# `Options:`, `OPTIONS`, `Flags:`, `Rule selection:` — every family prints
# some variant. The colon (or the shouting) is what makes it a heading: a
# tool's one-line description is also short, unindented and capitalised.
_SECTION = re.compile(r"^(?P<title>[A-Za-z][A-Za-z /-]*):$|^(?P<caps>[A-Z][A-Z /-]+)$")
# Sections that hold something other than options.
_NOT_OPTIONS = re.compile(r"command|example|usage|argument|see also|environment")

# One spelling inside a flag block: `--select <RULE>`, `--directory=DIR`,
# `-j N`, `--fix`. It runs on the flag column only — `_blocks` has already
# split the prose off at the two-space gap — so the compound go-types are
# named explicitly (`stringArray` repeats where `string` does not), and a
# bare lowercase word left in the column is read as a value placeholder
# (gh's `--assignee login`), not as the first word of the description.
# Compound names first: alternation is ordered, so a leading `string`
# would match `stringArray` and stop, losing the fact that it repeats.
_GO_TYPES = (
    r"(?:stringToString|stringArray|stringSlice|intSlice|uintSlice|boolSlice"
    r"|ipSlice|bytesBase64|bytesHex|duration|float32|float64"
    r"|int8|int16|int32|int64|uint8|uint16|uint32|uint64"
    r"|string|int|uint|bool|ip)"
)
# The flag and any attached optional-value placeholder, shared by both forms.
_FLAG = (
    # A dot is allowed only *inside* the name (`--foo.bar`), never trailing:
    # clap prints a repeatable flag as `--verbose...`, and a greedy `.` would
    # swallow the ellipsis into the name (`verbose...` → keyword `verbose___`).
    r"(?P<flag>--?(?:\[no-\])?[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9]))*)"
    # git glues an optional-value placeholder to the flag with no space:
    # `--gpg-sign[=<key-id>]`, `--untracked-files[=<mode>]`. Read as one
    # attached token so the option isn't mistaken for a bare switch.
    r"(?P<attached>\[=[^\]]*\])?"
)
# The value placeholder every dialect agrees on: `<x>`, `[x]`, an UPPERCASE
# metavar, or a cobra go-type (`stringArray`).
_META = (
    r"\[?<[^>]+>(?:\.\.\.)?\]?|\[[^\]]+\]|[A-Z][A-Z0-9_.,|]*(?:\.\.\.)?"
    rf"|{_GO_TYPES}"
)
# cobra and gh also name a value with a bare lowercase word — `--assignee
# login`, `--base branch`, `--memory bytes`. Only trusted in `--help` text,
# where `_blocks` has split the prose off at the two-space gap: a man page's
# description is a paragraph, and "the `--patch` option." there would read
# "option" as `--patch`'s value.
_META_BARE = r"|[a-z][A-Za-z0-9._-]*"
_SPELLING = re.compile(_FLAG + rf"(?:[= ](?P<meta>{_META}{_META_BARE}))?")
_SPELLING_STRICT = re.compile(_FLAG + rf"(?:[= ](?P<meta>{_META}))?")

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
            body = match["body"]
            head, _, tail = body.partition("  ")
            # Python's `--help` separates the flag column from the description
            # with a ` : ` gutter (`-b     : issue warnings`, `-c cmd : program
            # passed in`), not the double-space gutter every other dialect
            # uses. Re-split on it so the colon doesn't leak into the help text,
            # and — when the columns touch and the double-space split found
            # nothing — so the metavar and description aren't lost outright.
            if not tail.strip() and " : " in head:
                head, _, tail = body.partition(" : ")
            elif tail.lstrip().startswith(":"):
                tail = tail.lstrip()[1:]
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


def _spellings(
    head: str, *, strict: bool = False, bare_meta: bool = True
) -> tuple[list[str], str, bool]:
    """The flags in an option's left column, its placeholder, and whether
    the value is optional (a `[=…]` glued to the flag).

    *strict* drops the bare-lowercase metavar, for a man page whose prose
    refers to flags mid-sentence: `--patch` there must not read the next
    word as its value.

    *bare_meta* is the same drop for one option in `--help` text, where the
    usage line has already named this flag's value. The bare-word rule reads
    the first word after the flag as a metavar, which is right for gh's
    `--assignee login` and wrong wherever a description begins there instead
    — and it cannot tell them apart, because both are one lowercase word.
    The grammar can. Nothing is lost by dropping it: what the placeholder is
    *called* is never recorded, only that there is one, and that is exactly
    what the usage line has just said.
    """
    flags: list[str] = []
    meta = ""
    optional = False
    pattern = _SPELLING_STRICT if strict or not bare_meta else _SPELLING
    for match in pattern.finditer(head):
        flags.append(match["flag"])
        meta = meta or (match["meta"] or "")
        optional = optional or bool(match["attached"])
    return flags, meta, optional


# A manual's prose is Unicode-typeset (curly quotes, dashes, ellipsis).
# The stub is source that must stay ASCII-clean (ruff RUF002), so fold them.
_TYPOGRAPHY = str.maketrans(
    {
        "\u2019": "'",  # right single quote
        "\u2018": "'",  # left single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2026": "...",  # ellipsis
        "\u00a0": " ",  # no-break space
    }
)  # fmt: skip
# Abbreviations whose period does not end a sentence.
_ABBREV = (" e.g", " i.e", " etc", " vs", " cf", " al", " no")


def _clean(text: str) -> str:
    """The tool's prose, as one clean first sentence.

    A `--help` line is already a sentence; a manual entry is paragraphs, so
    keep only the first — the summary a completion popup can show — folding
    the manual's typographic punctuation to ASCII on the way.
    """
    text = _DEFAULT.sub("", text)
    text = _CHOICES.sub("", text)
    text = _POSSIBLE.sub("", text)
    text = re.sub(r"\[env: [^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).translate(_TYPOGRAPHY).strip(" .")
    return _first_sentence(text)


def _first_sentence(text: str) -> str:
    """Up to the first sentence-ending period, skipping `e.g.`/`i.e.`."""
    for match in re.finditer(r"\. ", text):
        head = text[: match.start()]
        if not head.endswith(_ABBREV):
            return head
    return text


def _parse_default(text: str) -> str:
    match = _DEFAULT.search(text)
    if not match:
        return ""
    return (match["clap"] or match["other"] or "").strip().strip("\"'")


def _option(
    head: str,
    help_text: str,
    *,
    strict: bool = False,
    shorts: str = "only",
    bare_meta: bool = True,
) -> Option | None:
    """One `Option` from one parsed block, or None if it isn't one.

    *shorts* is the short-option policy: `"none"` never keys on a short,
    `"only"` (default) keys on a short *when it is the option's only spelling*
    (python's `-m`, git's `-C`), and `"all"` also keys on a short that has a
    long — `_short_alias` adds the extra keyword for that mode.
    """
    flags, meta, optional = _spellings(head, strict=strict, bare_meta=bare_meta)
    longs = [f for f in flags if f.startswith("--")]
    if not longs:
        # Go's stdlib `flag` spells even long options with one dash (`-color`,
        # `-no_gitignore`); read a multi-char single-dash flag as the keyword
        # when there's no `--` form.
        longs = [f for f in flags if len(f) > 2 and not f.startswith("--")]
    if not longs and shorts != "none" and not strict:
        # A short-only option (python's `-m`, `-c`, `-O`): the single char is
        # the keyword — the bridge turns `m="build"` into `-m build`. Help text
        # only (a man page's prose is too noisy to trust), and only a letter
        # that forms a valid keyword (`-0` can't).
        longs = [f for f in flags if len(f) == 2 and f[1:].isidentifier()][:1]
    if not longs:
        return None  # nothing spellable
    if not help_text and not bare_meta:
        # The block never split, because the description sat one space from
        # the flag rather than in a column — markdownlint-cli2 aligns to its
        # longest option, so `--configPointer` alone loses its gap. The whole
        # line arrived as the head, and what follows the flags is the prose.
        # Only where the grammar has already settled arity: elsewhere that
        # first word may genuinely be the value's name.
        help_text = _after_flags(head)
    inline = _INLINE_NEGATION.match(longs[0])
    stem = inline["name"] if inline else longs[0].lstrip("-")
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


def parse_help(
    text: str, *, name: str = "", man: bool = False, shorts: str = "only"
) -> Verb:
    """One verb's options, from its `--help` output or (with `man`) manual.

    The option grammar is the same either way — the man page states a flag
    and its help exactly as `--help` does. Only the positional shape reads
    from a different place: a `usage:` line normally, the `SYNOPSIS` forms
    for a manual.
    """
    sections = _sections(text)
    # The flags the usage line has already given a value to. Where it has
    # spoken, the block's bare-lowercase-word rule is redundant at best and
    # wrong at worst — see `_spellings`.
    stated = set() if man else _grammar_values(_usage_line(text))
    options: list[Option] = []
    seen: set[str] = set()
    for title, lines in sections.items():
        if _NOT_OPTIONS.search(title):
            continue  # `Commands:`, `Examples:` — dashes there aren't flags
        for head, help_text in _blocks(lines):
            first = _FIRST_LONG.search(head)
            option = _option(
                head,
                help_text,
                strict=man,
                shorts=shorts,
                bare_meta=not (first and first["flag"] in stated),
            )
            if (
                option is not None
                and option.name not in _NOISE
                and option.name not in seen
            ):
                seen.add(option.name)
                options.append(option)
    if not options:
        # Go's `flag` prints its options under `Usage of <prog>:` — a section
        # `_NOT_OPTIONS` skips. Nothing parsed anywhere else, so scan every
        # section, including that one. Guarded on emptiness, so a tool that
        # parses normally never reaches here and can't regress.
        for _title, lines in sections.items():
            for head, help_text in _blocks(lines):
                option = _option(head, help_text, strict=man, shorts=shorts)
                if option is not None and option.name not in _NOISE:
                    seen.add(option.name)
                    options.append(option)
        options = list({o.name: o for o in options}.values())
    if shorts == "all":
        options = _with_short_aliases(options)
    if not man:
        options = _arity_from_grammar(options, _usage_line(text))
    positional, lead = _synopsis_shape(text, name) if man else _usage_shape(text)
    return Verb(
        name=name,
        help=_summary(text),
        options=tuple(sorted(_pair_negations(options), key=lambda o: o.name)),
        positional=positional,
        lead=lead,
        wraps=_wraps(text),
    )


def _arity_from_grammar(options: list[Option], grammar: str) -> list[Option]:
    """Believe the usage line where the option list said nothing.

    Most dialects state arity twice — `--config FILE` in the option block
    and `[--config FILE]` in the usage line — and the block is the richer
    source, so it wins. But a block is free to list a flag with prose and
    no metavar at all, and then the usage line is the *only* statement of
    arity in the whole document:

        Syntax: markdownlint-cli2 glob0 [--config file] [--fix]

        Optional parameters:
        - --config        specifies the path to a configuration file
        - --fix           updates files to resolve fixable issues

    Read alone, that block makes `--config` a switch, and the stub then
    types a path as `bool`. Read together, the tool has said plainly that
    one takes a value and the other does not.

    Only ever *adds* a value to something the block left bare: an option the
    block described with a metavar, a choice list or a default keeps what it
    said, because the block knows things the grammar cannot express. So a
    dialect that omits its options from the usage line loses nothing, and
    one that abbreviates with `[OPTIONS]` says nothing about any of them.
    """
    takes_value = _grammar_values(grammar)
    if not takes_value:
        return options
    out = []
    for option in options:
        bare = option.type_name == "bool" and not option.choices and not option.negation
        if bare and any(flag in takes_value for flag in option.flags):
            option = replace(option, type_name="str")
        out.append(option)
    return out


# `[--config file]`, `[--outdir OUTDIR]`, `[--installer {pip,uv}]` — a flag and
# its value inside the brackets that mark them optional together.
#
# Bracketed only, and that is the whole safety of it. `_usage_line` stitches
# indented continuation lines onto the usage, and a tool whose options block is
# indented under `Usage:` hands back the entire block as one line — so
# basedpyright's `--dependencies    Emit import dependency information` would
# read "Emit" as a metavar and turn nine real switches into strings. Inside
# brackets nothing but the grammar survives.
#
# The value is one token, and never another flag or an alternation bar: build
# groups its switches as `[--quiet | --verbose]`, where reading "| --verbose"
# as --quiet's value makes a string of a switch.
_GRAMMAR_PAIR = re.compile(
    r"\[(?P<flag>--[A-Za-z0-9][A-Za-z0-9_-]*)[ \t]+(?![-|])(?P<value>[^\s\[\]|]+)"
)

# The first long flag opening an option block, for asking the grammar about it.
_FIRST_LONG = re.compile(r"(?P<flag>--[A-Za-z0-9][A-Za-z0-9_-]*)")


def _grammar_values(grammar: str) -> set[str]:
    """The flags the usage line states a value for."""
    return {m["flag"] for m in _GRAMMAR_PAIR.finditer(grammar)} if grammar else set()


def _after_flags(head: str) -> str:
    """The prose left in a flag column after the flags that open it.

    Only the flags that *lead* — a description is free to name a flag mid
    sentence ("a JSON Pointer within the --config file"), and reading to the
    last one anywhere in the line would return "file" as the description.
    Once prose intervenes, the column is over.
    """
    end = 0
    for match in _SPELLING_STRICT.finditer(head):
        if match.start() > end and head[end : match.start()].strip(" ,|"):
            break
        end = match.end()
    return head[end:].strip(" ,")


def _with_short_aliases(options: list[Option]) -> list[Option]:
    """For `shorts="all"`: add a keyword for a short that *also* has a long,
    so `-m, --message` answers to both `message` and `m`. The long-keyed
    option stays; the alias is an extra entry keyed on the single char."""
    out = list(options)
    seen = {o.name for o in options}
    for option in options:
        for flag in option.flags:
            char = flag[1:]
            if len(flag) == 2 and char.isidentifier() and char not in seen:
                seen.add(char)
                out.append(replace(option, name=char))
    return out


# A metavar that stands for a *wrapped* command's argv: `uv run [COMMAND]`,
# `docker exec … COMMAND [ARG...]`.
_WRAP_METAVAR = frozenset({"command", "cmd", "args", "arg", "argv"})


def _wraps(text: str) -> bool:
    """Whether the verb forwards everything after its own args to a child.

    Signalled by a trailing command/argv metavar or coverage's literal
    "program options" — the mark of `uv run`, `docker exec`, `coverage run`.
    """
    usage = _usage_line(text)
    if "program option" in usage.lower():
        return True
    for token in _top_level_positionals(usage):
        base = re.split(r"[\[:]", token.strip("[]<>"))[0].lower()
        if base in _WRAP_METAVAR:
            return True
    return False


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
    return _grammar_shape(_usage_line(text))


def _grammar_shape(grammar: str) -> tuple[str, str]:
    """`(positional, lead)` from one argument grammar (no program name)."""
    if not grammar:
        return "any", ""
    positional = _top_level_positionals(grammar)
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


def _synopsis_shape(text: str, verb: str) -> tuple[str, str]:
    """`(positional, lead)` from a man page's `SYNOPSIS`.

    git's manual states each verb as one or more complete forms. A verb
    with a *single* form has one grammar to read (`git clone … <repository>
    [<directory>]` → required); a verb with several — `git checkout` lists,
    detaches, creates, restores — has no single shape, so it stays `"any"`.
    Counting the forms is just counting the lines that restate `git <verb>`;
    the wrapped continuations don't.
    """
    match = re.search(
        r"(?ms)^SYNOPSIS[ \t]*\n(?P<body>.*?)\n(?:[A-Z][A-Z ]+\n|\Z)", text
    )
    if not match:
        return "any", ""
    body = match["body"]
    prog = f"git {verb}"
    forms = re.findall(rf"(?m)^[ \t]*{re.escape(prog)}\b", body)
    if len(forms) != 1:
        return "any", ""  # multi-form (or unrecognised) — don't constrain
    grammar = " ".join(body.split()).split(prog, 1)[1]
    return _grammar_shape(grammar)


def _usage_line(text: str) -> str:
    """The `usage:` line, minus the program name, joined if it wraps.

    A wrapped usage (git's spans several indented lines) is stitched back
    together; the program name and any leading subcommands are dropped so
    only the argument grammar remains.

    `Syntax:` is the same line under a different name — markdownlint-cli2
    spells it that way, and skipping it cost the whole grammar: no usage
    line meant no arity for `--config`, which the option list states
    without a metavar.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        head = line.lower().lstrip()
        if not head.startswith(("usage", "syntax")):
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
    """A tool's one-line self-description: its help's first prose line.

    A manual says it outright, under NAME — `git - the stupid content
    tracker` — where the first prose line of the page is the running
    header. Read from the machine's `git -h` this landed on a fragment of
    the usage line instead, which described nothing at all.
    """
    named = _MAN_NAME.search(text)
    if named:
        return named.group(1).strip()
    if re.match(r"^Usage of \S", text):
        return ""  # Go's `flag` opens with `Usage of <prog>:` and has no summary
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("usage"):
            continue
        if (
            stripped.startswith("-")
            or _SECTION.match(stripped)
            or stripped.endswith(":")
        ):
            # Reached the options/sections with no summary in between — a tool
            # like python opens straight into `Options …:`, so it has none.
            return ""
        return stripped
    return ""


_MAN_NAME = re.compile(r"^NAME\n\s+\S+ - (.+)$", re.M)


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


def run_help(
    argv: list[str], *, flag: str = "--help", man: bool = False, timeout: float = 30.0
) -> str:
    """`<tool> ... --help`, as text. Empty when the tool isn't installed.

    `argv[0]` is the executable to run — a bare name resolved on `PATH`, or the
    absolute path a caller already resolved (`from_help(..., binary=…)`).

    Help goes to stdout for every tool footman curates, but a few print
    usage to stderr on older versions, so both are read.

    `man` reads the manual instead — `git help <verb>` — for a tool whose
    terse `-h` omits most of its flags (git's `-h` shows about half). It
    runs only at stub-generation time, never at task time, so its heavier
    footprint (a rendered man page) costs a user nothing.
    """
    if man:
        # A manual can be read without the tool: `man` renders the pages a
        # release shipped, and for a fetched tree there is no binary at all.
        return _run_man(argv, timeout)
    if shutil.which(argv[0]) is None:
        return ""
    try:
        # Tool help is UTF-8, never the locale codec: under Windows cp1252 a
        # multi-byte help string kills the reader thread mid-decode and the
        # stream comes back None — an observation lost as a hole.
        done = subprocess.run(
            [*argv, flag],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_wide_env(),
            creationflags=DETACHED,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out, err = done.stdout or "", done.stderr or ""
    return out if len(out) > len(err) else err


# A captured read must not expose the *caller's* console: a tool that
# interrogates the terminal at start-up (tea 0.13.0-0.14.2 sent an OSC
# theme query) blocks forever waiting for a reply no pipe will ever carry —
# so whether a read hung followed whatever window the walk was launched
# from. CREATE_NO_WINDOW gives the read a fresh hidden console instead.
# That does NOT cure a determined interrogator — measured, tea's band
# queries any attached console, VT or not, and hangs against the hidden
# one too (those releases sit below a provision floor for exactly that
# reason). What the flag buys is *determinism*: the same result from every
# terminal, while console-hosted runtimes still get the console they
# need — fully detached, pwsh dies at start-up and git-bash goes mute.
# The same choice footman's own detached children make (`_complete`,
# `_app`), for the sibling reason: console-less, Windows Terminal hands
# each spawn a visible window.
DETACHED = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Man renders bold/underline as `c\x08c` / `_\x08c` overstrike; dropping the
# char-then-backspace pair leaves clean text, no `col` binary needed.
_OVERSTRIKE = re.compile(r".\x08")


def man_version(tree: Path) -> str:
    """The version a fetched manual belongs to, from its own header.

    `.TH "GIT" "1" "2025-06-15" "Git 2\\&.50\\&.1" "Git Manual"` — the tree
    says which release it documents, so a reading names its own version
    the way a binary does, and the guard against describing the wrong
    release works unchanged.
    """
    page = tree / "man1" / "git.1"
    try:
        head = page.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    found = re.search(r'^\.TH\s+"[^"]*"\s+"[^"]*"\s+"[^"]*"\s+"([^"]*)"', head, re.M)
    if not found:  # pragma: no cover - every page carries a .TH line
        return ""
    title = found.group(1).replace("\\&", "")
    number = _VERSION_IN_TITLE.search(title)
    return number.group(0) if number else ""


_VERSION_IN_TITLE = re.compile(r"\d+(?:\.\d+)+")


def _fetched_manpath() -> str:
    """The manual tree an observation pointed us at, if any.

    Set by the walk around one release's pages. Empty means read the
    machine's own manuals, which is what stub generation did before there
    was anything else to read.
    """
    return os.environ.get("FOOTMAN_MANPATH", "")


def _run_man(argv: list[str], timeout: float) -> str:
    """`<tool> help <verb>`, de-overstruck — the manual as plain text.

    Against a fetched tree it is `man -M <tree> <tool>-<verb>` instead:
    `git help` needs git, and the whole point of reading a manual is that
    the release it documents never has to be installed.
    """
    tree = _fetched_manpath()
    if tree:
        # The page is named for the *tool*, not for the binary a caller
        # resolved: `from_help` passes an absolute path as argv[0], and
        # `man` would go looking for a page called `/opt/homebrew/…/git-add`.
        # `git help git` asks for the tool's own page, which is `git`, not
        # `git-git`.
        tool = Path(argv[0]).name
        rest = [part for part in argv[1:] if part != tool]
        return _render_page(tree, "-".join([tool, *rest]), timeout)

    env = read_env(
        GIT_PAGER="cat",
        PAGER="cat",
        MANPAGER="cat",
        MAN_KEEP_FORMATTING="",
        COLUMNS="200",
        # `git help` honours `help.format`, and on Windows it defaults to
        # html — which *opens a browser tab per verb* instead of printing
        # anything. Pin the format to man: a POSIX box reads the same text
        # it always did, and a box with no man viewer fails quietly into
        # the empty-text fallback rather than launching twenty tabs.
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="help.format",
        GIT_CONFIG_VALUE_0="man",
    )
    try:
        done = subprocess.run(
            [argv[0], "help", *argv[1:]],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=DETACHED,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _OVERSTRIKE.sub("", done.stdout or "")


def _render_page(tree: str, page: str, timeout: float) -> str:
    """One page of a fetched manual tree, as plain text."""
    if shutil.which("man") is None:
        return ""
    try:
        done = subprocess.run(
            # Absolute: `man -M` given a relative manpath finds nothing and
            # says so by rendering an empty page rather than failing, which
            # reads downstream as a release that documented nothing.
            ["man", "-M", str(Path(tree).resolve()), page],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={
                **os.environ,
                "MANPAGER": "cat",
                "PAGER": "cat",
                "MAN_KEEP_FORMATTING": "",
                "COLUMNS": "200",
            },
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _OVERSTRIKE.sub("", done.stdout or "")


# The caller's interpreter is never the read tool's interpreter. `uv run`
# exports PYTHONHOME pointing at the environment it launched — footman's own
# — and a console script from any *other* Python then loads that stdlib
# instead of its own and dies before it can describe itself ("Could not
# import runpy._run_module_as_main"). A walk reads period venvs by design, so
# every release on a different minor than the runner read as a hole: 107 of
# them on Windows, every tool whose launcher is a console script rather than
# a native binary. PYTHONPATH rides along for the same reason.
_HOST_INTERPRETER = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE")


def read_env(**extra: str) -> dict[str, str]:
    """The environment to read another tool in: ours, minus our interpreter."""
    env = {k: v for k, v in os.environ.items() if k not in _HOST_INTERPRETER}
    env.update(extra)
    return env


def _wide_env() -> dict[str, str]:
    """A wide terminal, so help text wraps as little as possible.

    Every family honours one of these; a narrow wrap costs nothing but
    re-joined prose, and a wide one keeps `[default: …]` on the line it
    belongs to.
    """

    return read_env(COLUMNS="200", TERM="dumb", NO_COLOR="1")


def _is_the_root_again(text: str, root: str) -> bool:
    """Whether a verb answered with the tool's own help instead of its own.

    A tool asked for a subcommand it does not have does not always fail:
    docker prints its root help and exits, so the reading looked like a
    successful one and `compose up` was recorded with docker's global
    options and docker's own summary. Nothing downstream could tell —
    it is a real help text, just not this verb's — and a walk that lost
    its compose plugin for one release would write that release's compose
    surface as ten global flags.

    Compared on the whole text, which is exact: two verbs of the same tool
    share phrasing but never the entire page.
    """
    return text.strip() == root.strip()


def from_help(
    name: str,
    *,
    binary: str | None = None,
    verbs: tuple[str, ...] = (),
    version: str = "",
    in_process: bool = False,
    flag: str = "--help",
    man: bool = False,
    shorts: str = "only",
) -> ToolSpec:
    """A `ToolSpec` for *name* by asking the installed binary.

    *binary* is the executable to run (the caller may have resolved it, e.g. to
    a Homebrew keg); it defaults to *name*, resolved on `PATH`. The tool's own
    verb names still ride as `name`/verbs in each argv, only the executable
    differs.

    Each verb costs one `<tool> <verb> --help` (or `<tool> help <verb>`
    with `man`); the root call supplies the tool's summary and its global
    options (verb `""`). With `man`, per-verb manuals are read but the root
    stays on `--help`, which is where a tool prints its verb list.
    """
    cmd = binary or name
    # Against a fetched manual there is no binary to ask for a usage line,
    # and asking the machine's own would describe a different release.
    root = run_help([cmd], flag=flag, man=man and bool(_fetched_manpath()))
    if not root:
        return ToolSpec(name=name, version=version)
    root_verb = parse_help(root, name="", shorts=shorts)
    if verbs:
        # A multi-command tool's bare usage line (`docker [OPTIONS] COMMAND`)
        # describes the subcommand slot, not arguments to `docker` itself —
        # so `tools.docker(...)` must not be constrained by it. Only a
        # single-command tool's root verb carries a real positional shape.
        root_verb = replace(root_verb, positional="any", lead="", wraps=False)
    if man:
        # The terse root help (`git -h`) lists subcommands, not globals; the
        # tool's own manual (`git help git`) lists the options that must
        # precede the verb — what `.opts()` binds. Read them from there.
        manual = run_help([cmd, name], man=True)
        if manual:
            root_verb = replace(
                root_verb, options=parse_help(manual, man=True, shorts=shorts).options
            )
    parsed = [root_verb]
    for verb in verbs:
        text = run_help([cmd, *verb.split(".")], flag=flag, man=man)
        if text and not _is_the_root_again(text, root):
            # `git rev-parse` is spelled `tools.git.rev_parse(...)`: the
            # bridge turns the underscore back into a dash when it calls.
            parsed.append(
                parse_help(text, name=verb.replace("-", "_"), man=man, shorts=shorts)
            )
    return ToolSpec(
        name=name,
        help=_summary(root),
        version=version,
        verbs=tuple(parsed),
        in_process=in_process,
    )
