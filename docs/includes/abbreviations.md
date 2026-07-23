*[manifest]: The cached JSON description of your task tree; it powers completion and the chain split without importing your code.
*[cascade]: The merged set of tasks.py (or config) files from the repo root down to your current directory; nearer files win.
*[chain]: Several tasks on one command line; independent ones run in parallel by default.
*[taught error]: An error that names the culprit, states the expectation, and proposes the fix — footman treats errors as product surface.
*[fan-out]: Running several tasks or thunks concurrently, from a chain or a parallel() call inside a task body.
*[thunk]: A zero-argument callable (often a lambda or functools.partial) that binds a task's arguments so it can be scheduled.
*[passthrough]: Everything after -- on the command line, handed to a task verbatim via *args or passthrough().
*[stale-while-revalidate]: Serving the cached completion answer at once while a detached rebuild refreshes it for next time.
*[in-process]: Running a Python tool inside footman's own process via its console-script entry point, skipping the subprocess spawn.
*[wrapper verb]: A subcommand that runs another command (uv run, docker exec); its own flags go before the wrapped command.
