"""The execution path: load tasks, refresh the manifest, run the chain.

This is everything that happens for a real `fm ...` invocation (as opposed to
the completion hot path). It imports the user's tasks file — paying that cost is
fine here — resolves the command line against the freshly-built manifest, and
runs the resulting segments, honouring the global options.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from footman import (
    _describe,
    _paths,
    _progress,
    config,
    context,
    discover,
    executor,
    manifest,
    registry,
    schedule,
    split,
)
from footman.app import DEFAULT_BRAND, Brand
from footman.split import Segment

# The brand (names + version) in effect for the current invocation. Set at the
# top of `run()`; a CLI is one invocation per process, so a module global is
# the simplest way to reach it from the error/version helpers. The colour
# flags follow the same pattern: one per stream, resolved once from the
# stream's tty-ness, --no-color, NO_COLOR, and TERM.
_brand: Brand = DEFAULT_BRAND
_color_out: bool = False
_color_err: bool = False


def _set_colors(no_color: bool) -> None:
    global _color_out, _color_err
    _color_out = _describe.wants_color(sys.stdout, no_color)
    _color_err = _describe.wants_color(sys.stderr, no_color)


def _error(message: str) -> None:
    prog = _describe.red(_brand.prog, _color_err)
    sys.stderr.write(f"{prog}: {message}\n")


def _refuse(json_mode: bool, message: str, code: int = 2) -> int:
    """Report a refusal on stderr — and when `--json` promised an envelope,
    keep stdout a single JSON document describing the same refusal, so a
    machine consumer never has to parse two formats."""
    _error(message)
    if json_mode:
        envelope = {"schema": 1, "error": {"code": code, "message": message}}
        print(json.dumps({**envelope, "results": []}, indent=2))
    return code


def _wants_json(argv: list[str]) -> bool:
    """Whether the leading globals include `--json`, tolerant of a malformed
    line — the refusal for `fm --json --nope` must still honour the envelope
    `--json` already promised. Mirrors `_parse_globals`' walk, minus raising.
    """
    i = 0
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        name = argv[i].split("=", 1)[0]
        if name == "--json":
            return True
        kind = split._GLOBAL_KIND.get(name)
        i += 1
        if kind == "option" and "=" not in argv[i - 1] and i < len(argv):
            i += 1  # skip the option's value
        elif (
            kind == "option?"
            and "=" not in argv[i - 1]
            and i < len(argv)
            and not argv[i].startswith("-")
        ):
            i += 1
    return False


def _print_version(json_mode: bool) -> int:
    if json_mode:
        payload = {"schema": 1, "name": _brand.name, "version": _brand.version}
        print(json.dumps(payload, indent=2))
    else:
        print(f"{_brand.name} {_brand.version}")
    return 0


def _globals_to_dict(tokens: list[str]) -> dict[str, object]:
    """Interpret the splitter's canonical global tokens into a flat mapping."""
    result: dict[str, object] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        name = tok.split("=", 1)[0]
        key = name.lstrip("-").replace("-", "_")
        if "=" in tok:  # a value attached by the splitter (--name=value)
            result[key] = tok.split("=", 1)[1]
            i += 1
        elif split._GLOBAL_KIND.get(name) == "option":
            result[key] = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
        else:  # a flag, or an option? given bare
            result[key] = True
            i += 1
    return result


def resolve_task_files(
    g: dict[str, object],
    *,
    on_warning: Callable[[str], None] | None = None,
    on_note: Callable[[str], None] | None = None,
) -> tuple[list[Path], dict[str, object]]:
    """The task files and merged config for the cwd + globals — the pure core of
    `_discover`, shared with the completion subprocess (`_suggest`) so both
    discover exactly the same tasks.

    `-f/--tasks-file` loads exactly one file, no cascade; otherwise every
    `tasks.py` from the repo root down to the cwd. Raises `config.ConfigError`
    on a bad `--config`; an empty file list means nothing matched. The caller
    owns how either outcome is surfaced.
    """
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = config.load_config(
        cwd,
        ceiling,
        g.get("config"),  # type: ignore[arg-type]
        on_warning=on_warning,
        on_note=on_note,
    )
    override = g.get("tasks_file")
    if override:
        one = Path(str(override)).expanduser()
        files = [one] if one.is_file() else []
    else:
        filename = cfg.get("tasks")
        name = filename if isinstance(filename, str) else _brand.tasks_file
        files = _paths.task_files(cwd, ceiling, name)
    return files, cfg


