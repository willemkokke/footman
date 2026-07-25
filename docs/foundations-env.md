# The environment

!!! question "Already know this?"
    1. When a child process starts, whose environment does it get — and
       does it see the parent's *later* changes?
    2. Is `os.environ` shared between threads?
    3. What does `os.putenv` *not* do that `os.environ["X"] = …` does?

    All three easy? Skip to [The shell](foundations-shell.md).

## The concept

The **environment** is a small string-to-string map every process carries:
`PATH`, `HOME`, your `API_KEY`. Its defining property is *how it moves*:
a child process receives a **copy at spawn**, and from that moment the two
maps are independent — the parent's later edits never reach a running
child, and nothing a child sets ever flows back up. Environment flows
*down*, once, at spawn.

Inside one process, though, there is just the one map, shared by every
thread — `os.environ` is a process global like the cwd. And it has a trap
of its own: `os.putenv` writes the *C-level* environment without updating
`os.environ`, so a program can end up with two views of its own env that
disagree. Plain Python advice, footman aside: assign through `os.environ`,
never `putenv`.

## Why it matters to a task runner

Two parallel tasks writing `os.environ` race exactly like two threads
writing any shared dict — last write wins, at a time nobody chose. And
before this design, footman had a subtler problem: a tool run **in-process**
read the live `os.environ`, while the **subprocess** form of the very same
call received a constructed `env=` — two lanes of one tool call reading
*different worlds*. Correct code could break by switching lanes.

## What footman does about it

For the run's duration, `os.environ` goes through footman's **environment
router** — the same move as its stdout router, applied to a second global:

- **Reads** see the environment as it was at run start, plus *this task's*
  own overlay — exactly what the subprocess lane would inject. The two
  lanes finally read one world.
- **Writes scope to the task.** `os.environ["API_KEY"] = "…"` is visible to
  this task's reads and to every child it spawns, and invisible to
  siblings. A one-time note names the deliberate spellings: `env=` for one
  call, `ctx.env` for the task.
- **Deletes are a taught error** — a scoped overlay is additive; spawn the
  child with an explicit `env=` that omits the variable, or mark the task
  serial.
- **`os.putenv`/`os.unsetenv` are taught errors** — they bypass
  `os.environ` even in plain Python, so nothing could scope them.

Outside a run, `os.environ` behaves exactly as stock Python. And because
environment flows down at spawn, everything composes: a scoped write made
with plain `os.environ` rides into a raw `subprocess.Popen` child just as
it would into `run()` — the child's copy is cut from the task's world, not
the process's.

## The one rule

**Environment flows down at spawn — say what a child should see with
`env=` or `ctx.env`, and never expect a write to travel sideways.**
