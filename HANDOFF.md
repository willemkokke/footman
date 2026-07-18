# Handoff — review-fix implementation (`fix/review-findings`)

Picking this up in a fresh session? Read this, then `PLAN.md`, then `git log --oneline main..HEAD`. This is a working note, not a deliverable — delete it when the branch merges.

## What this is

Implementing the 65-finding whole-repo review fix plan in `PLAN.md` (11 phases). Work happens on branch **`fix/review-findings`** (off `main`). Phases **1–5 are done and committed**; **6–11 remain**.

## Progress

| Phase | Findings | Commit | Status |
|-------|----------|--------|--------|
| 1 exit-code contract | F28, F19 | `c5e526a` | ✅ |
| 2 output routing (stderr/reentrancy/cp1252/UTF-8) | F10, F22, F11, F39* | `f225634` | ✅ |
| 3a NaN / positional-only / suggest-only | F31, F26, F27, F34 | `ec25125` | ✅ |
| 3b strict coercion + mixed unions | F01, F21, F24 | `cca46a7` | ✅ |
| 3c dict value markers | F23 | `f7b2859` | ✅ |
| 4 required options / Any / bare collections / guards | F02,F03,F45,F00,F25,F32,F33,F30,F44 | `f566b88` | ✅ |
| 5 scheduler duplicate segments | F09, F59 | `94ec76f` | ✅ |
| 6 context remainder (parallel steps/fail, callable capture/cwd/env, no-color, env tests) | F12, F13, F42, F60, F17, F41, F40 | `9ca27b7`,`b5e79b0`,`3d974ce`,`9a555e4` | ✅ |

**32/65 findings resolved. Coverage steady ~92.3%.** `git log --oneline main..HEAD` is the ledger.

> **\*F39 is only half done.** Phase 2 fixed the `context.py` subprocess decode. The **`tools.py` half** (`Tool.installed_version`'s `subprocess.run` needs `encoding="utf-8", errors="replace"`) is deliberately deferred to **Phase 8.1** (same lines that commit rewrites). Don't forget it.

## Remaining: Phases 7–11 (see PLAN.md for full item specs)

- **7 — app/compose/discovery/config (14), the big one:** do **7.1 first** — the move-only `_app.py` refactor extracting `_execute` + `_run_tree` + `run_group`; then 7.2 (help-first gating), 7.3 (`-C` cwd restore), 7.4 (`-f` no manifest rewrite), 7.6 (empty `--tree`) are small edits *inside* it. 7.5 (Runner KI propagation) needs `run_group`. Then the self-contained compose/discovery/registry/config items (7.7–7.14) can go in any order.
- **8 — tools surface (2):** 8.1 privatize `tools.py` module imports + declare them in `tools.pyi` + **AST parity test** (land before other tools.py edits) + **fold in the deferred F39 tools.py hunk**; 8.2 `recording()` kwarg overrides.
- **9 — completion (7):** F49 `--opt=value`; F61 model value-bearing globals (drift-pin against `split.GLOBALS`); F16 pwsh `--empty-partial`; F46 bash `printf %q`; F47 rc-encoding sniff; F48 rc-file targeting; **9.7 = the new SWR completion-refresh feature (D18)**.
- **10 — docs truth pass + test hygiene (6):** F54/F19 exit-code pins (already unblocked by Phase 1); F04 Many docs + delete the dead `MANY` sentinel (`params.py`); F55 monorepos plugins row; F56 export `capture`/`Runner`/`Result`/`recording`; F64 conftest `registry.capture()`.
- **11 — delights (4):** 11.1 `_did_you_mean` helper wired into all not-found sites (difflib already used at `split.py`'s `_check` — reuse it); 11.2 rich completion descriptions; 11.3 auto-example in `--help`; 11.4 bare `fm` → task list.

Decisions are ruled in **PLAN.md Phase 0 (D1–D18)** — follow them; don't re-litigate.

## Working protocol (this is how the loop stays autonomous)

- **Accept-edits mode is ON** (user toggles shift+tab). File edits don't prompt.
- **Commits:** `git commit --no-gpg-sign` (signing routes through 1Password → would prompt/fail; `--no-gpg-sign` starts with `git commit ` so it matches the allowlist). **Never `git push`.** The user does one signed squash at the end.
- **Never prepend anything to allowlisted commands** — no `git -C <path>`, no `set -o pipefail`, no env-var prefixes, no `cd &&`. Working dir is already the repo. `git switch` is allowlisted (added this session).
- **The gate, per item/phase:** `uv run fm check` (ruff format+check, basedpyright, pytest) then `uv run pytest -q --cov=footman --cov-report=` (enforces `fail_under=92` — `fm check`'s pytest does **not** enforce coverage). Add `uv run --group docs zensical build --clean --strict` **only when docs change**.
- **Commit granularity:** one commit per plan item (or tight phase), message body explaining root cause + fix + findings, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- If an item turns risky, revert just that item and continue; don't stall the run.

## Gotchas learned (will save you a gate cycle)

- **`from __future__ import annotations` in test files** ⇒ annotations are strings evaluated via `eval_str`; **local** classes/functions in an annotation won't resolve (fall back to raw string, markers lost). Use **module-level** helpers: `Colour` enum in `tests/test_binding.py`, `_even` validator in `tests/test_markers.py`. `between(...)`/`suggest(...)` work because they're module-level imports.
- **ruff nits that fail the gate:** line length 88; **RUF043** (regex metachars in `pytest.raises(match=...)` → use a raw string, escape `.` and `|`); **I001** import order. Fix fast with `uv run ruff check --fix src tests` and `uv run ruff format src tests`.
- **Test helpers by file:** `run`/`build_tree` (test_params, test_markers), `_run` (test_binding), `drive` (test_context, test_schedule), `ERROR_CASES` + the `tree` fixture from `conftest.py` (test_split). `specs(fn)` in test_manifest. Branding tests use `Runner(App(...)).invoke(line, cwd=tmp_path)`.
- **Deferred within Phase 3b:** a union of *custom* types with garbage (`UUID | int` given non-UUID, non-int) binds best-effort raw rather than failing cleanly — full strict union-custom rejection was out of scope. Fine to leave; note if you revisit.

## Next action

Start **Phase 7** (the big one). Read `PLAN.md` Phase 7. Do **7.1 first** — the move-only `_app.py` refactor extracting `_execute` + `_run_tree` + `run_group` — then 7.2/7.3/7.4/7.6 are small edits *inside* it; 7.5 needs `run_group`; 7.7–7.14 are self-contained and can go in any order. Implement item-by-item with the gate + `--no-gpg-sign` commit loop above.

Note: the branch is now **pushed** to `origin/fix/review-findings` (CI only runs on PRs, not branch pushes — no PR open yet).
