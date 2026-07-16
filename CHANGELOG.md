# Changelog

All notable changes to footman are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/). While footman is pre-1.0, minor
versions may include breaking changes.

## [Unreleased]

## [0.4.0] — 2026-07-16

### Added

- **Custom-branded CLIs.** A public `App(name, prog, version)` carries your
  project's names and version and threads them through every user-facing string
  (the `--version` banner, the `prog:` error prefix, the completion hint) — so
  you can ship an internal tool under its own name while it stays footman
  underneath. footman's own `fm`/`footman` are now just the default-branded
  `App()`.
- **API reference** on the docs site, generated from docstrings via
  [mkdocstrings](https://mkdocstrings.github.io/).
- **Coverage report** embedded directly in the docs via an inline `<iframe>`,
  regenerated on every deploy.

## [0.3.0] — 2026-07-16

### Added

- **Monorepo task cascade.** Every `tasks.py` from the repo root (the nearest
  `.git`) down to the current directory is merged into one command set: new
  names append, collisions are overridden nearest-wins, and groups merge. Each
  task runs from the folder that defined it.
- **Config discovery.** `[tool.footman]` in `pyproject.toml` and a standalone
  `footman.toml`, walked up to the repo root (nearest wins), plus a
  `--config PATH` override.
- **Per-directory completion cache**, so each folder of a monorepo caches its
  own merged cascade.
- **Documentation site** (Zensical) published to GitHub Pages.

## [0.2.0] — 2026-07-16

### Added

- **Richer type system:** union parameters (validated and coerced by
  specificity), `Many[T]` one-or-many values, opt-in `csv` comma-splitting,
  `dict[K, V]` (including `dict[str, list[...]]`), and custom types via their
  typed constructors.
- **Execution layer:** `run()` (subprocess or in-process callable, capture with
  replay-on-failure, dry-run, `--json` steps), the typed `tools.*` wrappers, and
  opt-in `Context` injection.
- **Parallel-by-default DAG scheduler:** independent tasks run concurrently;
  `pre`/`post` dependencies, the `parallel()` helper, `-s/--sequential`, and
  grouped non-interleaved output.

## [0.1.0] — 2026-07-16

### Added

- Initial release: typed function signatures become CLIs (flags, options,
  positionals, choices), modules become nested command groups, a separator-free
  chain grammar, and instant shell completion answered from a cached JSON
  manifest without importing your code.

## [0.0.2] — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI (MIT license, project
  URLs).

## [0.0.1] — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI.

[Unreleased]: https://github.com/willemkokke/footman/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/willemkokke/footman/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/willemkokke/footman/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/willemkokke/footman/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/willemkokke/footman/releases/tag/v0.1.0
[0.0.2]: https://github.com/willemkokke/footman/releases/tag/v0.0.2
[0.0.1]: https://github.com/willemkokke/footman/releases/tag/v0.0.1
