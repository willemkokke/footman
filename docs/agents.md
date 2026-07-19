# AI agents

footman's machine surface — one catalog call, taught refusals, a single-JSON
stdout, structured results with task-returned data — is what a coding agent
needs to drive a project safely. This page packages it: a paste-ready
instructions snippet, and hooks that keep an agent's work formatted, linted,
and gated.

An agent-readable index of this whole site lives at
[`llms.txt`](https://willemkokke.github.io/footman/llms.txt) (and the full
text at
[`llms-full.txt`](https://willemkokke.github.io/footman/llms-full.txt)).

## The snippet

Put this in `CLAUDE.md` for Claude Code — the identical text works as
`AGENTS.md` for Codex, Cursor, Copilot, Zed, and most other agents (Gemini
CLI reads it as `GEMINI.md`). Two blanks to fill for your project: the
runner prefix and the gate task.

```markdown
## Tasks (footman)

Tasks are typed Python functions in `tasks.py`, run with `uv run fm`.
The gate is `uv run fm check` — run it before calling any change done;
it must exit 0.

- Discover: `fm --list` (tasks + descriptions), or `fm --json --list`
  for the full tree with parameter types, choices, and defaults.
- Inspect: `fm --help <task>` — typed usage, options, and an example.
  `--help` anywhere on the line never executes anything.
- Validate a command line without running it: `fm --json --dry-run <chain>`.
- Run for machines: `fm --json <chain>` — stdout is exactly one JSON
  envelope: {"schema": 1, "results": [{task, ok, code, duration_ms,
  output, steps, error, returned}]}. A task's return value lands in
  `returned`; refusals put a taught message in a top-level `error`.
- Jump to a task's source: `fm --where <task>` prints file:line.

Grammar: globals (`--json`, `-k`, …) go **before** the first task; a
task's options come right after that task; several tasks on one line
form a chain, and independent tasks run in parallel (output never
interleaves). Everything after `--` passes through to the task's
`*args`.

Exit codes: 0 all ok · 1 a task raised · N a task exited N · 2 footman
refused the line (the stderr message states the fix) · 130 interrupted.

To add or change tasks, edit `tasks.py` — the signature is the CLI.
Never edit the completion cache under `~/.cache/footman/`; it's derived.
```

## Hooks: Claude Code

Two recipes for `.claude/settings.json`. The mechanics in one sentence: a
hook's **stderr plus exit code 2** is fed back to Claude as something to
fix; anything else is display-only — so route footman's output to stderr
and let the exit code do the talking.

**Format and lint after every edit** — the tree stays clean as the agent
works, and lint failures land straight back in its context:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "uv run fm format lint 1>&2 || exit 2" }
        ]
      }
    ]
  }
}
```

**The gate as the definition of done** — a `Stop` hook that refuses to let
the session end red. `stop_hook_active` is the loop guard: when this stop
*is already* the retry, skip, so a stubborn failure can't ping-pong forever:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "jq -e '.stop_hook_active' >/dev/null && exit 0; uv run fm check 1>&2 || exit 2"
          }
        ]
      }
    ]
  }
}
```

Two refinements when you need them: gate the PostToolUse command on Python
files (`p=$(jq -r '.tool_input.file_path // empty'); case "$p" in *.py) …;;
esac`) in repos where non-Python edits dominate, and swap `check` for a
lighter chain if the full gate is slow.

## Hooks: Cursor

`.cursor/hooks.json` (project hooks run from the project root):

```json
{
  "version": 1,
  "hooks": {
    "afterFileEdit": [{ "command": ".cursor/hooks/fm-format.sh" }],
    "stop":          [{ "command": ".cursor/hooks/fm-gate.sh" }]
  }
}
```

`fm-format.sh` is just `uv run fm format lint` — Cursor's `afterFileEdit`
is observational, so this keeps the tree formatted but can't push lint
output back into the loop. The feedback channel is the `stop` hook, which
may return a `followup_message` that auto-submits as the next prompt
(Cursor caps the loop at 5 by default) — and this is where `--json` earns
its keep:

```sh
#!/bin/sh
# .cursor/hooks/fm-gate.sh — block "done" on a red gate, with receipts.
out=$(uv run fm --json check) && exit 0
printf '%s' "$out" | jq '{followup_message:
  ("fm check failed — fix these, then finish:\n" +
   ([.results[] | select(.ok | not) | "\(.task): exit \(.code)\n\(.output)"] | join("\n")))}'
```

## Everyone else

The snippet is the portable layer — `AGENTS.md` reaches most agents. For
agents with no hook system (Copilot's coding agent runs in Actions, for
instance), the enforcement layer is the one you already have:
`uv run fm check` in [CI](ci.md) plus branch protection, which catches
every agent and every human identically.
