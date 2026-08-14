/* Everything the playground imports from CodeMirror, re-exported from ONE
 * module graph. Bundling is not an optimisation here — it is the fix:
 * loading codemirror, lang-python, and the theme as separate CDN builds
 * gave each its own @codemirror/state instance, and CM refuses extensions
 * whose instanceof checks cross instances ("Unrecognized extension value
 * in extension set"). One bundle, one instance set, by construction. */

export { python } from "@codemirror/lang-python";
export { EditorView } from "@codemirror/view";
export { vscodeDark, vscodeLight } from "@uiw/codemirror-theme-vscode";
export { basicSetup } from "codemirror";