def _discover(
    g: dict[str, object], wants_help: bool, bare: bool
) -> tuple[list[Path], dict[str, object]] | int:
    """Resolve the task files to load and the merged config for this cwd.

    `-f/--tasks-file` is the escape hatch: it loads exactly one file, no
    cascade. Otherwise footman collects every `tasks.py` from the repo root
    (the `.git` ceiling) down to the cwd. Returns `(files, config)` or, when
    nothing was found, the exit code to return (0 for a listing, 2 otherwise).
    """
    try:
        files, cfg = resolve_task_files(
            g,
            on_warning=_error,
            on_note=_error if g.get("verbose") else None,
        )
    except config.ConfigError as exc:
        return _refuse(bool(g.get("json")), f"--config: {exc}")

    if files:
        return files, cfg

    looked = g.get("tasks_file") or cfg.get("tasks") or _brand.tasks_file
    if wants_help:
        # A stuck newcomer asking for help should see the globals (-f/-C are the
        # way out) — not a bare one-liner. Global help over an empty tree, then
        # the "where did I look" note.
        _print_global_help(manifest.build_manifest(registry.Group("root"))["tree"])
        print(f"\n(no tasks file found — looked for {looked})")
        return 0
    if bare or g.get("list") or g.get("tree"):
        # A bare `fm` (like `--list`) is a warm empty state, not a hard error.
        if g.get("json"):  # the catalog envelope, honestly empty
            tree = manifest.build_manifest(registry.Group("root"))["tree"]
            print(json.dumps({"schema": 1, "tree": tree}, indent=2))
        else:
            print(f"No tasks file found (looked for {looked}).")
        return 0
    return _refuse(
        bool(g.get("json")),
        f"no tasks file found (looked for {looked}); "
        f"create one or pass -f/--tasks-file.",
    )


# --- rendering ---------------------------------------------------------------


def _format_value(value: object) -> str:
    if value is True:
        return ""
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _plan_line(seg: Segment) -> str:
    parts = []
    for name, value in seg.values.items():
        if value is True:
            parts.append(f"--{name}")
        elif value is False:
            parts.append(f"--no-{name}")
        else:
            parts.append(f"{name}={_format_value(value)}")
    if seg.variadic:
        parts.append("*" + " ".join(seg.variadic))
    arrow = _describe.dim("->", _color_out)
    task = _describe.bold(seg.task, _color_out)
    line = f"  {arrow} {task}  " + " ".join(parts)
    if seg.passthrough is not None:
        line += _describe.dim(f"  [-- {' '.join(seg.passthrough)}]", _color_out)
    return line.rstrip()


def _print_plan(globals_: list[str], segments: list[Segment]) -> None:
    if globals_:
        label = _describe.dim("globals:", _color_out)
        print(f"  {label} {' '.join(globals_)}")
    for seg in segments:
        print(_plan_line(seg))


def _print_footer() -> None:
    footer = f"Run `{_brand.prog} --help <task>` for a task's options."
    print(f"\n{_describe.dim(footer, _color_out)}")


def _styled_name(name: str, width: int) -> str:
    """A task name for a listing: dim group prefix, bold leaf, padded."""
    pad = " " * (width - len(name))
    prefix, _, leaf = name.rpartition(" ")
    lead = _describe.dim(f"{prefix} ", _color_out) if prefix else ""
    return f"{lead}{_describe.bold(leaf, _color_out)}{pad}"


def _styled_help(help_text: str) -> str:
    """A help line for a listing: trailing status notes dimmed."""
    for marker in ("(runs until", "(unavailable:"):
        head, sep, note = help_text.partition(marker)
        if sep:
            return f"{head}{_describe.dim(f'{marker}{note}', _color_out)}"
    return help_text


def _print_list(tree: dict) -> None:
    rows = list(_describe.iter_tasks(tree))
    if not rows:
        print("No tasks defined.")
        return
    width = max(len(name) for name, _ in rows)
    print(_describe.bold("Tasks:", _color_out))
    for name, help_text in rows:
        line = f"  {_styled_name(name, width)}  {_styled_help(help_text)}"
        print(line.rstrip())


def _print_tree(node: dict, indent: str = "") -> None:
    # Top-level empty tree (indent sentinel) → mirror _print_list rather than
    # printing zero bytes and exiting 0.
    if not indent and not node["tasks"] and not node["groups"]:
        print("No tasks defined.")
        return
    dash = _describe.dim("—", _color_out)
    for name, task in node["tasks"].items():
        line = _describe.task_line(task)
        help_text = f"  {dash} {_styled_help(line)}" if line else ""
        print(f"{indent}{_describe.bold(name, _color_out)}{help_text}")
    for name, sub in node["groups"].items():
        label = f"  {dash} {sub['help']}" if sub["help"] else ""
        print(f"{indent}{_describe.bold_cyan(f'{name}/', _color_out)}{label}")
        _print_tree(sub, indent + "  ")


