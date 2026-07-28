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

One honest limitation: a browser can't spawn subprocesses, so a task that
executes `run("…")` for real stops at the boundary a terminal would cross.
Everything up to that boundary is the real thing — parsing, validation,
taught errors, help, completion metadata, `--json`, and `--dry-run` plans.
Try `--list`, then `--dry-run check`, then `deploy produ` and read the
error. That last one is the pitch.

<div id="fm-playground" markdown="0">
  <div class="fmp-pane fmp-editor-pane">
    <div class="fmp-label">tasks.py</div>
    <textarea id="fmp-code" spellcheck="false" autocomplete="off"
      autocapitalize="off" aria-label="tasks.py source"></textarea>
  </div>
  <div class="fmp-pane fmp-output-pane">
    <div class="fmp-toolbar">
      <span class="fmp-prompt">fm</span>
      <input id="fmp-args" spellcheck="false" autocomplete="off"
        autocapitalize="off" aria-label="fm command line" />
      <button id="fmp-run" disabled>Run</button>
    </div>
    <pre id="fmp-out" aria-live="polite"></pre>
    <div class="fmp-status" id="fmp-status">Python loads when you first run —
      a few seconds and ~15&nbsp;MB, once per visit.</div>
  </div>
</div>

Every Python example in these docs has a **run it there** link that brings
the page's code straight into this editor — the examples are tested against
every commit, so what you paste is what runs.
