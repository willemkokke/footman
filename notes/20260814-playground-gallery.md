# The playground gallery

The playground works and is honest about its sandbox, but it is a demo:
one hardcoded sample, a plain textarea, an output pane that forgets the
previous run. This note plans the walk from demo to gallery — a curated,
categorised set of examples covering every feature footman has, each with
command lines that make its point, all tested against every commit like
the rest of the docs.

Decided with Willem 2026-08-14. Phases land separately; the note is law
for scope and vocabulary until it says otherwise.

## The shape

An **examples registry** — `docs/assets/examples.json` — of self-contained
entries:

```json
{
  "id": "validation/checks",
  "title": "Belt-and-braces deploy",
  "category": "Validation",
  "blurb": "Markers stack; each refusal is a taught error.",
  "files": {"tasks.py": "…"},
  "commands": [
    {"line": "deploy config.toml 1.2.3", "note": "everything passes"},
    {"line": "deploy config.toml nope", "note": "check(fn) refuses"}
  ]
}
```

The page renders a grouped dropdown; selecting an example swaps the editor
tabs and shows its command lines as clickable chips under the prompt, each
chip carrying its note. No navigation, so the loaded Pyodide instance is
reused. JSON, not a JS module, because two consumers read it: the page
(fetch, same-origin) and pytest (`json.loads`, no regex extraction).

"Run it there" fragment links coexist: a `#code=` fragment shows as a
"from the docs" pseudo-entry; gallery entries are linkable as
`#example=<id>` so docs pages can deep-link into them.

**Testing is the point, not an afterthought**: the `_FM_PLAYGROUND_SIM`
rehearsal harness drives every entry's every command line through the
shipped driver in CPython, asserting exit codes and output markers. A
**coverage guard** maps a feature checklist to example ids and fails when
a feature has no example — "cover everything" enforced, not aspirational.

## Feature categories to cover

1. **Basics** — tasks, groups, `@group.default`, docstrings → `--list` /
   `--help` / `--describe`, dotted addressing.
2. **Typing** — int/bool/Path/enum/Literal, unions, comma-split lists,
   dict params, `Arg[T]`.
3. **Validation** — `between`, `check(fn)` + siblings, `env()`,
   `default(fn)`, `doc()`, path requirements (blurb discloses the sandbox
   passes them).
4. **Variadic & forwarding** — `*args`, `--` passthrough, `forward` /
   `Forward[T]`.
5. **Scheduling** — pre/post, chains, `-k` keep-going, `.opts()`,
   fail-fast.
6. **Run & tools** — `run()`, toolroom wrappers, `--dry-run`, `fail()`,
   exit codes, `shell=True` (needs the shell-resolution dummy below).
7. **Structured results** — returned values, `Stdout[T]`, `--json`,
   stdin binding (needs the stdin tab below).
8. **Config** — a `footman.toml` tab showing the cascade.
9. **Composition** — `include()` across two file tabs.
10. **Completion** — static choices/lists in-page; dynamic `suggest()`
    once the spike below lands.

## Sandbox extensions

- **Shell resolution dummy** (like the path-requirement dummy): the
  simulated child never executes, so `_resolve_shell` can be patched to a
  fixed `["/bin/sh", "-c"]` under emscripten and `shell=True` examples
  run with `[simulated]` output.
- **stdin as a file tab** (Willem: yes): a `stdin` tab, prepopulated with
  JSON payloads by examples that bind from the boundary; `_fm_invoke`
  passes its content as the invocation's stdin. Empty tab = no pipe.
- **Dynamic completion** (Willem: make it work): in a real shell the
  manifest bakes a sentinel and `_suggest.py` respawns to run the
  completer fresh; in the page the interpreter holding the tasks is right
  there, so Tab can run the `suggest()` function in-process instead of
  refusing on the sentinel. SPIKE: read `_suggest`'s protocol and mirror
  its semantics (fresh values per Tab, no baking) without the child.
- **ask() prompts** (Willem: make them usable): Pyodide calls JS
  synchronously, so the prompt seam can route to the browser
  (`js.prompt()` first; an in-page modal needs async and can come later).
  SPIKE: find the seam `_prompt_param` reads through, and whatever the
  Runner's interactive guard needs to consider the page a terminal.

Out of scope, disclosed on the page: PEP 723 re-exec (no processes),
`fetch()` (no cross-origin network), the profile plugin.

## Polish (phase 1, no content changes)

- **CodeMirror 6**, vendored as one bundle
  (`docs/assets/vendor/codemirror.js`, recipe in `vendor/codemirror/`),
  dynamically imported only on the playground page so the module-eval
  guard test and every other docs page stay untouched. Fallback on load
  failure: the plain textarea, exactly as today. MEASURED: the CDN route
  (three jsdelivr `+esm` entry points, pinned) fails — each got its own
  `@codemirror/state` instance and CM rejected the extension set
  ("Unrecognized extension value… multiple instances of
  @codemirror/state"). One esbuild bundle is one instance set by
  construction, and drops the second runtime CDN.
- **ANSI-coloured output**: the sandbox injects `--color=always` (like it
  injects `-s`, emscripten-only), and the page renders SGR codes to
  spans. The pane stops looking like a log file.
- **Session transcript**: each run appends `fm <line>` + output instead
  of replacing the pane — a real session with scrollback and a Clear
  button.
- **Dynamic tab bar** generated from the example's files (was hardcoded
  to two).
- Per-visit memory of edits per example; a reset-example affordance;
  a share link encoding files+command into the fragment (the b64
  machinery exists).

## Phases

1. **Mechanics** — CM6, dynamic tabs, ANSI pane, transcript.
2. **Gallery** — registry, dropdown, chips, fragment interplay,
   rehearsal + coverage-guard tests.
3. **Content & sandbox** — the ten categories; shell dummy; stdin tab;
   the two spikes (dynamic completion, ask()).
4. **Stretch** — branded-prompt example (`App(...)` renames the prompt),
   monorepo cascade tabs, simulated `fetch()`.

## Decided

- CodeMirror over a hand-rolled textarea-overlay highlighter: an editor
  has fiddly failure modes (scroll sync, iOS composition) already solved
  upstream, and the page already accepts one pinned CDN runtime.
- Registry is JSON in `docs/assets/`, dual-read by page and pytest.
- ANSI colour and the transcript are core, not garnish.
- stdin arrives as a tab, not a separate widget, so examples can ship
  prepopulated payloads.

## Open

- Whether `#example=` deep links should replace some "run it there"
  fragment links in docs pages, or the two spellings live side by side
  indefinitely.
- Whether the Pyodide instance truly survives instant navigation from a
  docs page (module-level `pyodideReady` says it should; verify).
