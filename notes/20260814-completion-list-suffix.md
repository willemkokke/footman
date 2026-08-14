# Completion suffix for comma-continuable values — investigation brief

OPEN — nothing built. Handoff for a session to design and (if ruled in)
build the lane. Willem's constraint, verbatim in spirit: **no
playground-only behaviour** — the feature exists only if at least one
real shell delivers it; the playground then mirrors the same signal.

## The itch

Completing an element of a comma-splitting value ends the token:
`--regions=e<Tab>` → `--regions=eu ` (trailing space). To continue the
list you must delete the space and type the comma. Every shell hook
behaves this way today, and so does the playground prompt. The
completer itself already *knows* the value can continue — mid-list it
filters items already typed (`_choice_tokens`, the `given` filter) and
answers with the remainder.

A page-only fix was built and **reverted** (2026-08-14, this session):
the playground probed the completer with `candidate + ","` — non-empty
answer means continuable — and suppressed its inserted space. Exact
semantics, one probe per menu, ~15 lines. Rejected because it
flattered the playground with behaviour no shell delivers. The probe
idea itself is sound and may return as the *playground's consumer* of
the real signal — or the page can simply read the same protocol marker
the shells will.

## Where everything lives

- `src/footman/_complete.py` — the stdlib-only hot path. Precedents for
  side-channel answers: `_FILES` sentinel + exit 100 (path value →
  shell's file completion), `_FILES_CSV` + exit 101 (comma-splitting
  path mid-list), `_DYNAMIC` sentinel (recompute via `_suggest`).
  `_csv_head()` splits a partial at its last comma; `_choice_tokens()` /
  `_attached_value()` build `head+choice` whole-token candidates and
  filter `given` items. THE fact this feature rides on: the completer
  can tell, at answer time, that candidates are elements of a
  comma-splitting value with items remaining.
- `src/footman/_shellcomp.py` — the five shell hooks. bash already
  conditionally sets `compopt -o nospace` (directory-continuation
  cases, exit 100/101 branches); that is the per-reply mechanism this
  feature would use on bash.
- Playground consumer: `docs/assets/playground.js`, `insertCompletion`
  (the `glue` decision) and `_fm_complete` in the BOOTSTRAP template
  literal (no backticks / `${` in that literal — a guard test enforces
  it).

## Per-shell mechanisms (to verify, not trusted)

- **zsh**: the star. `compadd -q -S ','` appends the comma as an
  auto-removable suffix — accept `eu`, get `--regions=eu,` with the
  comma vanishing on space/Enter. Strictly better UX than no-space.
  Check how the zsh hook currently feeds candidates (compadd vs
  _describe) and whether the suffix can be applied per-reply.
- **bash**: `compopt -o nospace` — per-reply, not per-candidate. Fine:
  a value menu is uniform (all candidates are elements of one option).
  Cost: after a *final* element the user has no space either; zsh's
  removable suffix avoids this, bash cannot. Decide whether that trade
  is acceptable or whether bash keeps today's behaviour.
- **fish**: no per-candidate suffix control known; fish itself decides
  spacing. Likely unchanged. Verify.
- **pwsh / nushell**: their completers return structured results;
  investigate whether a completion can suppress the trailing space
  (pwsh `CompletionResult` — probably not; nushell custom completers —
  possibly via `options.completion_algorithm`/record fields). Likely
  unchanged at first.

"At least one actual shell" = zsh (+ bash if the trade reads well).

## Protocol sketch (a starting point, not a decision)

The hot path's answer for an attached comma-splitting value with
remaining items grows a marker the hooks can read — options:

1. A new sentinel line before the candidates (like `_FILES_CSV`), or
2. a new exit code (like 100/101), or
3. a trailing-comma variant riding each candidate (zsh could use
   `-S ''` + candidates that end `,` — but that pollutes bash's
   display and the playground's menu; probably wrong).

(1) or (2) keep candidates clean and let each hook apply its native
mechanism. The playground reads the same marker for its `glue`.

## Constraints and tests

- The hot path stays stdlib-only and import-free of the framework; a
  TAB is one file read + JSON parse + tree walk. No new imports.
- `tests/test_shellcomp.py` drives real bash/zsh/fish/nushell/pwsh
  (skip-if-absent locally; CI installs all — `FOOTMAN_REQUIRE_SHELLS`
  makes a missing shell a hard failure). New behaviour needs functional
  coverage there, per shell that adopts it.
- `tests/test_complete.py` / the `tree` fixture + `ERROR_CASES`
  conventions for hot-path unit tests.
- Measured grammar facts that bound the design (memory
  `completion-grammar-facts`): values are always `=`-attached; bare
  `--opt` is always legal (no-default params are positionals); bools
  reject `=`; dynamic `suggest()` completers never bake into the
  manifest (they answer via `_suggest` respawn — decide whether dynamic
  list values also carry the marker, or first ship choices-only).
- The playground gallery may only demonstrate RELEASED behaviour
  (note `20260814-playground-gallery.md`, "the released-wheel gap") —
  the Completion example advertises the suffix only after the release
  that carries it.

## Related, decided elsewhere

- Duplicates in a `list` value are by design (a list holds what you
  put in it; `set[Literal[…]]` folds them — the playground's Completion
  example now uses `set` for exactly this reason). Completion's
  `given` filter deliberately narrows *suggestions*, never validity —
  "a completion filter that quietly became validation" is a recorded
  anti-pattern (`params.py`, matching()'s docstring). The suffix marker
  must follow the same rule: presentation, never grammar.
