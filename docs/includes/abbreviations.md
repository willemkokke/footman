*[manifest]: The cached JSON description of your task tree; it powers completion and the chain split without importing your code.
*[cascade]: The merged set of tasks.py (or config) files from the repo root down to your current directory; nearer files win.
*[chain]: Several tasks on one command line; independent ones run in parallel by default.
*[taught error]: An error that names the culprit, states the expectation, and proposes the fix — footman treats errors as product surface.
*[fan-out]: Running several tasks or steps concurrently, from a chain or a parallel() call inside a task body.
*[passthrough]: Everything after -- on the command line, handed to a task verbatim via *args or passthrough().
*[stale-while-revalidate]: Serving the cached completion answer at once while a detached rebuild refreshes it for next time.
*[in-process]: Running a Python tool inside footman's own process via its console-script entry point, skipping the subprocess spawn.
*[sequential]: The run-wide mode (-s/--sequential, or sequential = true in config): no pool at all, every task one at a time, output live.
*[serial]: One task's lane in the globals arbiter (@task(serial=True)): it owns the process globals, at most one at a time — and unlike a sequential run, the parallel pool keeps running around it.
*[wrapper verb]: A subcommand that runs another command (uv run, docker exec); its own flags go before the wrapped command.
