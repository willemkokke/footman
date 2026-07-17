"""The execution path: load tasks, refresh the manifest, run the chain.

This is everything that happens for a real `fm ...` invocation (as opposed to
the completion hot path). It imports the user's tasks file — paying that cost is
fine here — resolves the command line against the freshly-built manifest, and
runs the resulting segments, honouring the global options.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from footman import (
    _paths,
    config,
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
# the simplest way to reach it from the error/version helpers.
_brand: Brand = DEFAULT_BRAND


def _error(message: str) -> None:
    sys.stderr.write(f"{_brand.prog}: {message}\n")


def _globals_to_dict(tokens: list[str]) -> dict[str, object]:
    """Interpret the splitter's canonical global tokens into a flat mapping."""
    result: dict[str, object] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        name = tok.split("=", 1)[0]
        key = name.lstrip("-").replace("-", "_")
        if split._GLOBAL_KIND.get(name) == "option":
            if "=" in tok:
                result[key] = tok.split("=", 1)[1]
                i += 1
            else:
                result[key] = tokens[i + 1] if i + 1 < len(tokens) else ""
                i += 2
        else:
            result[key] = True
            i += 1
    return result


def _discover(g: dict[str, object]) -> tuple[list[Path], dict[str, object]] | int:
    """Resolve the task files to load and the merged config for this cwd.

    `-f/--tasks-file` is the escape hatch: it loads exactly one file, no
    cascade. Otherwise footman collects every `tasks.py` from the repo root
    (the `.git` ceiling) down to the cwd. Returns `(files, config)` or, when
    nothing was found, the exit code to return (0 for a listing, 2 otherwise).
    """
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    try:
        cfg = config.load_config(
            cwd,
            ceiling,
            g.get("config"),  # type: ignore[arg-type]
            on_warning=_error,
        )
    except config.ConfigError as exc:
        _error(f"--config: {exc}")
        return 2

    override = g.get("tasks_file")
    if override:
        one = Path(str(override)).expanduser()
        files = [one] if one.is_file() else []
    else:
        filename = cfg.get("tasks")
        name = filename if isinstance(filename, str) else _paths.DEFAULT_TASKS_FILE
        files = _paths.task_files(cwd, ceiling, name)

    if files:
        return files, cfg

    looked = override or cfg.get("tasks") or _paths.DEFAULT_TASKS_FILE
    if g.get("help") or g.get("list") or g.get("tree"):
        print(f"No tasks file found (looked for {looked}).")
        return 0
    _error(
        f"no tasks file found (looked for {looked}); "
        f"create one or pass -f/--tasks-file."
    )
    return 2


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
    line = f"  -> {seg.task}  " + " ".join(parts)
    if seg.passthrough is not None:
        line += f"  [-- {' '.join(seg.passthrough)}]"
    return line.rstrip()


def _print_plan(globals_: list[str], segments: list[Segment]) -> None:
    if globals_:
        print(f"  globals: {' '.join(globals_)}")
    for seg in segments:
        print(_plan_line(seg))


def _iter_tasks(node: dict, prefix: str = ""):
    for name, task in node["tasks"].items():
        yield f"{prefix}{name}", _task_line(task)
    for name, sub in node["groups"].items():
        yield from _iter_tasks(sub, f"{prefix}{name} ")


def _task_line(task: dict) -> str:
    """A task's one-line description, with its availability if disabled."""
    note = f"(unavailable: {task['disabled']})" if task.get("disabled") else ""
    return f"{task['help']}  {note}".strip() if note else task["help"]


def _print_list(tree: dict) -> None:
    rows = list(_iter_tasks(tree))
    if not rows:
        print("No tasks defined.")
        return
    width = max(len(name) for name, _ in rows)
    print("Tasks:")
    for name, help_text in rows:
        print(f"  {name:<{width}}  {help_text}".rstrip())


def _print_tree(node: dict, indent: str = "") -> None:
    for name, task in node["tasks"].items():
        line = _task_line(task)
        help_text = f"  — {line}" if line else ""
        print(f"{indent}{name}{help_text}")
    for name, sub in node["groups"].items():
        label = f"  — {sub['help']}" if sub["help"] else ""
        print(f"{indent}{name}/{label}")
        _print_tree(sub, indent + "  ")


