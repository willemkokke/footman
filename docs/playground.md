---
hide:
  - navigation
  - toc
---

# Playground

A real footman, in your browser. The editor below is a `tasks.py`; the
prompt is `fm`. Python (and footman itself) load into the page via
[Pyodide](https://pyodide.org) on first use — nothing is installed on your
machine, and nothing you type leaves it.

The browser sandbox, said plainly: it has no processes and one thread, so
every `run("…")` child is **simulated** — it succeeds and its output says
`[simulated]` — and runs are sequential. Everything else is the real
thing: parsing, eager validation, taught errors, scheduling, `--json`,
`--dry-run` plans — and **`fm test` runs the real pytest**, in-process
through the tools bridge, right here in the page. The prompt completes
too: press <kbd>Tab</kbd> and the candidates come from the same manifest
walk a shell hook consults, rebuilt from whatever the editor says.

Press **Run**. The gate fails — one of the tests is wrong on purpose.
Read pytest's diff, fix `fizzbuzz` (or the test), and run it green. Then
try `-k check audit` to watch keep-going collect every failure, and
`deploy produ` to read a taught error. Completion knows the grammar,
not just the words: type `deploy prod --regions=eu,` and press
<kbd>Tab</kbd> to watch a comma-separated list complete item by item.

<div id="fm-playground" markdown="0">
  <div class="fmp-pane fmp-editor-pane">
    <div class="fmp-label" role="tablist">
      <button class="fmp-tab" role="tab" data-file="tasks.py">tasks.py</button>
      <button class="fmp-tab" role="tab" data-file="test_demo.py">test_demo.py</button>
    </div>
    <textarea id="fmp-code" spellcheck="false" autocomplete="off"
      autocapitalize="off" aria-label="editor"></textarea>
  </div>
  <div class="fmp-pane fmp-output-pane">
    <div class="fmp-toolbar">
      <span class="fmp-prompt">fm</span>
      <input id="fmp-args" spellcheck="false" autocomplete="off"
        autocapitalize="off" aria-label="fm command line" />
      <button id="fmp-run" disabled>Run</button>
    </div>
    <div id="fmp-complete" hidden></div>
    <pre id="fmp-out" aria-live="polite"></pre>
    <div class="fmp-status" id="fmp-status">Python loads when you first run —
      a few seconds and ~15&nbsp;MB, once per visit.</div>
  </div>
</div>

Every Python example in these docs has a **run it there** link that brings
the page's code straight into this editor, the prompt already holding a
command that runs against it — the examples are tested against every
commit, so what you paste is what runs.
