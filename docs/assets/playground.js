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

_fm_manifest = {"code": None, "tree": None}

def _fm_complete(code, line):
    # The real completion hot path over the editor's tasks.py: build the
    # manifest tree once per source text, then every Tab is a pure walk —
    # the same complete() a shell hook consults.
    import types
    from footman import manifest, registry
    from footman._complete import complete
    if _fm_manifest["code"] != code:
        module = types.ModuleType("tasks")
        sys.modules["tasks"] = module
        try:
            with registry.capture() as root:
                exec(compile(code, "tasks.py", "exec"), module.__dict__)
            _fm_manifest["tree"] = manifest.build_manifest(root)["tree"]
            _fm_manifest["code"] = code
        except Exception:
            return json.dumps([])
        finally:
            sys.modules.pop("tasks", None)
    words = line.split()
    if not line or line.endswith(" "):
        words.append("")
    candidates = [
        c for c in complete(_fm_manifest["tree"], words)
        if not c.startswith(chr(0))  # file/dynamic sentinels need a real shell
    ]
    return json.dumps(candidates)
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
    if (event.key === "Tab") {
      event.preventDefault();
      completeArgs();
    }
    if (event.key === "Escape") hideCandidates();
  });
  args.addEventListener("input", hideCandidates);
  runBtn.addEventListener("click", run);

  /* Tab completion: the same manifest completer a shell hook consults,
   * over whatever the editor currently says. */

  const strip = document.getElementById("fmp-complete");

  function hideCandidates() {
    strip.hidden = true;
    strip.replaceChildren();
  }

  function insertCompletion(name) {
    const cursor = args.selectionStart ?? args.value.length;
    const before = args.value.slice(0, cursor);
    const after = args.value.slice(cursor);
    const partial = before.match(/\S*$/)[0];
    const glue = name.endsWith("=") ? "" : " ";
    const head = before.slice(0, before.length - partial.length) + name + glue;
    args.value = head + after;
    args.selectionStart = args.selectionEnd = head.length;
    args.focus();
  }

  function commonPrefix(names) {
    let prefix = names[0] ?? "";
    for (const name of names) {
      while (!name.startsWith(prefix)) prefix = prefix.slice(0, -1);
    }
    return prefix;
  }

  async function completeArgs() {
    try {
      const pyodide = await loadRuntime(setStatus);
      setStatus("ready");
      const fn = pyodide.globals.get("_fm_complete");
      const cursor = args.selectionStart ?? args.value.length;
      const raw = fn(code.value, args.value.slice(0, cursor));
      fn.destroy?.();
      const candidates = JSON.parse(raw);
      const names = candidates.map((c) => c.split("\t")[0]);
      hideCandidates();
      if (!names.length) return;
      if (names.length === 1) {
        insertCompletion(names[0]);
        return;
      }
      const partial = args.value.slice(0, cursor).match(/\S*$/)[0];
      const prefix = commonPrefix(names);
      if (prefix.length > partial.length) {
        const cut = prefix.length; // keep the menu; extend what's typed
        const before = args.value.slice(0, cursor);
        args.value =
          before.slice(0, before.length - partial.length) +
          prefix +
          args.value.slice(cursor);
        args.selectionStart = args.selectionEnd =
          before.length - partial.length + cut;
      }
      for (const candidate of candidates) {
        const [name, summary] = candidate.split("\t");
        const button = document.createElement("button");
        button.type = "button";
        const strong = document.createElement("strong");
        strong.textContent = name;
        button.appendChild(strong);
        if (summary) {
          const dim = document.createElement("span");
          dim.textContent = summary;
          button.appendChild(dim);
        }
        // mousedown, not click: the input keeps focus and the strip stays.
        button.addEventListener("mousedown", (event) => {
          event.preventDefault();
          insertCompletion(name);
          hideCandidates();
        });
        strip.appendChild(button);
      }
      strip.hidden = false;
    } catch (err) {
      console.warn("footman playground completion:", err);
    }
  }

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