_TYPE_WORD = {
    "bool": "true/false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


def _value_hint(p: dict) -> str:
    """The value placeholder shown for an option/argument in help output."""
    if p.get("mapping"):
        return "KEY=VALUE"
    choices = p.get("choices")
    if choices:
        return "{" + "|".join(choices) + "}"
    types = p.get("types")
    if types:
        return "|".join(t.upper() for t in types)
    return "VALUE"


def _usage_fragment(p: dict) -> str:
    kind = p["kind"]
    if kind == "flag":
        return f"[--{p['name']}]"
    if kind == "option":
        core = f"--{p['name']} {_value_hint(p)}"
        many = p.get("multiple") or p.get("mapping")
        return f"[{core} ...]" if many else f"[{core}]"
    if kind == "variadic":
        return f"[<{p['name']}> ...]"
    suffix = "..." if p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def _param_label(p: dict) -> str:
    kind = p["kind"]
    if kind == "flag":
        return f"--{p['name']}"
    if kind == "option":
        return f"--{p['name']} {_value_hint(p)}"
    suffix = "..." if kind == "variadic" or p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def _param_detail(p: dict) -> str:
    bits: list[str] = []
    if p["kind"] == "flag":
        bits.append(f"flag (--no-{p['name']} to disable)")
    choices = p.get("choices")
    if choices:
        bits.append("one of " + "|".join(choices))
    elif p.get("types"):
        bits.append(" or ".join(_TYPE_WORD.get(str(t), str(t)) for t in p["types"]))
    if p.get("mapping"):
        bits.append("KEY=VALUE pairs (repeat appends)")
    if p.get("multiple") or p.get("mapping"):
        bits.append("repeatable" if p.get("nosplit") else "repeatable/comma-split")
    if p["kind"] == "variadic":
        bits.append("extra arguments (also receives everything after --)")
    return "; ".join(bits)


def _print_task_help(tree: dict, path: list[str]) -> None:
    node = tree
    for name in path[:-1]:
        node = node["groups"][name]
    task = node["tasks"][path[-1]]
    fragments = [f for p in task["params"] if (f := _usage_fragment(p))]
    print(" ".join([f"usage: {_brand.prog}", *path, *fragments]))
    if task["help"]:
        print(f"\n  {task['help']}")
    if task.get("disabled"):
        print(f"\n  unavailable here: {task['disabled']}")
    positionals = [p for p in task["params"] if p["kind"] in ("argument", "variadic")]
    options = [p for p in task["params"] if p["kind"] in ("flag", "option")]
    for title, params in (("positionals", positionals), ("options", options)):
        if not params:
            continue
        rows = [(_param_label(p), _param_detail(p)) for p in params]
        width = max(len(label) for label, _ in rows)
        print(f"\n{title}:")
        for label, detail in rows:
            print(f"  {label:<{width}}  {detail}".rstrip())


def _print_group_help(tree: dict, path: list[str]) -> None:
    node = tree
    for name in path:
        node = node["groups"][name]
    scope = " ".join(path)
    print(f"usage: {_brand.prog} {scope} <task> [options]")
    if node["help"]:
        print(f"\n  {node['help']}")
    rows = list(_iter_tasks(node))
    if rows:
        width = max(len(name) for name, _ in rows)
        print("\ntasks:")
        for name, help_text in rows:
            print(f"  {name:<{width}}  {help_text}".rstrip())


def _print_global_help(tree: dict) -> None:
    prog = _brand.prog
    print(f"usage: {prog} [globals] <task> [options] [<task> ...]")
    print("\nglobals (before the first task):")
    rows = []
    for name, alias, _kind, hint, help_text in split.GLOBALS:
        label = f"{alias}, {name}" if alias else f"    {name}"
        if hint:
            label += f" {hint}"
        rows.append((label, help_text))
    width = max(len(label) for label, _ in rows)
    for label, help_text in rows:
        print(f"  {label:<{width}}  {help_text}")
    print()
    _print_list(tree)
    print(f"\nRun `{prog} --help <task>` for a task's options.")


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


def _help_targets(tree: dict, argv: list[str]) -> list[tuple[str, list[str]]]:
    """Group/task paths mentioned on a `--help` line, resolved leniently.

    The real splitter enforces arity — `--help add` must work even though
    `add` alone would be "missing required argument(s)" — so this walks group
    and task names only and skips every other token.
    """
    _, i = split._parse_globals(argv, 0)
    targets: list[tuple[str, list[str]]] = []
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
        i += 1
    return targets


def _print_help(tree: dict, argv: list[str]) -> int:
    """`--help` alone covers fm itself; with names, the named groups/tasks."""
    targets = _help_targets(tree, argv)
    if not targets:
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


def _where(root: registry.Group, dotted: str) -> int:
    path = dotted.replace(".", " ").split()
    try:
        fn = executor.resolve(root, path)
    except (KeyError, IndexError):
        _error(f"--where: unknown task {dotted!r}")
        return 2
    code = getattr(fn, "__code__", None)
    if code is None:
        _error(f"--where: cannot locate source for {dotted!r}")
        return 2
    print(f"{code.co_filename}:{code.co_firstlineno}")
    return 0


def _print_summary(results: list[executor.TaskResult], *, timings: bool) -> None:
    for result in results:
        mark = "ok" if result.ok else "FAIL"
        timing = f"  ({result.duration * 1000:.0f} ms)" if timings else ""
        line = f"  {mark:>4}  {result.task}{timing}"
        print(line)
        if result.error is not None:
            _error(f"{result.task}: {type(result.error).__name__}: {result.error}")
        elif not result.ok:
            _error(f"{result.task}: exited with code {result.code}")


def _print_json(results: list[executor.TaskResult]) -> None:
    payload = [
        {
            "task": r.task,
            "ok": r.ok,
            "code": r.code,
            "duration_ms": round(r.duration * 1000, 3),
            "output": r.output,
            "steps": [
                {
                    "command": s.command,
                    "code": s.code,
                    "duration_ms": round(s.duration * 1000, 3),
                    "output": s.output,
                }
                for s in r.steps
            ],
            "error": None if r.error is None else str(r.error),
        }
        for r in results
    ]
    # The stable machine surface: an envelope so post-1.0 additions (metadata,
    # summaries) never have to break consumers of the results list.
    print(json.dumps({"schema": 1, "results": payload}, indent=2))


def _install_completion(shell: object) -> int:
    from footman import _shellcomp

    name = str(shell or "").lower()
    aliases = {"powershell": "pwsh", "nu": "nushell"}  # muscle-memory aliases
    name = aliases.get(name, name)
    if name not in _shellcomp.SHELLS:
        supported = "|".join(_shellcomp.SHELLS)
        got = f" (got {name!r})" if name else ""
        _error(f"--install-completion expects one of {supported}{got}")
        return 2
    try:
        lines = _shellcomp.install(name, _brand.prog)
    except _shellcomp.InstallError as exc:
        _error(f"--install-completion {name}: {exc}")
        return 2
    for line in lines:
        print(line)
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
        _error("interrupted")
        return 130


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
        _error(str(exc))
        return 2
    g = _globals_to_dict(pre_globals)

    if g.get("version"):
        print(f"{_brand.name} {_brand.version}")
        return 0
    if "install_completion" in g:
        return _install_completion(g.get("install_completion"))

    if g.get("directory"):
        try:
            os.chdir(str(g["directory"]))
        except OSError as exc:
            _error(f"-C {g['directory']}: {exc}")
            return 2

    found = _discover(g)
    if isinstance(found, int):
        return found
    files, cfg = found

    base = registry.Group("root")
    plugins = cfg.get("plugins")
    if isinstance(plugins, list) and plugins:
        from footman import compose

        try:
            compose.mount_plugins(base, plugins)
        except registry.RegistrationError as exc:
            _error(str(exc))
            return 2

    try:
        reg = discover.load_tree(files, base=base)
    except discover.TasksImportError as exc:
        if isinstance(exc.original, registry.RegistrationError):
            _error(f"{exc.path}: {exc.original}")  # a user mistake, not a crash
        else:
            _error(
                f"failed to import {exc.path}: "
                f"{type(exc.original).__name__}: {exc.original}"
            )
        return 2
    except Exception as exc:  # report import failures cleanly, don't crash
        _error(f"failed to import the task cascade: {type(exc).__name__}: {exc}")
        return 2

    try:
        tree = manifest.sync_manifest(reg, Path.cwd())["tree"]
    except manifest.ManifestError as exc:  # broken completer, bad markers, …
        _error(str(exc))
        return 2

    if _wants_help(argv):
        return _print_help(tree, argv)

    if g.get("where"):
        return _where(reg, str(g["where"]))

    try:
        globals_, segments = split.split_chain(tree, argv)
    except split.ChainError as exc:
        _error(str(exc))
        return 2

    if not segments:
        if g.get("tree"):
            _print_tree(tree)
        else:
            _print_list(tree)
        return 0

    if g.get("dry_run"):
        _print_plan(globals_, segments)
        return 0

    json_mode = bool(g.get("json"))
    sequential = bool(g.get("sequential")) or bool(cfg.get("sequential"))
    ctx_config = {
        "quiet": bool(g.get("quiet")),
        "verbose": bool(g.get("verbose")),
        "no_color": bool(g.get("no_color")),
    }
    try:
        results = schedule.run_plan(
            reg,
            segments,
            sequential=sequential,
            keep_going=bool(g.get("keep_going")),
            capture=json_mode,
            ctx_config=ctx_config,
        )
    except split.ChainError as exc:  # e.g. passthrough with no *args
        _error(str(exc))
        return 2

    if collect is not None:
        collect.extend(results)

    if json_mode:
        _print_json(results)
    elif not g.get("quiet"):
        _print_summary(results, timings=bool(g.get("timings")))

    return next((r.code or 1 for r in results if not r.ok), 0)
