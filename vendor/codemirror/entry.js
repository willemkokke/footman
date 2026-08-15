/* Everything the playground imports from CodeMirror, re-exported from ONE
 * module graph. Bundling is not an optimisation here — it is the fix:
 * loading codemirror and lang-python as separate CDN builds gave each its
 * own @codemirror/state instance, and CM refuses extensions whose
 * instanceof checks cross instances ("Unrecognized extension value in
 * extension set"). One bundle, one instance set, by construction.
 *
 * The theme is defined in terms of the site's own --md-code-hl-* CSS
 * variables — the very ones zensical's Pygments blocks are styled with —
 * so the editor and every docs code block share one palette by
 * construction, follow the light/dark toggle live, and re-skin together
 * when the variables change. The tag → variable mapping below mirrors
 * zensical's Pygments-class → variable mapping (.k → keyword, .nd → the
 * decorator @ → keyword, .nf/.nc → function, .kc → name, …). */

import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
} from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  HighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { EditorState } from "@codemirror/state";
import {
  crosshairCursor,
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from "@codemirror/view";
import { pythonLanguage } from "@codemirror/lang-python";
import { highlightCode, tags as t } from "@lezer/highlight";

/* codemirror's basicSetup minus autocompletion: the stock completer
 * offers every keyword, builtin, and buffer word — noise, not help.
 * The playground wires autocompletion() back in with an override source
 * that asks the interpreter in the page (footman and toolroom
 * importable, docstrings and all); the pieces are re-exported below. */
export const footmanSetup = [
  lineNumbers(),
  highlightActiveLineGutter(),
  highlightSpecialChars(),
  history(),
  foldGutter(),
  drawSelection(),
  dropCursor(),
  EditorState.allowMultipleSelections.of(true),
  indentOnInput(),
  bracketMatching(),
  closeBrackets(),
  rectangularSelection(),
  crosshairCursor(),
  highlightActiveLine(),
  highlightSelectionMatches(),
  keymap.of([
    ...closeBracketsKeymap,
    ...defaultKeymap,
    ...searchKeymap,
    ...historyKeymap,
    ...foldKeymap,
  ]),
];

const chrome = EditorView.theme({
  "&": {
    backgroundColor: "var(--md-code-bg-color)",
    color: "var(--md-code-fg-color)",
  },
  ".cm-content": { caretColor: "var(--md-code-fg-color)" },
  ".cm-cursor, .cm-dropCursor": {
    borderLeftColor: "var(--md-code-fg-color)",
  },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground":
    { backgroundColor: "var(--md-default-fg-color--lightest)" },
  ".cm-activeLine": {
    backgroundColor: "var(--md-code-hl-color--light, transparent)",
  },
  ".cm-gutters": {
    backgroundColor: "var(--md-code-bg-color)",
    color: "var(--md-default-fg-color--light)",
    border: "none",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "var(--md-code-hl-color--light, transparent)",
  },
  // The completion and hover tooltips, in the site's own variables —
  // CM's stock tooltip styling is light-mode-only and never matched the
  // page (Willem: "no concept of dark mode perhaps?").
  ".cm-tooltip": {
    backgroundColor: "var(--md-code-bg-color)",
    color: "var(--md-code-fg-color)",
    border: "0.05rem solid var(--md-default-fg-color--lightest)",
    borderRadius: "0.2rem",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul": {
    fontFamily: '"Fira Code", var(--md-code-font-family)',
    fontSize: "0.64rem",
    maxHeight: "14em",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--md-default-fg-color--lightest)",
    color: "var(--md-code-fg-color)",
  },
  ".cm-completionMatchedText": {
    textDecoration: "none",
    fontWeight: "700",
    color: "var(--md-accent-fg-color)",
  },
  ".cm-tooltip.cm-completionInfo": {
    backgroundColor: "var(--md-code-bg-color)",
    border: "0.05rem solid var(--md-default-fg-color--lightest)",
    color: "var(--md-default-fg-color--light)",
    fontSize: "0.64rem",
    maxWidth: "28rem",
    whiteSpace: "pre-wrap",
  },
  ".fmp-signature": {
    padding: "0.4rem 0.6rem",
    fontFamily: '"Fira Code", var(--md-code-font-family)',
    fontSize: "0.64rem",
    minWidth: "min(26rem, 80vw)",
    maxWidth: "min(42rem, 90vw)",
    maxHeight: "min(50vh, 22rem)",
    overflow: "auto",
  },
  // One block per signature line: a soft-wrapped long parameter hangs
  // at its own indent instead of snapping back to column zero.
  ".fmp-sig-line": {
    whiteSpace: "pre-wrap",
    paddingLeft: "8ch",
    textIndent: "-8ch",
  },
  ".fmp-signature-doc": {
    color: "var(--md-default-fg-color--light)",
    fontFamily: "var(--md-text-font-family, inherit)",
    marginTop: "0.2rem",
  },
});

const colours = HighlightStyle.define([
  {
    // Pygments .k/.kn/.kd — and .nd: the decorator's @ (tags.meta here).
    tag: [
      t.keyword,
      t.controlKeyword,
      t.operatorKeyword,
      t.definitionKeyword,
      t.moduleKeyword,
      t.modifier,
      t.meta,
    ],
    color: "var(--md-code-hl-keyword-color)",
  },
  {
    tag: [t.string, t.special(t.string), t.escape],
    color: "var(--md-code-hl-string-color)",
  },
  { tag: t.number, color: "var(--md-code-hl-number-color)" },
  { tag: t.lineComment, color: "var(--md-code-hl-comment-color)" },
  {
    // .nf/.nc/.nn: names being defined or called.
    tag: [
      t.function(t.variableName),
      t.function(t.definition(t.variableName)),
      t.function(t.propertyName),
      t.definition(t.className),
    ],
    color: "var(--md-code-hl-function-color)",
  },
  {
    // .kc: True/False/None sit with plain names in zensical's mapping.
    tag: [t.bool, t.null, t.variableName, t.propertyName],
    color: "var(--md-code-hl-name-color)",
  },
  {
    tag: [
      t.updateOperator,
      t.arithmeticOperator,
      t.bitwiseOperator,
      t.compareOperator,
      t.definitionOperator,
      t.derefOperator,
    ],
    color: "var(--md-code-hl-operator-color)",
  },
  {
    tag: [t.punctuation, t.paren, t.squareBracket, t.brace, t.separator],
    color: "var(--md-code-hl-punctuation-color)",
  },
]);

export const footmanTheme = [chrome, syntaxHighlighting(colours)];

/* Python source → highlighted DOM, through the SAME HighlightStyle the
 * editor uses — for tooltip content (hover signatures) that should read
 * like the code beside it. Lezer's parser is error-tolerant, so a bare
 * signature line highlights fine without being a whole valid module. */
export function highlightPython(code) {
  const frag = document.createDocumentFragment();
  const tree = pythonLanguage.parser.parse(code);
  highlightCode(
    code,
    tree,
    colours,
    (text, classes) => {
      if (classes) {
        const span = document.createElement("span");
        span.className = classes;
        span.textContent = text;
        frag.appendChild(span);
      } else {
        frag.appendChild(document.createTextNode(text));
      }
    },
    () => {
      frag.appendChild(document.createTextNode("\n"));
    },
  );
  return frag;
}

export { autocompletion, completionKeymap } from "@codemirror/autocomplete";
export { python } from "@codemirror/lang-python";
export { EditorView, hoverTooltip, keymap, tooltips } from "@codemirror/view";
