# The playground's editor bundle

`docs/assets/vendor/codemirror.js` is CodeMirror 6 — the editor the
playground upgrades its textarea into — bundled from the pinned versions
in `package.json` here (repo `vendor/codemirror/`) and committed, because the site has no build step
for its assets and the CDN route measurably fails: jsdelivr's `+esm`
builds give `codemirror`, `@codemirror/lang-python`, and the theme each
their own `@codemirror/state` instance, and CodeMirror rejects extensions
whose `instanceof` checks cross instances. One bundle is one instance set
by construction — and the site stops depending on a second CDN at runtime.

To rebuild (bump the pins first if that is the point):

```sh
npm install
npm run build
```

Commit the regenerated `docs/assets/vendor/codemirror.js` together with
the pin change. `node_modules/` stays untracked; `package-lock.json` is
tracked so the build reproduces.

The build also copies `docs/assets/vendor/FiraCode-VF.woff2` — the
editor's and the terminal pane's typeface, ligatures included — from the
`firacode` package.

Licences: CodeMirror is MIT (© Marijn Haverbeke and contributors,
https://codemirror.net); the VS Code highlight theme
(`@uiw/codemirror-theme-vscode`) is MIT; Fira Code is OFL-1.1
(© The Fira Code Project Authors). The bundle carries a banner naming
the MIT parts.
