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

CodeMirror is MIT-licensed (© Marijn Haverbeke and contributors,
https://codemirror.net). The bundle carries a banner saying so.
