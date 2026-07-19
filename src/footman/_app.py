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
import sys
from pathlib import Path

from footman import (
    _describe,
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


def _discover(
    g: dict[str, object], wants_help: bool, bare: bool
) -> tuple[list[Path], dict[str, object]] | int:
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
        return _refuse(bool(g.get("json")), f"--config: {exc}")

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
    line = f"  -> {seg.task}  " + " ".join(parts)
    if seg.passthrough is not None:
        line += f"  [-- {' '.join(seg.passthrough)}]"
    return line.rstrip()


def _print_plan(globals_: list[str], segments: list[Segment]) -> None:
    if globals_:
        print(f"  globals: {' '.join(globals_)}")
    for seg in segments:
        print(_plan_line(seg))


def _print_footer() -> None:
    print(f"\nRun `{_brand.prog} --help <task>` for a task's options.")


def _print_list(tree: dict) -> None:
    rows = list(_describe.iter_tasks(tree))
    if not rows:
        print("No tasks defined.")
        return
    width = max(len(name) for name, _ in rows)
    print("Tasks:")
    for name, help_text in rows:
        print(f"  {name:<{width}}  {help_text}".rstrip())


def _print_tree(node: dict, indent: str = "") -> None:
    # Top-level empty tree (indent sentinel) → mirror _print_list rather than
    # printing zero bytes and exiting 0.
    if not indent and not node["tasks"] and not node["groups"]:
        print("No tasks defined.")
        return
    for name, task in node["tasks"].items():
        line = _describe.task_line(task)
        help_text = f"  — {line}" if line else ""
        print(f"{indent}{name}{help_text}")
    for name, sub in node["groups"].items():
        label = f"  — {sub['help']}" if sub["help"] else ""
        print(f"{indent}{name}/{label}")
        _print_tree(sub, indent + "  ")


def _print_task_help(tree: dict, path: list[str]) -> None:
    # All phrasing (labels, details, examples) lives in `_describe`, shared
    # with the markdown exporter so help text and pages can never drift.
    node = tree
    for name in path[:-1]:
        node = node["groups"][name]
    task = node["tasks"][path[-1]]
    fragments = [f for p in task["params"] if (f := _describe.usage_fragment(p))]
    print(" ".join([f"usage: {_brand.prog}", *path, *fragments]))
    if task["help"]:
        print(f"\n  {task['help']}")
    if task.get("long"):  # the docstring's body, structure preserved
        body = "\n".join(f"  {ln}".rstrip() for ln in task["long"].splitlines())
        print(f"\n{body}")
    if task.get("disabled"):
        print(f"\n  unavailable here: {task['disabled']}")
    positionals = [p for p in task["params"] if p["kind"] in ("argument", "variadic")]
    options = [p for p in task["params"] if p["kind"] in ("flag", "option")]
    for title, params in (("positionals", positionals), ("options", options)):
        if not params:
            continue
        rows = [(_describe.param_label(p), _describe.param_detail(p)) for p in params]
        width = max(len(label) for label, _ in rows)
        print(f"\n{title}:")
        for label, detail in rows:
            print(f"  {label:<{width}}  {detail}".rstrip())
    print(f"\nExample: {_describe.example(path, task, _brand.prog)}")


def _print_group_help(tree: dict, path: list[str]) -> None:
    node = tree
    for name in path:
        node = node["groups"][name]
    scope = " ".join(path)
    print(f"usage: {_brand.prog} {scope} <task> [options]")
    if node["help"]:
        print(f"\n  {node['help']}")
    rows = list(_describe.iter_tasks(node))
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
        # `.replace` (not `.format`) so a help string containing braces can
        # never crash help output.
        rows.append((label, help_text.replace("{prog}", prog)))
    width = max(len(label) for label, _ in rows)
    for label, help_text in rows:
        print(f"  {label:<{width}}  {help_text}")
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
    code = getattr(fn, "__code__", None)
    if code is None:
        _error(f"--where: cannot locate source for {dotted!r}")
        return 2
    print(f"{code.co_filename}:{code.co_firstlineno}")
    return 0


def _print_summary(results: list[executor.TaskResult], *, timings: bool) -> None:
    # The summary is commentary about the run, not the run's output — it goes
    # to stderr so `fm task > file` captures exactly what the task produced.
    for result in results:
        mark = "ok" if result.ok else "FAIL"
        timing = f"  ({result.duration * 1000:.0f} ms)" if timings else ""
        line = f"  {mark:>4}  {result.task}{timing}"
        print(line, file=sys.stderr)
        if result.error is not None:
            _error(f"{result.task}: {type(result.error).__name__}: {result.error}")
        elif not result.ok:
            _error(f"{result.task}: exited with code {result.code}")


def _print_json(results: list[executor.TaskResult]) -> None:
    payload = []
    for r in results:
        entry: dict[str, object] = {
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
            {"schema": 1, "results": payload},
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
            # -f loads one arbitrary file, not the cwd's cascade — writing its
            # manifest into the cwd's completion cache would poison TAB there
            # until the next plain run. Build fresh, touch no cache.
            tree = manifest.build_manifest(reg)["tree"]
        else:
            tree = manifest.sync_manifest(
                reg, Path.cwd(), completion_max_age=config.completion_max_age(cfg)
            )["tree"]
    except manifest.ManifestError as exc:  # broken completer, bad markers, …
        return _refuse(json_mode, str(exc))

    return _run_tree(reg, tree, argv, cfg, collect)


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
        return _refuse(json_mode, str(exc))

    if collect is not None:
        collect.extend(results)

    if json_mode:
        _print_json(results)
    elif not g.get("quiet"):
        _print_summary(results, timings=bool(g.get("timings")))

    return next((r.code or 1 for r in results if not r.ok), 0)


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

    if g.get("version"):
        return _print_version(bool(g.get("json")))

    tree = manifest.build_manifest(root)["tree"]
    return _run_tree(root, tree, argv, {}, collect)
