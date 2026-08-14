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

import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
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
import { tags as t } from "@lezer/highlight";

/* codemirror's basicSetup minus autocompletion: the stock completer
 * offers every keyword, builtin, and buffer word — noise, not help.
 * Real completion is planned to come from the interpreter that is
 * already in the page (footman and toolroom importable, docstrings and
 * all); until then the editor simply doesn't guess. */
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

export { python } from "@codemirror/lang-python";
export { EditorView } from "@codemirror/view";
