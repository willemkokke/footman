/* The footman playground: a real footman in the browser via Pyodide, plus
 * the "run it there" links under every python example in the docs.
 *
 * Loaded on every page (extra_javascript, module + defer). The site uses
 * navigation.instant, so init re-runs on each document$ emission; every
 * step is idempotent and guarded so a failure here can never break a page.
 */

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.3/full/pyodide.mjs";
const SITE_ROOT = new URL("..", import.meta.url); // …/assets/ -> site root
const FRAGMENT_MARK = "example: fragment";

const DEFAULT_CODE = `from typing import Literal
from footman import parallel, run, task

@task
def lint(fix: bool = False):
    "Lint the source tree."
    run("ruff check src" + (" --fix" if fix else ""))

@task
def test():
    "Run the test suite."
    run("pytest -q")

@task
def deploy(target: Literal["dev", "staging", "prod"]):
    "Ship to an environment."
    run(f"./rollout.sh {target}")

@task
def check():
    "Lint and test, in parallel."
    parallel(lint, test)
`;

const DEFAULT_ARGS = "--dry-run check";

/* ---------- shared helpers ---------- */

function b64encode(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function b64decode(b64) {
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function onEachPage(fn) {
  const run = () => {
    try {
      fn();
    } catch (err) {
      console.warn("footman playground:", err);
    }
  };
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(run); // instant navigation: fires per page
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
}

/* ---------- "run it there" links ---------- */

function isFragment(block) {
  // The docs mark illustration-only blocks with an HTML comment directly
  // above the fence; markdown passes it through, so honour it here too.
  for (let n = block.previousSibling; n; n = n.previousSibling) {
    if (n.nodeType === Node.TEXT_NODE && !n.textContent.trim()) continue;
    return n.nodeType === Node.COMMENT_NODE && n.textContent.includes(FRAGMENT_MARK);
  }
  return false;
}

function addRunLinks() {
  if (document.getElementById("fm-playground")) return; // not on the playground
  const article = document.querySelector("article");
  if (!article) return;
  const session = []; // the page-as-session prefix, like the docs tests
  for (const block of article.querySelectorAll("div.language-python.highlight")) {
    const codeEl = block.querySelector("code");
    if (!codeEl) continue;
    if (isFragment(block)) continue;
    session.push(codeEl.textContent.replace(/\n$/, ""));
    if (block.dataset.fmpLinked) continue; // idempotent re-init
    block.dataset.fmpLinked = "1";
    const href = new URL("playground/", SITE_ROOT);
    href.hash = "code=" + b64encode(session.join("\n\n") + "\n");
    const wrap = document.createElement("div");
    wrap.className = "fmp-runlink";
    const a = document.createElement("a");
    a.href = href.toString();
    a.textContent = "run it there ↗";
    a.title = "Open this example (and what it builds on) in the playground";
    wrap.appendChild(a);
    block.after(wrap);
  }
}

/* ---------- the playground page ---------- */

const BOOTSTRAP = `
import io, json, sys, traceback
from pathlib import Path

def _fm_invoke(code, line):
    Path("tasks.py").write_text(code, encoding="utf-8")
    try:
        from footman.testing import Runner
        result = Runner().invoke(line, tasks=Path("tasks.py"))
        return json.dumps({
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
    except Exception:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": traceback.format_exc(limit=8),
        })
`;

let pyodideReady = null; // one load per browser tab, kept across instant nav

function loadRuntime(status) {
  if (!pyodideReady) {
    pyodideReady = (async () => {
      status("loading Python — a few seconds, once per visit…");
      const { loadPyodide } = await import(PYODIDE_URL);
      const pyodide = await loadPyodide();
      status("installing footman…");
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install("footman");
      pyodide.runPython(BOOTSTRAP);
      return pyodide;
    })();
    pyodideReady.catch(() => {
      pyodideReady = null; // a failed load may be retried
    });
  }
  return pyodideReady;
}

function initPlayground() {
  const root = document.getElementById("fm-playground");
  if (!root || root.dataset.fmpInit) return;
  root.dataset.fmpInit = "1";

  const code = document.getElementById("fmp-code");
  const args = document.getElementById("fmp-args");
  const runBtn = document.getElementById("fmp-run");
  const out = document.getElementById("fmp-out");
  const status = document.getElementById("fmp-status");
  const setStatus = (text) => {
    status.textContent = text;
  };

  // Prefill from a "run it there" link, or the default example.
  const hash = new URLSearchParams(window.location.hash.slice(1));
  try {
    code.value = hash.has("code") ? b64decode(hash.get("code")) : DEFAULT_CODE;
  } catch {
    code.value = DEFAULT_CODE;
  }
  args.value = hash.get("cmd") || DEFAULT_ARGS;
  runBtn.disabled = false;

  code.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const { selectionStart: s, selectionEnd: e, value } = code;
      code.value = value.slice(0, s) + "    " + value.slice(e);
      code.selectionStart = code.selectionEnd = s + 4;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) run();
  });
  args.addEventListener("keydown", (event) => {
    if (event.key === "Enter") run();
  });
  runBtn.addEventListener("click", run);

  let running = false;
  async function run() {
    if (running) return;
    running = true;
    runBtn.disabled = true;
    try {
      const pyodide = await loadRuntime(setStatus);
      setStatus("running…");
      const invoke = pyodide.globals.get("_fm_invoke");
      const raw = invoke(code.value, args.value);
      invoke.destroy?.();
      const result = JSON.parse(raw);
      out.textContent =
        (result.stdout || "") +
        (result.stderr ? (result.stdout ? "\n" : "") + result.stderr : "");
      if (!out.textContent.trim()) out.textContent = "(no output)";
      setStatus(`exit code ${result.exit_code}`);
      out.classList.toggle("fmp-failed", result.exit_code !== 0);
    } catch (err) {
      out.textContent = String(err);
      setStatus("the runtime failed to load — check your connection and retry");
    } finally {
      running = false;
      runBtn.disabled = false;
    }
  }
}

onEachPage(() => {
  addRunLinks();
  initPlayground();
});