def _print_task_help(tree: dict, path: list[str]) -> None:
    # All phrasing (labels, details, examples) lives in `_describe`, shared
    # with the markdown exporter so help text and pages can never drift.
    node = tree
    for name in path[:-1]:
        node = node["groups"][name]
    task = node["tasks"][path[-1]]
    on = _color_out
    usage = _describe.paint_cli(_describe.usage_parts(_brand.prog, path, task), on)
    print(f"usage: {usage}")
    if task["help"]:
        print(f"\n  {task['help']}")
    if task.get("long"):  # the docstring's body, structure preserved
        body = "\n".join(f"  {ln}".rstrip() for ln in task["long"].splitlines())
        print(f"\n{body}")
    if task.get("infinite"):
        print(_describe.dim("\n  runs until you stop it — Ctrl-C", on))
    if task.get("disabled"):
        print(_describe.dim(f"\n  unavailable here: {task['disabled']}", on))
    positionals = [p for p in task["params"] if p["kind"] in ("argument", "variadic")]
    options = [p for p in task["params"] if p["kind"] in ("flag", "option")]
    for title, params in (("positionals", positionals), ("options", options)):
        if not params:
            continue
        rows = []
        for p in params:
            doc, mech = _describe.param_detail_parts(p)
            # The author's words stay bright; the mechanics dim beneath them.
            mech = _describe.dim(mech, on) if mech else ""
            detail = "; ".join(bit for bit in (doc, mech) if bit)
            rows.append((_describe.param_label(p), detail))
        width = max(len(label) for label, _ in rows)
        print(f"\n{_describe.bold(f'{title}:', on)}")
        for label, detail in rows:
            pad = " " * (width - len(label))
            print(f"  {_describe.bold(label, on)}{pad}  {detail}".rstrip())
    example = _describe.paint_cli(_describe.example_parts(path, task, _brand.prog), on)
    print(f"\n{_describe.dim('Example:', on)} {example}")
    if (shadows := task.get("shadows")) is not None:
        # This task overrides one further up the cascade — show the call
        # `inherited()` makes, so the forwarding line can be read off it.
        where = shadows.get("where") or "the cascade"
        print(_describe.dim(f"\nshadows {where} — inherited() calls it", on))
        usage = _describe.paint_cli(
            _describe.usage_parts(_brand.prog, path, shadows), on
        )
        print(f"  {usage}")


def _print_group_help(tree: dict, path: list[str]) -> None:
    node = tree
    for name in path:
        node = node["groups"][name]
    on = _color_out
    default = node.get("default")
    parts = [("prog", _brand.prog), *[("group", name) for name in path]]
    # A runnable group (one with `@group.default`) can run bare — its default —
    # so the task becomes optional.
    parts += [("opt", "[<task>]")] if default else [("req", "<task>")]
    parts += [("opt", "[options]")]
    print(f"usage: {_describe.paint_cli(parts, on)}")
    if node["help"]:
        print(f"\n  {node['help']}")
    if default:
        print(_describe.dim("\n  runs its default when no task is named", on))
    rows = list(_describe.iter_tasks(node))
    if rows:
        width = max(len(name) for name, _ in rows)
        print(f"\n{_describe.bold('tasks:', on)}")
        for name, help_text in rows:
            line = f"  {_styled_name(name, width)}  {_styled_help(help_text)}"
            print(line.rstrip())
    params = default["params"] if default else []
    options = [p for p in params if p["kind"] in ("flag", "option")]
    if options:
        rows2 = []
        for p in options:
            doc, mech = _describe.param_detail_parts(p)
            mech = _describe.dim(mech, on) if mech else ""
            detail = "; ".join(bit for bit in (doc, mech) if bit)
            rows2.append((_describe.param_label(p), detail))
        width2 = max(len(label) for label, _ in rows2)
        print(f"\n{_describe.bold('options:', on)}")
        for label, detail in rows2:
            pad = " " * (width2 - len(label))
            print(f"  {_describe.bold(label, on)}{pad}  {detail}".rstrip())


def _print_global_help(tree: dict) -> None:
    prog = _brand.prog
    parts = [
        ("prog", prog),
        ("opt", "[globals]"),
        ("req", "<task>"),
        ("opt", "[options]"),
        ("opt", "[<task> ...]"),
    ]
    print(f"usage: {_describe.paint_cli(parts, _color_out)}")
    print(f"\n{_describe.bold('globals (before the first task):', _color_out)}")
    rows = []
    for name, alias, _kind, hint, help_text in split.GLOBALS:
        label = f"{alias}, {name}" if alias else f"    {name}"
        if hint:
            label += f" {hint}"
        # `.replace` (not `.format`) so a help string containing braces can
        # never crash help output.
        rows.append((label, help_text.replace("{prog}", prog)))
    width = max(len(label) for label, _ in rows)
    for label, help_text in rows:
        pad = " " * (width - len(label))
        print(f"  {_describe.bold(label, _color_out)}{pad}  {help_text}")
    print()
    _print_list(tree)
    _print_footer()


def _wants_help(argv: list[str]) -> bool:
    """`-h`/`--help` anywhere before `--` turns the whole line into a help
    request — asking for help must never execute anything, wherever it lands
    on the line. After `--` it belongs to the passthrough."""
    for tok in argv:
        if tok == "--":
            return False
        if tok in ("-h", "--help"):
            return True
    return False


