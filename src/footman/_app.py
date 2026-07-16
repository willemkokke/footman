"""The execution path: load tasks, refresh the manifest, run the chain.

This is everything that happens for a real ``fm ...`` invocation (as opposed to
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
    __version__,
    _paths,
    config,
    discover,
    executor,
    manifest,
    registry,
    schedule,
    split,
)
from footman.split import Segment


def _error(message: str) -> None:
    sys.stderr.write(f"fm: {message}\n")


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

    ``-f/--tasks-file`` is the escape hatch: it loads exactly one file, no
    cascade. Otherwise footman collects every ``tasks.py`` from the repo root
    (the ``.git`` ceiling) down to the cwd. Returns ``(files, config)`` or, when
    nothing was found, the exit code to return (0 for a listing, 2 otherwise).
    """
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = config.load_config(cwd, ceiling, g.get("config"))  # type: ignore[arg-type]

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
        yield f"{prefix}{name}", task["help"]
    for name, sub in node["groups"].items():
        yield from _iter_tasks(sub, f"{prefix}{name} ")


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
        help_text = f"  — {task['help']}" if task["help"] else ""
        print(f"{indent}{name}{help_text}")
    for name, sub in node["groups"].items():
        label = f"  — {sub['help']}" if sub["help"] else ""
        print(f"{indent}{name}/{label}")
        _print_tree(sub, indent + "  ")


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
    print(json.dumps(payload, indent=2))


def _install_completion(shell: object) -> int:
    _error(
        "shell completion install is not wired up yet; "
        "the resolver works today via `fm --complete`. Coming in a later cut."
    )
    return 1


# --- orchestration -----------------------------------------------------------


def run(argv: list[str]) -> int:
    try:
        pre_globals, _ = split._parse_globals(argv, 0)
    except split.ChainError as exc:
        _error(str(exc))
        return 2
    g = _globals_to_dict(pre_globals)

    if g.get("version"):
        print(f"footman {__version__}")
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

    try:
        reg = discover.load_tree(files)
    except Exception as exc:  # report import failures cleanly, don't crash
        culprit = files[-1] if len(files) == 1 else "the task cascade"
        _error(f"failed to import {culprit}: {type(exc).__name__}: {exc}")
        return 2

    tree = manifest.sync_manifest(reg, Path.cwd())["tree"]

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

    if json_mode:
        _print_json(results)
    elif not g.get("quiet"):
        _print_summary(results, timings=bool(g.get("timings")))

    return next((r.code or 1 for r in results if not r.ok), 0)