def _help_targets(
    tree: dict, argv: list[str]
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Group/task paths mentioned on a `--help` line, resolved leniently —
    plus the bare words that resolved to nothing, so the caller can refuse a
    `--help typo` instead of shrugging.

    The real splitter enforces arity — `--help add` must work even though
    `add` alone would be "missing required argument(s)" — so this walks group
    and task names only and skips every other token (option-shaped tokens and,
    once a target is found, its argument values).
    """
    _, i = split._parse_globals(argv, 0)
    targets: list[tuple[str, list[str]]] = []
    strays: list[str] = []
    while i < len(argv):
        if argv[i] == "--":
            break
        node, path = tree, []
        while i < len(argv) and argv[i] in node["groups"]:
            path.append(argv[i])
            node = node["groups"][argv[i]]
            i += 1
        if i < len(argv) and argv[i] in node["tasks"]:
            targets.append(("task", [*path, argv[i]]))
        elif path:
            targets.append(("group", path))
            continue  # the walk already consumed the group name(s)
        elif i < len(argv) and not argv[i].startswith("-"):
            strays.append(argv[i])
        i += 1
    return targets, strays


def _print_help(tree: dict, argv: list[str]) -> int:
    """`--help` alone covers fm itself; with names, the named groups/tasks.

    A name that matches nothing is a refusal (exit 2) with a suggestion —
    silently degrading to the global listing looked like an answer while
    teaching nothing. With at least one real target found, extra bare words
    stay lenient: they are argument values, not typos.
    """
    targets, strays = _help_targets(tree, argv)
    if not targets:
        if strays:
            known = [name for name, _ in _describe.iter_tasks(tree)]
            known += _describe.iter_group_paths(tree)
            # Help's *success* output is the one human-only surface; a refusal
            # still honours the envelope `--json` promised.
            return _refuse(
                _wants_json(argv),
                f"--help: unknown task or group {strays[0]!r}"
                f"{split._did_you_mean(strays[0], known)}",
            )
        _print_global_help(tree)
        return 0
    for index, (kind, path) in enumerate(targets):
        if index:
            print()
        if kind == "task":
            _print_task_help(tree, path)
        else:
            _print_group_help(tree, path)
    return 0


def _where(root: registry.Group, tree: dict, dotted: str) -> int:
    path = dotted.replace(".", " ").split()
    try:
        fn = executor.resolve(root, path)
    except (KeyError, IndexError):
        names = [name.replace(" ", ".") for name, _ in _describe.iter_tasks(tree)]
        _error(f"--where: unknown task {dotted!r}{split._did_you_mean(dotted, names)}")
        return 2
    chain = discover.shadow_chain(fn)
    lines = []
    for index, member in enumerate(chain):
        code = getattr(member, "__code__", None)
        if code is None:
            continue
        where = f"{code.co_filename}:{code.co_firstlineno}"
        # The winner first; anything it shadows follows, marked — so
        # "am I overriding something, and where is it?" is one command.
        lines.append(where if index == 0 else f"{where}   (shadowed)")
    if not lines:
        _error(f"--where: cannot locate source for {dotted!r}")
        return 2
    print("\n".join(lines))
    return 0


def _print_summary(
    results: list[executor.TaskResult],
    *,
    timings: bool,
    total: float,
) -> None:
    # The summary is commentary about the run, not the run's output — it goes
    # to stderr so `fm task > file` captures exactly what the task produced.
    # Each receipt is task-shaped (mark · name · time), the same grid as the
    # step lines above it, with the name in cyan — same family, one rank up.
    color = _color_err
    width = max((len(r.task) for r in results), default=0)
    for result in results:
        ok = result.ok
        cancelled = result.cancelled
        if color:
            if ok:
                mark = "\033[32m✓\033[0m"
            elif cancelled:
                mark = "\033[33m○\033[0m"  # cut off by fail-fast, not a failure
            else:
                mark = "\033[31m✗\033[0m"
            name = f"\033[1;36m{result.task:<{width}}\033[0m"
        else:
            word = "ok" if ok else ("cut" if cancelled else "FAIL")
            mark = f"{word:<4}"
            name = f"{result.task:<{width}}"
        timing = (
            f"({result.duration * 1000:.0f} ms)"
            if timings
            else f"({_progress.fmt_secs(result.duration)})"
        )
        if color:
            timing = f"\033[36m{timing}\033[0m"
        print(f"{mark} {name}  {timing}", file=sys.stderr)
        if cancelled:
            _error(f"{result.task}: cancelled — fail-fast stopped the run")
        elif result.error is not None:
            _error(f"{result.task}: {type(result.error).__name__}: {result.error}")
        elif not result.ok:
            _error(f"{result.task}: exited with code {result.code}")
    if len(results) > 1:  # one task's receipt already carries the total
        took = f"took {_progress.fmt_secs(total)}"
        if color:
            took = f"\033[2m{took}\033[0m"
        print(took, file=sys.stderr)


def _print_json(results: list[executor.TaskResult], *, total: float) -> None:
    payload = []
    for r in results:
        entry: dict[str, object] = {
            "task": r.task,
            "ok": r.ok,
            "cancelled": r.cancelled,
            "code": r.code,
            "duration_ms": round(r.duration * 1000, 3),
            "output": r.output,
            "steps": [
                {
                    "command": s.command,
                    "code": s.code,
                    "duration_ms": round(s.duration * 1000, 3),
                    "stdout": s.stdout,
                    "stderr": s.stderr,
                }
                for s in r.steps
            ],
            "error": None if r.error is None else str(r.error),
        }
        value = r.returned
        # An int return is the exit-code channel (duty's contract), not data;
        # None is "nothing to say". Everything else — bools included — is data.
        if value is not None and not (
            isinstance(value, int) and not isinstance(value, bool)
        ):
            try:
                json.dumps(value, default=_describe.json_default)
            except (TypeError, ValueError) as exc:  # ValueError: circular refs
                entry["returned_error"] = str(exc)
                _error(f"{r.task}: --json: return value dropped — {exc}")
            else:
                entry["returned"] = value
        payload.append(entry)
    # The stable machine surface: an envelope so post-1.0 additions (metadata,
    # summaries) never have to break consumers of the results list.
    print(
        json.dumps(
            {"schema": 1, "total_ms": round(total * 1000, 3), "results": payload},
            indent=2,
            default=_describe.json_default,
        )
    )


def _resolve_shell(shell: object, flag: str) -> str | None:
    """Resolve *shell* to a supported name for *flag*, or None after `_error`.

    A bare flag (`shell is True`) detects the invoking shell; an explicit value
    is lowercased and de-aliased (`nu`→`nushell`, `powershell`→`pwsh`).
    """
    from footman import _shellcomp

    supported = "|".join(_shellcomp.SHELLS)
    if shell is True:
        name = _shellcomp.detect_shell()
        if name is None:
            _error(
                f"{flag}: could not detect your shell — "
                f"name it explicitly: one of {supported}"
            )
            return None
    else:
        name = str(shell or "").lower()
        name = {"powershell": "pwsh", "nu": "nushell"}.get(name, name)  # muscle-memory
    if name not in _shellcomp.SHELLS:
        got = f" (got {name!r})" if name else ""
        _error(f"{flag} expects one of {supported}{got}")
        return None
    return name


def _install_completion(shell: object) -> int:
    from footman import _shellcomp

    name = _resolve_shell(shell, "--install-completion")
    if name is None:
        return 2
    if shell is True:
        print(f"detected shell: {name}")
    try:
        lines = _shellcomp.install(name, _brand.prog)
    except _shellcomp.InstallError as exc:
        _error(f"--install-completion {name}: {exc}")
        return 2
    for line in lines:
        print(line)
    return 0


def _uninstall_completion(shell: object) -> int:
    from footman import _shellcomp

    name = _resolve_shell(shell, "--uninstall-completion")
    if name is None:
        return 2
    if shell is True:
        print(f"detected shell: {name}")
    try:
        lines = _shellcomp.uninstall(name, _brand.prog)
    except _shellcomp.InstallError as exc:
        _error(f"--uninstall-completion {name}: {exc}")
        return 2
    for line in lines:
        print(line)
    return 0


def _setup_completion(shell: object) -> int:
    """Print the completion hook to stdout, for the current session only.

    `eval "$(prog --setup-completion zsh)"` enables completion without touching
    any rc file. A bare flag detects the shell; the detection note goes to
    stderr so stdout stays clean for `eval`.
    """
    from footman import _shellcomp

    name = _resolve_shell(shell, "--setup-completion")
    if name is None:
        return 2
    if shell is True:
        print(f"detected shell: {name}", file=sys.stderr)
    print(_shellcomp.script_for(name, _brand.prog))
    return 0


# --- orchestration -----------------------------------------------------------


def run(
    argv: list[str],
    brand: Brand = DEFAULT_BRAND,
    collect: list[executor.TaskResult] | None = None,
) -> int:
    """Run the CLI; when *collect* is given, extend it with the TaskResults.

    `collect` exists for `footman.testing.Runner`, which needs the structured
    results as well as the exit code and printed output.
    """
    try:
        return _run(argv, brand, collect)
    except KeyboardInterrupt:
        # In --json mode nothing has reached stdout yet (capture buffers task
        # output), so the envelope contract still holds at 130.
        return _refuse(_wants_json(argv), "interrupted", 130)


_WINDOWS = os.name == "nt"  # decided at import; a constant tests can steer


GC_INTERVAL_S = 24 * 3600


def _maybe_collect(cfg: dict[str, object]) -> None:
    """At most daily, and never on a fresh cache, spawn the collector.

    A missing stamp is *planted*, not acted on — the first run a cache ever
    sees schedules collection for tomorrow, so short-lived caches (a test
    suite's tmp dirs) never spawn anything. An aged stamp is re-touched
    *before* spawning, the refresh idiom: concurrent runs elect one
    collector, and a crashed child costs a day, not correctness.
    """
    if cfg.get("gc") is False or os.environ.get("FOOTMAN_NO_GC"):
        return
    cache = _paths.footman_cache_dir()
    stamp = cache / "gc.stamp"
    try:
        age = time.time() - stamp.stat().st_mtime
    except OSError:
        with contextlib.suppress(OSError):
            cache.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        return
    if age < GC_INTERVAL_S:
        return
    with contextlib.suppress(OSError):
        stamp.touch()
    _spawn_gc(cache, _paths.manifest_path(Path.cwd()).stem)


def _spawn_gc(cache: Path, skip_stem: str) -> None:
    """Detach the collector child — `_complete`'s refresh spawn, verbatim."""
    cmd = [
        sys.executable,
        "-c",
        "from footman import _gc; _gc.main()",
        str(cache),
        skip_stem,
    ]
    null = subprocess.DEVNULL
    try:
        if _WINDOWS:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            subprocess.Popen(
                cmd, stdin=null, stdout=null, stderr=null, creationflags=flags
            )
        else:
            subprocess.Popen(
                cmd, stdin=null, stdout=null, stderr=null, start_new_session=True
            )
    except OSError:
        return  # a background collector must never break a run


def _uv_handoff(argv: list[str], g: dict[str, object]) -> None:
    """Hand a globally-installed invocation to the project's own footman.

    The rule, one sentence: when the project's `uv.lock` pins footman and
    this interpreter is not already inside the project's environment, the
    invocation belongs to `uv run` — the project has declared what `fm`
    means there, version and all. Reached only where tasks would be
    imported: `--version`, completion management, and the TAB hot path
    never arrive here. Opt out with `uv = false` in `[tool.footman]` or
    `FOOTMAN_NO_UV=1`. The child carries `FOOTMAN_UV_REEXEC` as a loop
    belt for projects whose environment lives outside `.venv`.

    On POSIX the process is replaced (`execvp`: tty, signals, and exit
    code all belong to the child). Windows `exec*` lies — the parent
    exits while the child runs on — so there it spawns and waits,
    swallowing its own Ctrl-C (the console already delivered it to the
    child, which will exit 130 on its own terms).
    """
    if os.environ.get("FOOTMAN_UV_REEXEC") or os.environ.get("FOOTMAN_NO_UV"):
        return
    try:
        probe = Path(str(g.get("directory") or Path.cwd())).resolve(strict=True)
    except OSError:
        return  # a missing -C target: _run's own error path reports it
    root = next((p for p in (probe, *probe.parents) if (p / "uv.lock").is_file()), None)
    if root is None:
        return
    venv = root / ".venv"
    with contextlib.suppress(OSError):
        if venv.is_dir() and Path(sys.prefix).resolve().is_relative_to(venv.resolve()):
            return  # already the project's environment
    uv = shutil.which("uv")
    if uv is None:
        return
    try:
        with open(root / "uv.lock", "rb") as fh:
            lock = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return
    if not any(p.get("name") == "footman" for p in lock.get("package", [])):
        return
    try:
        cfg = config.load_config(
            probe,
            _paths.find_repo_root(probe),
            str(g["config"]) if g.get("config") else None,
            on_warning=lambda _: None,  # the real run repeats any warning
        )
    except config.ConfigError:
        return  # the real run reports the broken --config properly
    if cfg.get("uv") is False:
        return
    if g.get("verbose"):
        print(
            f"{_brand.prog}: handing off to uv run --project {root}",
            file=sys.stderr,
        )
    os.environ["FOOTMAN_UV_REEXEC"] = "1"
    cmd = [uv, "run", "--project", str(root), _brand.prog, *argv]
    if _WINDOWS:
        proc = subprocess.Popen(cmd)
        while True:
            try:
                raise SystemExit(proc.wait())
            except KeyboardInterrupt:
                continue
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(uv, cmd)


def _run(
    argv: list[str],
    brand: Brand,
    collect: list[executor.TaskResult] | None = None,
) -> int:
    global _brand
    _brand = brand
    try:
        pre_globals, _ = split._parse_globals(argv, 0)
    except split.ChainError as exc:
        return _refuse(_wants_json(argv), str(exc))
    g = _globals_to_dict(pre_globals)
    _set_colors(bool(g.get("no_color")))
    wants_help = _wants_help(argv)

    if g.get("version"):  # D7: --version wins even over --help
        return _print_version(bool(g.get("json")))
    # Asking for help must never touch the filesystem: `--install-completion
    # fish --help` used to write rc files before printing anything.
    if "install_completion" in g and not wants_help:
        return _install_completion(g.get("install_completion"))
    if "setup_completion" in g and not wants_help:
        return _setup_completion(g.get("setup_completion"))
    if "uninstall_completion" in g and not wants_help:
        return _uninstall_completion(g.get("uninstall_completion"))

    # May replace the process (POSIX) or exit with the child's code
    # (Windows); returns quietly whenever the handoff doesn't apply.
    _uv_handoff(argv, g)

    if not g.get("directory"):
        return _execute(argv, g, wants_help, collect)

    # -C must not permanently move the process (a `Runner.invoke` shares the
    # host pytest's cwd): chdir, run, then restore in a finally. The original
    # dir may have vanished mid-run, so the restore is best-effort.
    saved_cwd = os.getcwd()
    try:
        os.chdir(str(g["directory"]))
    except OSError as exc:
        return _refuse(bool(g.get("json")), f"-C {g['directory']}: {exc}")
    try:
        return _execute(argv, g, wants_help, collect)
    finally:
        with contextlib.suppress(OSError):
            os.chdir(saved_cwd)


def _execute(
    argv: list[str],
    g: dict[str, object],
    wants_help: bool,
    collect: list[executor.TaskResult] | None,
) -> int:
    """Discover the cascade, load + sync its manifest, then run the tree.

    Everything after globals/`--version`/`--install-completion`/`-C`: the
    disk-backed half that `run_group` (in-memory) deliberately skips.
    """
    # "Bare" means no chain was asked for — globals-only lines (`fm --json`,
    # `fm -k`) are listing-shaped, exactly like they are when tasks exist.
    _, after_globals = split._parse_globals(argv, 0)
    found = _discover(g, wants_help, bare=after_globals >= len(argv))
    if isinstance(found, int):
        return found
    files, cfg = found
    json_mode = bool(g.get("json"))

    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list) and plugins:
        from footman import compose

        try:
            compose.mount_plugins(base, plugins)
        except registry.RegistrationError as exc:
            return _refuse(json_mode, str(exc))

    try:
        reg = discover.load_tree(files, base=base)
    except discover.TasksImportError as exc:
        if isinstance(exc.original, registry.RegistrationError):
            # a user mistake, not a crash
            return _refuse(json_mode, f"{exc.path}: {exc.original}")
        return _refuse(
            json_mode,
            f"failed to import {exc.path}: "
            f"{type(exc.original).__name__}: {exc.original}",
        )
    except Exception as exc:  # report import failures cleanly, don't crash
        return _refuse(
            json_mode,
            f"failed to import the task cascade: {type(exc).__name__}: {exc}",
        )

    try:
        if g.get("tasks_file"):
            # -f loads one arbitrary file, not the cwd's cascade. Cache its
            # manifest under a (cwd, file) key — separate from the cwd's, so it
            # never poisons plain TAB there — so `fm -f <file> <TAB>` completes
            # that file's tasks. max_age=0: no background refresh (rebuilt on the
            # next -f run); a live refresh is a fast-follow.
            override = str(g.get("tasks_file"))
            tree = manifest.sync_manifest(
                reg,
                Path.cwd(),
                completion_max_age=0,
                tasks_file=override,
                path=_paths.source_manifest_path(Path.cwd(), Path(override)),
            )["tree"]
        else:
            cfg_tasks = cfg.get("tasks")
            tree = manifest.sync_manifest(
                reg,
                Path.cwd(),
                completion_max_age=config.completion_max_age(cfg),
                tasks_file=cfg_tasks
                if isinstance(cfg_tasks, str)
                else _brand.tasks_file,
            )["tree"]
    except manifest.ManifestError as exc:  # broken completer, bad markers, …
        return _refuse(json_mode, str(exc))

    code = _run_tree(reg, tree, argv, cfg, collect)
    # After the run, so it never adds latency before the user's command —
    # and after the uv handoff by construction (the handoff replaced this
    # process back in _run), so a pinned project's own footman collects.
    _maybe_collect(cfg)
    return code


def _run_tree(
    reg: registry.Group,
    tree: dict,
    argv: list[str],
    cfg: dict[str, object],
    collect: list[executor.TaskResult] | None,
) -> int:
    """The post-manifest tail: help/where/split/list/tree/dry-run/run/report.

    Shared by the disk path (`_execute`) and the in-memory path (`run_group`),
    so both honour `--help`/`--version`/`--list`/`--tree`/`--json` identically.
    Globals are re-derived from `argv` (already validated upstream).
    """
    g = _globals_to_dict(split._parse_globals(argv, 0)[0])
    json_mode = bool(g.get("json"))

    if _wants_help(argv):
        return _print_help(tree, argv)

    if g.get("where"):
        # Deliberately plain under --json too: `file:line` already is the
        # machine format.
        return _where(reg, tree, str(g["where"]))

    try:
        globals_, segments = split.split_chain(tree, argv)
    except split.ChainError as exc:
        return _refuse(json_mode, str(exc))

    if not segments:
        if json_mode:
            # The catalog envelope: the manifest tree, params and all — the
            # machine twin of --list/--tree (and of bare `fm`).
            print(json.dumps({"schema": 1, "tree": tree}, indent=2))
            return 0
        if g.get("tree"):
            _print_tree(tree)
        else:
            _print_list(tree)
        if tree["tasks"] or tree["groups"]:
            _print_footer()
        return 0

    if g.get("dry_run"):
        if json_mode:
            plan = [
                {
                    "task": s.task,
                    "values": s.values,
                    "variadic": s.variadic,
                    "passthrough": s.passthrough,
                }
                for s in segments
            ]
            payload = {"schema": 1, "globals": globals_, "plan": plan}
            print(json.dumps(payload, indent=2))
        else:
            _print_plan(globals_, segments)
        return 0
    sequential = bool(g.get("sequential")) or bool(cfg.get("sequential"))

    # The parallel width: -j/--jobs wins, then config `jobs`, then the
    # cores-minus-one default. Caps both engines (the scheduler's pool and
    # parallel() in task bodies) and is part of the timing key — a -j2 run
    # has a genuinely different duration distribution.
    if g.get("jobs") is not None:
        try:
            jobs = int(str(g["jobs"]))
        except ValueError:
            jobs = 0
        if jobs < 1:
            return _refuse(
                json_mode,
                f"--jobs expects a positive integer (got {g['jobs']!r})",
            )
    elif (
        isinstance(cfg_jobs := cfg.get("jobs"), int)
        and not isinstance(cfg_jobs, bool)
        and cfg_jobs >= 1
    ):
        jobs = cfg_jobs
    else:
        jobs = _progress.default_jobs()

    fetch_cfg = cfg.get("fetch")
    backend = fetch_cfg.get("backend") if isinstance(fetch_cfg, dict) else None
    ctx_config = {
        "fetch_backend": str(backend) if isinstance(backend, str) else "",
        "quiet": bool(g.get("quiet")),
        "verbose": bool(g.get("verbose")),
        "no_color": bool(g.get("no_color")),
        # Tasks can know who invoked them (a branded CLI's prog) — the
        # taskdocs plugin brands its output with this, for one.
        "prog": _brand.prog,
        # The user's -s/config request, so parallel() in task bodies
        # serialises too — not the scheduler's single-node routing.
        "sequential": sequential,
        "jobs": jobs,
        # Interactivity globals: --yes auto-answers confirm() gates, --no-input
        # refuses to prompt (a required prompt errors instead of hanging).
        "assume_yes": bool(g.get("yes")),
        "no_input": bool(g.get("no_input")),
    }

    # The timing story: --no-progress (one run) or `progress = false` in
    # config (permanently) turns the whole apparatus off. A run is
    # *predictable* when it's on, every task consented, and this is the real
    # cascade (-f runs pollute no cache, times included) — only then do we
    # estimate from history and record the outcome.
    progress_on = not g.get("no_progress") and cfg.get("progress") is not False
    predictable = (
        progress_on
        and not g.get("tasks_file")
        and schedule.dag_wants_progress(reg, segments)
    )
    est = times_key = None
    context.seed_cmd_width(0)  # each run learns (or is seeded) afresh
    if predictable:
        times_key = _progress.chain_key(segments, sequential=sequential, jobs=jobs)
        est = _progress.estimate(_progress.load_runs(Path.cwd(), times_key))
        context.seed_cmd_width(_progress.load_cmd_width(Path.cwd(), times_key))
    if est is not None and not g.get("quiet") and not sys.stderr.isatty():
        # No TTY (CI, a pipe): the one-line version of the bar, up front.
        print(f"  {'eta':>4}  ~{_progress.fmt_secs(est.typical)}", file=sys.stderr)

    # Tri-state on the command line: `-k` forces keep-going, `--fail-fast` forces
    # fail-fast, neither leaves it to the invoked task's declared policy.
    cli_keep_going = True if g.get("keep_going") else None
    if g.get("fail_fast"):
        cli_keep_going = False

    start = time.perf_counter()
    try:
        results = schedule.run_plan(
            reg,
            segments,
            sequential=sequential,
            keep_going=cli_keep_going,  # None = unspecified; run_plan scopes per node
            capture=json_mode,
            ctx_config=ctx_config,
            estimate=est,
            progress=progress_on,
            jobs=jobs,
        )
    except split.ChainError as exc:  # e.g. passthrough with no *args
        return _refuse(json_mode, str(exc))
    total = time.perf_counter() - start

    if collect is not None:
        collect.extend(results)
    if predictable and times_key and results and all(r.ok for r in results):
        # Green runs teach: the duration, and the step-alignment width.
        _progress.record(Path.cwd(), times_key, total, cmd_width=context.cmd_width())

    if json_mode:
        _print_json(results, total=total)
    elif not g.get("quiet"):
        _print_summary(results, timings=bool(g.get("timings")), total=total)

    # The exit code is the first genuine failure's — a cancelled task carries
    # only a kill signal, so it's the fallback, not the headline.
    failed = [r for r in results if not r.ok]
    genuine = next((r.code or 1 for r in failed if not r.cancelled), None)
    if genuine is not None:
        return genuine
    return next((r.code or 1 for r in failed), 0)


def run_group(
    root: registry.Group,
    argv: list[str],
    brand: Brand = DEFAULT_BRAND,
    collect: list[executor.TaskResult] | None = None,
) -> int:
    """Drive an in-memory Group tree: globals, `--version`, manifest, run.

    The in-memory sibling of `_run`, minus discovery/cascade/config and the
    `-C`/`--install-completion` machinery those imply. No KeyboardInterrupt
    wrapper (D13): a test runner must let Ctrl-C reach pytest. This is the
    single shared surface `footman.testing.Runner` drives, so its Group mode
    can never drift from the real CLI's help/version/list/tree/json behaviour.
    """
    global _brand
    _brand = brand
    try:
        pre_globals, _ = split._parse_globals(argv, 0)
    except split.ChainError as exc:
        return _refuse(_wants_json(argv), str(exc))
    g = _globals_to_dict(pre_globals)
    _set_colors(bool(g.get("no_color")))

    if g.get("version"):
        return _print_version(bool(g.get("json")))

    tree = manifest.build_manifest(root)["tree"]
    return _run_tree(root, tree, argv, {}, collect)
