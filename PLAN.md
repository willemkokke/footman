# Fix plan: whole-repo review findings

Generated 2026-07-18 from a multi-agent review (65 verified findings: 23 major,
42 minor — every one reproduced or confirmed against source, none refuted) and
a per-workstream fix-design pass with a cross-workstream conflict audit.

How to use this file: phases are ordered by dependency; items inside a phase
are ordered but mostly independent. Each item names its findings (F##), files,
size (S <30 min, M ~1 h, L design work), the fix mechanism, and the regression
tests to add. Every commit must pass the gate:

```sh
uv run fm check && uv run pytest -q --cov=footman --cov-report= && uv run --group docs zensical build --clean --strict
```

Phases 5, 7, 8, and 9 are independent of phases 3–4 and can proceed in
parallel once phases 1–2 land.

---

## Phase 0 — Decisions

The plan assumes the recommended answers below. Overriding one changes the
items it names.

| # | Question | Recommendation (assumed) |
|---|----------|--------------------------|
| D1 | Exit-code contract: honor explicit nonzero codes in `_result`, propagate `run()` command codes via `RunFailed`, bind refusals exit 2? | **Yes to all three** — docs/ci.md and troubleshooting.md are the contract; `executor.py:216` already passes 2 (dead today); no test pins the opposite. Items 1.1, 10.1. |
| D2 | No-default `dict` param: what does it mean? | **Always an option, with a `required: true` manifest key enforced by the splitter** — matches both doc claims; the old behavior was 100 % broken so nothing regresses. Items 4.1, 4.2. |
| D3 | No-default scalar `bool`? | **Required flag** — user must pass `--x` or `--no-x`; rides the required-option mechanism. Item 4.2. |
| D4 | Positional-only params (`def f(a, /)`) | **Support them in `bind()`** — legal Python accepted by every other layer; today it fails 100 % of the time. Item 3.4. |
| D5 | Duplicate explicit chain segments (`fm build web build api`) | **Independent nodes** (concurrent under parallel, ordered under `-s`) + one doc line. Item 5.1. |
| D6 | `--no-color`/`NO_COLOR`/`TERM=dumb` and the live progress line | **Absent entirely** (same output as piped mode); amend the one doc sentence. Item 6.4. |
| D7 | Should `--version` also yield to `--help`? | **No** — the invariant is about side effects; gate only `--install-completion`. Item 7.2. |
| D8 | Cascade helper-import isolation depth | **Move-to-front + direct-sibling eviction, leave sys.path entries** — full isolation would break deferred `import helpers` in single-dir projects. Document the multi-dir deferred case as a limitation. Item 7.8. |
| D9 | `requires=` dotted names | **Keep the dotted `find_spec` check, broaden the catch to `Exception`**; fix the two overpromising docstrings. Item 7.11. |
| D10 | bash completion escaping | **`printf %q` per candidate**, not `-o filenames` (which appends `/` to candidates naming real dirs). Item 9.4. |
| D11 | rc-file targeting for install | **Honor `$ZDOTDIR` for zsh; on macOS write `.bashrc` + the first existing login profile** (never create `.bash_profile` over an existing `.profile`). Item 9.6. |
| D12 | `tools.run` / `tools.sys` etc. | **Privatize the module imports** so they become Tools via `__getattr__`, matching the shipped stub. Item 8.1. |
| D13 | `Runner.invoke` KeyboardInterrupt | **Propagate on both paths** (docstring is the contract); real CLI keeps 130. Item 7.5. |
| D14 | `Many[T]` scalar-collapse | **Fix the docs** — `tests/test_params.py:139` pins always-a-list, and `Many = list` under `TYPE_CHECKING` makes a runtime scalar statically unsound. The `MANY` sentinel is dead code; remove it. Item 10.2. |
| D15 | api.md importability | **Export `capture`, `Runner`, `Result`, `recording` lazily** from `footman/__init__.py`. Item 10.5. |
| D16 | After 1.1 + 6.2 both land: a task whose `parallel()` thunk fails with code N exits N? | **Yes** — falls out of the two fixes; pin with one test when the second lands. |
| D17 | Encoding policy (ruled 2026-07-18) | **Subprocess decode: UTF-8 by default, per-call `run(..., encoding=)` override (`None` = locale behavior). fm's own stdout/stderr: not configurable for now** — it's the contract with whoever spawned fm; revisit only if a real need appears. Item 2.2. |
| D18 | Completion freshness refresh (feature) — refresh mode and default max-age | **Background (stale-while-revalidate): return cached JSON instantly, spawn a detached rebuild when the manifest is older than `max_age`. Trigger on manifest mtime. Default `max_age` ≈ 10 min; `[tool.footman] completion.max_age`, `off`/`0` disables.** Item 9.7. Alternative (inline rebuild-before-return) rejected: it would block the TAB on package import + shelling completers. |

---

## Phase 1 — Exit-code contract (do this first)

Every later exit-code assertion builds on this.

### 1.1 Honor explicit exit codes in `_result`; propagate `RunFailed` codes (F28, F19) — S

Files: `src/footman/executor.py`, tests in `test_params.py`, `test_binding.py`, `test_app.py`, `test_context.py`

- `_result` (executor.py:247): `code=code if error is None else 1` kills the
  explicit 2 passed at :216. Change to keep the invariant "errored result is
  never 0" while honoring explicit codes:
  `ok=error is None and code == 0`, `code=code if code != 0 else (1 if error is not None else 0)`.
- Fold in F19: in `_call`, before the generic `except Exception` (:189), add
  `except RunFailed as exc: return (exc.result.code or 1), None, exc`.
  `RunFailed.result.code` already carries the command's code.
- No caller changes: task-raised exceptions stay 1; `SystemExit(N)`/int returns
  arrive with `error=None`, untouched (pinned by existing tests).
- Tests: bind-refusal (`--id not-a-uuid` on a `UUID | None` param) exits 2
  end-to-end; task exception still 1; task whose `run()` exits 3 → `fm` exits 3
  (use `sys.executable -c "raise SystemExit(3)"` for portability).
- Visible change: binding refusals move 1→2; `run()` failures move 1→N. Both
  are what docs/ci.md:80 and docs/troubleshooting.md:99 already promise.

---

## Phase 2 — Output routing (one commit)

F10 + F22 + F11 all rewrite `context.routing()`; landing any one alone means
rewriting it three times. Use the merged form below.

### 2.1 routing(): stderr router + reentrancy + cp1252 crash guard (F10, F22, F11) — M

Files: `src/footman/context.py`, tests in `test_context.py`, `test_schedule.py`
Merged final form:

```python
@contextlib.contextmanager
def routing():
    global _router, _err_router
    prev_out, prev_err = _router, _err_router
    real_out, real_err = sys.stdout, sys.stderr
    for stream in (real_out, real_err):           # F11: never crash on cp1252
        with contextlib.suppress(Exception):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
    _router, _err_router = _Router(real_out), _Router(real_err)   # F10
    sys.stdout, sys.stderr = _router, _err_router
    try:
        yield real_out
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        _router, _err_router = prev_out, prev_err                 # F22: stack, not None
```

- F10: `_Router` works unchanged for stderr; a task's stderr now lands in the
  same per-task sink as stdout (matching the subprocess path's merge), so
  `--json` gets failure text and parallel blocks never interleave stderr.
  `_run_callable`'s routed branch needs no change.
- F22: nested `run_plan` (the `fm` pytest fixture inside `tools.pytest(in_process=True)`)
  no longer permanently downgrades the outer run's capture.
- F11: `→`/`✓`/`✗` and replayed UTF-8 tool output degrade to `?` on cp1252
  pipes instead of `UnicodeEncodeError` aborting the whole parallel run. Do NOT
  add per-glyph ASCII fallbacks — replay of captured output still needs the
  errors handler; reconfigure is the mechanism.
- Tests: stderr captured into `StepResult.output` with nothing leaking to real
  stderr; contiguous stdout+stderr per task in parallel; `_router`/`sys.stdout`
  identity restored after a nested `Runner.invoke`; cp1252 `TextIOWrapper`
  stdout survives a parallel run with all results ok.

### 2.2 Subprocess decode: UTF-8 default + per-call override (F39, D17) — S

Files: `src/footman/context.py`, tests in `test_context.py`

- `run()` gains `encoding: str | None = "utf-8"`, threaded to
  `_run_subprocess` (:210-212) alongside the existing `errors="replace"` —
  dev tools emit UTF-8 regardless of the ANSI code page; today Windows
  decodes cp1252 and mojibakes `--json` payloads and replays.
- `run("cl.exe ...", encoding="oem")` covers ANSI/OEM-speaking tools;
  `encoding=None` restores locale behavior (that is literally what `None`
  means to `subprocess`), so the escape hatch costs nothing.
- `errors="replace"` stays unconditional — the never-crash net.
- fm's own stdout/stderr stay non-configurable (D17); the callable branch has
  no bytes boundary, so the kwarg is ignored there (document in the
  docstring).
- The twin hunk in `tools.Tool.installed_version` lands with item 8.1 (same
  lines that commit rewrites); it stays hardcoded UTF-8 — version banners are
  ASCII digits.
- Tests: subprocess emitting `résumé ✓` in UTF-8 → step output contains it
  verbatim (pins the codec on the windows CI leg); child emitting latin-1
  bytes + `run(..., encoding="latin-1")` decodes correctly (pins the
  override).

---

## Phase 3 — Strict coercion mechanism (coerce/executor/split)

One shared mechanism, then three riders. Land 3.1 before any other split.py
work; ws-params owns split.py afterwards.

### 3.1 `coerce_token` + union-aware choices; strict env values; variadic peel (F01, F21, F24) — L

Files: `src/footman/coerce.py`, `src/footman/executor.py`, `src/footman/manifest.py`, `src/footman/split.py`
This is three coordinated changes sharing one mechanism — land as one series:

1. **coerce.py**: add `coerce_token(value, element)` — strict variant of
   `coerce_one` that raises `ValueError("expects <phrase> (got '...')")` when
   the token fits none of the element's scalar tags. Move split.py's
   `_TYPE_PHRASE` table into coerce.py (`type_phrase(tags)`), import it back so
   wording stays byte-identical. Add `union_members()`, union-aware choice
   gathering (Literal values + Enum members across members), and
   `eagerly_checkable()` (every member taggable or Literal/Enum).
   `coerce_one`: for unions, try exact Literal/Enum membership before the tag
   pass (`Literal[5] | str` coerces `'5'` → int 5). **coerce_token must check
   choices before raising** — else `Literal['fast','slow'] | int` env values
   reject `'fast'` (the one semantic conflict the audit caught).
2. **executor.py** (F01): `_env_value.one()` uses `coerce_token`; rename
   `_validate_env` → `_validate_value`. Uncoercible env values now fail with
   `--jobs (from $JOBS) expects an integer (got 'abc')` instead of silently
   binding the raw string (and skipping bounds).
3. **manifest/split/executor** (F21): peel VAR_POSITIONAL annotations —
   `manifest.param_spec` emits `types` for `Annotated` variadics; split
   validates variadic tokens eagerly (fix `_validate`'s label to `<nums>` not
   `--nums`); `bind()` peels and runs the same validators on variadic +
   passthrough tokens. Fixes markers-silently-dropped AND plain `*nums: int`
   binding strings.
4. **manifest/split** (F24): emit `choices` AND `types` together for mixed
   unions; `split._check` accepts membership OR scalar-coercion success, with a
   combined message (`--x must be one of fast|slow, or an integer`); unions
   with non-checkable members (`UUID | int`) skip eager rejection and fail at
   bind instead. No existing manifest carries both keys, so old specs take old
   paths bit-for-bit.
- Tests: env strict failures (+exit 2 per Phase 1); `Literal|int` accepts both
  shapes from CLI and env; annotated variadic bounds enforced with taught
  errors; manifest specs carry `types`/`choices` as designed.
- Visible: garbage env values now fail tasks (documented behavior); mistyped
  variadic tokens fail at parse time instead of crashing mid-task.

### 3.2 Propagate dict value-type markers (F23) — M

Files: `src/footman/coerce.py`, `src/footman/split.py` — after 3.1.

- `peel`'s dict branch keeps only `.element`/`.multiple` of the value peel;
  merge inner `bounds`/`path_req`/`checks` into the returned `Peeled`, and pass
  marker keys to `_consume_pair`'s value check so
  `dict[str, Annotated[int, between(1,5)]]` rejects `x=99` eagerly.

### 3.3 Suggest-only contract in the positional next-task guard (F34) — S

Files: `src/footman/split.py` — after 3.1.

- Extract `_suggest_only(choices, dynamic)` used by both `_check` and
  `_consume_positional`, so a `suggest(strict=False)` positional stops
  hard-rejecting values that collide with task names. Share 3.1's acceptance
  predicate so typed values that collide with task names pass too.

### 3.4 Bind positional-only parameters positionally (F26, F27) — S

Files: `src/footman/executor.py`

- `bind()` currently passes everything as kwargs → guaranteed `TypeError` for
  `def build(target: str, /)`. After the main loop, move leading
  POSITIONAL_ONLY params (excluding the injected `ctx`) from kwargs into a
  positional list in signature order, filling gaps with defaults.
- Tests: plain, mixed, defaults-with-hole, alongside `*args`, with `ctx`.

### 3.5 Reject NaN in `between()` bounds (F31) — S

Files: `src/footman/split.py`, `src/footman/executor.py` — after 3.1 (rename).

- Flip both bounds checks to negated comparisons
  (`not (number >= lo)` / `not (number <= hi)`) — rejects NaN, identical for
  real numbers, sensible for infinities. Comment why, so nobody "simplifies"
  it back. Covers CLI and env paths.

---

## Phase 4 — Mapping params + remaining param edges

### 4.1 Mapping params are always options; required-option mechanism (F02, F03) — M

Files: `src/footman/manifest.py`, `src/footman/split.py`, `src/footman/_app.py`

- `manifest.py:101`: `spec["kind"] = "option"` unconditionally for mappings;
  `required: True` when no default. General splitter rule (not a dict special
  case): any option spec with `required: True` absent from `seg.values` →
  `ChainError("deploy: missing required option --vars (expects KEY=VALUE)")`.
- `bind()` needs no change (option-path dicts already arrive as tuples).
- Help: `_usage_fragment` renders required options unbracketed;
  `_param_detail` appends "required".
- Manifest key is additive; no SCHEMA_VERSION bump (hash change → auto
  rewrite). Completion starts offering `--vars` for free.
- Tests: the exact docs/typing.md:73 example works verbatim; omission is a
  taught error; the old positional spelling never produces an unpack crash.

### 4.2 No-default `bool` = required flag (F45) — S

Files: `src/footman/manifest.py`, `src/footman/split.py`, `docs/typing.md` — after 4.1.

- Flag branch: `required: True` when no default; error message teaches both
  spellings: `missing required option --prod (or --no-prod)`.

### 4.3 Pass `typing.Any` (and `object`) through coercion (F00) — S

Files: `src/footman/coerce.py`

- `coerce_custom`: `if element is Any or element is object or not isinstance(element, type): return value`.
  On ≥3.11 `isinstance(Any, type)` is True, so today `Any("hello")` raises on
  every value. One guard fixes scalars, dict values, and `*args: Any`.

### 4.4 Bare `list`/`dict` annotations = parameterized forms (F25) — S

Files: `src/footman/coerce.py`

- `peel`: `if ann is dict or get_origin(ann) is dict` (same for list) — bare
  `tags: list = []` stops exploding `'abc'` into `['a','b','c']`.

### 4.5 Reject `=value` on flag-kind globals (F32) — S

Files: `src/footman/split.py`

- `_parse_globals`: `--sequential=false` currently ENABLES sequential (any
  attached value is truthy). Reject with the same taught error task flags get:
  `--sequential is a flag and takes no value`.

### 4.6 Refuse `--` as an option value (F33) — S

Files: `src/footman/split.py`

- `_consume_option`: a forgotten value before `--` swallows the passthrough
  sentinel. Raise `--marker expects a value, but found '--'`; the attached
  `--marker=--` form stays as the escape hatch for a literal.

### 4.7 Brand the `--help` globals row (F30) — S

Files: `src/footman/split.py`, `src/footman/_app.py`

- GLOBALS help text becomes `help for {prog}, ...`; `_print_global_help`
  substitutes with `.replace` (never `.format` — braces in future strings must
  not crash help).

### 4.8 Reject `**kwargs` tasks with a taught SpecError (F44) — S

Files: `src/footman/manifest.py`, `docs/troubleshooting.md`

- `param_spec`: VAR_KEYWORD → `SpecError("**opts is not supported — declare
  named parameters, or accept KEY=VALUE pairs with a dict[str, str] parameter")`.
  Today it's advertised as a positional and binds a silently wrong shape.

---

## Phase 5 — Scheduler (independent; parallel with 3–4)

### 5.1 Stop deduping explicit duplicate chain segments (F09) — M

Files: `src/footman/schedule.py`, `docs/orchestration.md`, tests

- `_build_dag` keys nodes by `id(fn)`, so `fm build web build api` runs once
  with `target=api` and exits 0 (while `--dry-run` truthfully shows both).
  Separate node identity (serial ints) from dep-dedup identity
  (`dep_nodes: dict[id(fn), node]`). Preserved: shared pre/post deps run once;
  explicit args beat a bare dep in both orders; toposort stability.
- One doc line: each explicit segment is its own invocation.
- Tests: duplicate segments run twice (sequential order pinned; parallel both
  seen); shared-dep-once variants; explicit-beats-dep in both orders.

### 5.2 Make the `--sequential` regression test load-independent (F59) — S

Files: `tests/test_schedule.py`

- Assert `len(results) == 1` (true sequential skips b after a's barrier
  timeout; a regressed parallel path always yields 2 results regardless of
  thread latency) instead of relying on the 0.3 s barrier timing.

---

## Phase 6 — Context remainder (after Phase 2)

### 6.1 Merge `parallel()` child steps into the parent (F12) — S

Files: `src/footman/context.py`

- One line inside the existing completion lock: `parent.steps.extend(child.steps)`.
  Today every `run()` inside `parallel()` vanishes from `--json` and
  `recording()`. Steps land in completion order — assert as a set.

### 6.2 Fail `parallel()` on non-zero int returns; cover `keep_going` (F13, F42) — S

Files: `src/footman/context.py`

- Synthesize the failure where the code is computed:
  `if code != 0: error = RunFailed(StepResult(_label(call, ()), code, "", 0.0))`
  — the existing gate then handles raise-vs-collect uniformly;
  `keep_going=True` still returns the codes list.
- Tests: `parallel(failing_task)` fails the enclosing task; keep_going returns
  `[1, 0]` without raising (first-ever coverage of that branch). Pin D16 when
  both this and 1.1 are in.

### 6.3 `_run_callable`: `capture=False` + ctx.cwd/ctx.env (F60, F17) — M

Files: `src/footman/context.py`, `docs/tools.md`, tests in `test_context.py`, `test_tools.py`
One commit — both items re-sign the same function:

- New signature `_run_callable(cmd, args, *, capture=True, env, cwd)`.
- F60: `capture=False` short-circuits to `_call_for_code` (no buffering, live
  output, returns `''` like the subprocess branch) — today the flag is
  silently ignored for callables and buffers unboundedly for serve-style
  tasks.
- F17: apply `{**ctx.env, **(env or {})}` and `cwd or ctx.cwd` via a
  `_process_state` context manager (RLock-guarded os.chdir/os.environ patch
  with a lock-free fast path when there's nothing to apply). In-process tools
  finally honor the run-from-defining-folder contract that the subprocess
  fallback of the same call already obeys. The `capture=False` short-circuit
  must run INSIDE `_process_state`, or uncaptured callables lose cwd/env.
- Lock-free fast path is load-bearing: in-memory Group tasks have no
  DEFINING_DIR, so existing barrier-overlap parallelism tests must stay green
  untouched.
- Visible: in-process mkdocs/zensical/pytest genuinely run from the defining
  folder now; calls needing a chdir/env patch serialize (inherent to
  process-global state).

### 6.4 Suppress ANSI codes under `--no-color`/`NO_COLOR`/`TERM=dumb` (F41) — M

Files: `src/footman/schedule.py`, `docs/orchestration.md`

- Gate where capabilities are decided: `_make_ctx` computes
  `plain = ctx.no_color or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"`
  and folds it into `ctx.tty`; `_make_progress` adds the same conditions to
  its return-None guard. Per D6 the live line is absent, not rewritten-plain.
- Amend `docs/orchestration.md:69` ("plain text" → "absent, like piped output").

### 6.5 Pin env/cwd subprocess propagation with real-process tests (F40) — S

Files: `tests/test_context.py` (tests only)

- The env merge and cwd threading are load-bearing and completely unasserted —
  dropping `ctx.env` entirely leaves the suite green today. Add
  subprocess-observing tests: ctx.env flows; call-kwarg beats ctx.env beats
  os.environ; cwd via kwarg and via ctx. Compare against `tmp_path.resolve()`
  (macOS /tmp symlink).

---

## Phase 7 — App layer, compose, discovery, config

### 7.1 `_app.py` restructure: `_execute` + `_run_tree`/`run_group` (F36 shell, F18 tail) — M

Files: `src/footman/_app.py`, `src/footman/testing.py`
One move-only refactor commit both workstreams agreed on:

- Extract everything in `_run` after the `-C` branch into `_execute(argv, g, collect)`;
  inside it, extract the post-manifest tail (help/where/split/list/tree/
  dry-run/run_plan/json/summary/exit-code fold) into `_run_tree(reg, tree, argv, cfg, collect)`.
- Add `run_group(root, argv, brand, collect)`: parse globals, `--version`
  banner, `build_manifest`, `_run_tree`. **No KeyboardInterrupt wrapper** (D13).
- `testing.Runner._invoke_group` becomes a one-liner delegating to
  `run_group` — deleting the drifted re-implementation that executes tasks on
  `--help` (the review's worst testing-surface bug) and produces empty output
  for `--version`/`--list`/`--tree`/`--json`.
- Tests: help-never-executes in Group mode (side-effect list stays empty);
  `--version` banner with custom branding; `--list`/`--tree`/`--json`/`--quiet`
  /`--where` parity.

### 7.2 Help-first: gate `--install-completion`, help without a tasks file (F06, F63) — M

Files: `src/footman/_app.py` — inside 7.1's `_execute`.

- Compute `wants_help = _wants_help(argv)` once. Gate the install branch on
  `not wants_help` (today `fm --install-completion fish --help` WRITES rc
  files). In `_discover`'s no-files branch, `wants_help` → print global help +
  "(no tasks file found — looked for ...)" and exit 0, so a stuck new user
  sees `-f`/`-C` instead of a bare one-liner. `--list`/`--tree` keep the
  pinned one-line message. Per D7, `--version` stays first.

### 7.3 Restore cwd after `-C` (F36) — S

- Wrap `_execute` in try/finally around the `os.chdir`; restore with
  `contextlib.suppress(OSError)` (original dir may have vanished). Fixes
  `Runner.invoke("-C sub lint")` permanently moving the host pytest process.

### 7.4 Don't rewrite the completion manifest on `-f` runs (F37) — S

- `-f` → `build_manifest(reg)` (cache-free) instead of `sync_manifest(reg, cwd)`;
  today an `-f` run poisons the cwd's TAB completion until the next plain run.
  One doc sentence in monorepos.md.

### 7.5 `Runner.invoke` file path: propagate KeyboardInterrupt (F52) — S

Files: `src/footman/testing.py` — after 7.1.

- Call `_app._run` instead of `_app.run`, bypassing the CLI's KI→130 wrapper
  (which stays, pinned, for real CLI entry). Docstring is the contract; a test
  runner must let pytest handle Ctrl-C.

### 7.6 Empty `--tree` prints "No tasks defined." (F35) — S

- Guard `_print_tree`'s top-level call via the indent sentinel, mirroring
  `_print_list`. Today it prints zero bytes and exits 0.

### 7.7 Plugin robustness: taught import errors + reachable Group fallback (F07, F08) — S

Files: `src/footman/compose.py`, `docs/troubleshooting.md` — one commit, same lines.

- F07: wrap `matches[0].load()`; non-RegistrationError exceptions become
  `RegistrationError("plugin 'x': failed to import (ImportError: ...)")` →
  existing mount guard reports at exit 2. Today a plugin with a missing
  optional dep dumps a raw traceback on every invocation including `--help`.
- F08: the bare-module branch routes through the memo and
  `_adopt_explicit_group` directly instead of `_import_source` (which sees the
  module already in sys.modules and raises the misleading "already imported
  outside include()" error). The documented explicit-Group provider convention
  becomes actually usable.

### 7.8 Isolate sibling-helper imports per cascade file (F14) — M

Files: `src/footman/discover.py`, `docs/monorepos.md`

- Per D8: in `_import_file`, move the file's dir to `sys.path[0]`
  (move-to-front, not insert-if-absent), snapshot `sys.modules`, and in a
  `finally` evict newly imported direct siblings (file in the same dir, or
  package one level down). Two tasks files each doing `import helpers` now
  get their own module instead of whoever-imported-first-wins.
- Deliberately does NOT evict deeper imports or editable-installed packages.
  Document: import helpers at the top of the tasks file; multi-dir deferred
  imports are a known limitation.

### 7.9 Fork provider trees at graft boundaries (F38) — M

Files: `src/footman/compose.py`

- `include()`/`mount_plugins()` graft provider Groups by reference, and
  `_overlay` later mutates the process-global `_module_trees` memo in place —
  one project's cascade tasks leak into every later in-process invocation.
  Add `_fork(tree)` (fresh Group objects/dicts, shared task fns) and use it at
  both graft sites. Task fns stay shared deliberately (DEFINING_DIR is
  re-stamped per load).

### 7.10 Reset the registry even when a cascade import fails (F62) — S

Files: `src/footman/discover.py`

- Wrap `load_tree`'s import loop in try/finally around `registry.reset()`.
  Today a file that registers then raises leaves ghost tasks in
  `registry.root` for the rest of the process.

### 7.11 `availability()` must never crash (F29) — S

Files: `src/footman/registry.py`

- `_importable`: catch `Exception` (not just ImportError/ValueError) — a
  dotted `requires` imports parent packages via `find_spec`, and a parent
  whose `__init__` raises currently crashes `fm --list` with a traceback.
  Broken parent → task lists as unavailable. Fix the two "does not import
  them" docstrings (composing.md already tells the truth).

### 7.12 Loud error for missing explicit `--config` (F15) — S

Files: `src/footman/config.py`, `docs/troubleshooting.md`

- `load_config`: `is_file()` pre-check → `ConfigError(f"{path}: no such file")`;
  thread `required=True` through `_read_toml` so exists-but-unreadable is loud
  too. Today `--config prod.tmol` (typo) is silently ignored, contradicting
  the function's own docstring.

### 7.13 Add `footman.toml` to PROJECT_MARKERS (F43) — S

Files: `src/footman/_paths.py`

- One line. A directory containing footman's own config file is a project
  root; today a footman.toml-only root (Docker context with .git ignored) is
  invisible from subdirectories.

### 7.14 Repair the three non-pinning tests (F20, F57, F58) — S

Files: `tests/test_app.py`, `tests/test_compose.py`

- `test_where`: replace the `or ":" in out` tautology with a real
  path/line-number pin (tolerating co_firstlineno variance: line 4 or 5).
- `test_requires_check_does_not_import`: delete the manual `sys.modules.pop`
  that defeats monkeypatch's restore (the textwrap eviction currently leaks
  into the whole session).
- `test_included_tasks_run_from_the_includers_dir`: actually observe the
  mechanism — a provider task printing `ctx.cwd`, asserted equal to the
  includer's project dir.

---

## Phase 8 — Tools surface

### 8.1 Privatize tools.py imports; enforce stub parity (F50, F53, F39-tools) — M

Files: `src/footman/tools.py`, `src/footman/tools.pyi`

- Alias every module import privately (`import re as _re`, …,
  `from footman.context import run as _run`) so `tools.run`/`tools.sys` become
  Tools via `__getattr__` as the stub already promises — today they typecheck
  as Tools and crash at runtime. Declare `_argv_lock` (and the private
  aliases) in the stub.
- Fold in F39's tools half: `installed_version`'s `subprocess.run` gains
  `encoding="utf-8", errors="replace"`.
- Add the AST parity test: every module-level runtime binding in tools.py must
  be declared in tools.pyi (allowlist: `annotations`). This freezes the stub —
  land before any other tools.py edits.

### 8.2 `recording()` kwarg overrides (F51) — S

Files: `src/footman/testing.py`

- `Context(**{"dry_run": True, "quiet": True, **overrides})` — today exactly
  the two documented override fields raise "got multiple values".

---

## Phase 9 — Completion (independent; both files owned here)

### 9.1 Complete `--opt=value` (F49) — M

Files: `src/footman/_complete.py`

- Three edits in `complete()`: keep `value_opt` armed across bash's bare `=`
  word; strip a leading `=` from the partial in the value branch; handle an
  attached `--opt=val` partial for shells that don't split on `=` (return full
  `--mode=strict` tokens). Today the documented `=` spelling completes to
  nothing (zsh/fish) or garbage (bash).

### 9.2 Model value-bearing globals in the completion walk (F61) — M

Files: `src/footman/_complete.py`

- Hardcoded mirror of split.GLOBALS arity (`_GLOBAL_VALUE`, `_GLOBAL_MAYBE`,
  `_GLOBAL_CHOICES`) — the hot path stays stdlib-only — consumed exactly like
  `_parse_globals` before the walk. `fm -C docs <TAB>` stops descending into a
  `docs` group as if `-C`'s value were a task. Drift pin: a test builds the
  expected sets FROM split.GLOBALS, so renaming a global fails CI. Land the
  pin after Phase 3–4 settle split.GLOBALS.

### 9.3 pwsh: `--empty-partial` flag instead of an empty argv element (F16) — M

Files: `src/footman/_complete.py`, `src/footman/_shellcomp.py`

- WinPS 5.1 and pwsh 7.0–7.2 drop empty-string args to native commands, so TAB
  after a space re-completes the previous word. Change the wire protocol: the
  hook passes `--empty-partial` and the resolver appends the `''` itself.
  Functional test runs the completion under `$PSNativeCommandArgumentPassing = 'Legacy'`.
  CHANGELOG: users re-run `fm --install-completion pwsh`.

### 9.4 bash: glob-safe COMPREPLY with `printf %q` (F46) — M

Files: `src/footman/_shellcomp.py`

- Replace `COMPREPLY=($(...))` (pathname-expands candidates: a suggest()
  candidate `*.md` becomes README.md NOTES.md) with a bash-3.2-safe read loop plus
  `printf -v line %q`. Keep `-o default`; do NOT use `-o filenames` (D10).

### 9.5 `_append_once`: BOM sniff + latin-1 fallback + InstallError (F47) — M

Files: `src/footman/_shellcomp.py`

- Read rc bytes; sniff UTF-8/UTF-16 BOMs and append in the matching encoding
  (never a BOM-injecting encoder mid-file); no BOM → try UTF-8, fall back to
  latin-1 (round-trips bytes). Wrap residual failures in
  `InstallError("could not update ... — add this line yourself: ...")` → the
  existing exit-2 path. Today a WinPS5 UTF-16 profile crashes install with a
  raw traceback.

### 9.6 Target the rc files shells actually read (F48) — M

Files: `src/footman/_shellcomp.py`

- Per D11: zsh honors `$ZDOTDIR`; on darwin bash gets `.bashrc` + the first
  existing login profile (create `.bash_profile` only when no login profile
  exists). Today macOS bash and XDG-zsh installs print success and never
  activate. Test fixture: `delenv ZDOTDIR` so existing tests stop depending on
  the dev machine.

### 9.7 Time-based completion refresh — stale-while-revalidate (feature, D18) — M

Files: `src/footman/_complete.py`, `src/footman/manifest.py`, `src/footman/config.py`, `docs/completion.md`, tests

Not a review finding — a new delight feature. The completion hot path never
rebuilds ([_complete.py:167](src/footman/_complete.py) just reads the JSON);
only real `fm` runs refresh the baked dynamic-completer output via
`sync_manifest`. So dynamic completions (git branches, file lists) go stale for
"time since your last real `fm` command here", which is unbounded.

- In `complete()`, after loading the cached manifest, stat its mtime; if
  `now - mtime > max_age`, **spawn a detached rebuild for next time** and return
  the cached results now (SWR — never block the TAB on package import + shelling
  completers).
- The rebuild child imports the package and runs `sync_manifest(reg, cwd)` for
  the cwd cascade (never an `-f` tree — coordinate with F37/7.4).
- **mtime discipline (the trap):** `sync_manifest` only writes on hash change,
  so a no-op refresh won't bump mtime → re-spawn every TAB. After a
  time-triggered check, `os.utime()` the manifest even when unchanged so the
  clock resets. Bump mtime *before* spawning (storm guard: concurrent TABs
  don't each spawn). Detach correctly on Windows (`DETACHED_PROCESS` /
  `CREATE_NEW_PROCESS_GROUP`) and POSIX (`start_new_session`).
- Config: `[tool.footman] completion.max_age` (duration string, e.g. `"10m"`);
  `off`/`0` disables. Default ≈ 10 min. Hot path stays stdlib-only — read the
  value from the cached manifest (bake it in at build time) rather than parsing
  config on the completion path.
- Tests: fresh manifest → no spawn; aged manifest → spawn + mtime bumped even on
  a no-op rebuild; `off` → never spawns; rapid TABs after aging → exactly one
  spawn.

---

## Phase 10 — Docs truth pass + test hygiene (last)

### 10.1 Pin exit-2-for-binding-refusals end-to-end (F54) — S

Files: `tests/test_app.py` — strictly after 1.1 (fails red before).

- UUID refusal and env-bounds refusal both exit 2 through the real CLI path.
  No doc change needed once 1.1 lands.

### 10.2 `Many[T]` doc rewrite (F04) — S

Files: `docs/typing.md`, `tests/test_params.py`, `src/footman/params.py`

- Per D14: doc says always-a-list; add the single-token → `["web"]` pinning
  test; delete the dead `MANY` sentinel.

### 10.3 Dict docs after D2 (F05) — S

- With D2 implemented (4.1), `docs/typing.md` needs zero edits — verify the
  :73 example against the new tests and close.

### 10.4 `plugins` row in monorepos.md config table (F55) — S

- One table row, wording aligned with reference.md:80.

### 10.5 Export `capture`/`Runner`/`Result`/`recording` (F56) — S

Files: `src/footman/__init__.py`, `tests/test_cli.py`

- Lazily via the existing `__getattr__` (zero import cost, completion hot path
  untouched). Extend `test_lazy_reexports` to iterate `__all__` asserting every
  entry resolves — permanent drift guard.

### 10.6 conftest: isolate fixtures with `registry.capture()` (F64) — S

Files: `tests/conftest.py`, `tests/test_registry.py`

- `load_tasks` returns a captured Group instead of leaking ~25 sample tasks
  into `registry.root` for the session. Five lines, isolates both directions.

---

## Phase 11 — Delights (features, not review findings)

Opt-in quality-of-life features that lean on data the manifest already carries.
Independent of phases 1–10; do them whenever. All reinforce footman's identity
(the signature is the CLI; the error/help is the docs) rather than bolt on
surface area.

### 11.1 "Did you mean?" at every not-found site — S

Files: `src/footman/split.py`, `src/footman/_app.py`, tests

`difflib.get_close_matches` is already used for option *values*
([split.py:104](src/footman/split.py)), but the three "not found" sites skip
it. Extract the existing idiom into one helper and apply it everywhere:

```python
def _did_you_mean(word: str, known: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(known), n=1)
    return f" — did you mean {close[0]!r}?" if close else ""
```

- Unknown task ([split.py:210](src/footman/split.py)): match `got` against
  `list(node["groups"]) + list(node["tasks"])`; lead with the hint, keep the
  full `know:` list (or drop it behind `--help` if it's long).
- Unknown option ([split.py:292](src/footman/split.py)): match `name` against
  the task's `opts` keys (include `--no-x` flag forms).
- `--where` unknown task ([_app.py:353](src/footman/_app.py)): match against
  the flat task list.
- Leave the value site as-is; just route it through the shared helper so all
  four read identically.
- Tests: `fm biuld` → suggests `'build'`; `fm build --fux` → suggests
  `--fix`; a genuinely unmatchable typo adds no hint (no false confidence).

### 11.2 Rich completion descriptions (zsh/fish) — M

Files: `src/footman/_complete.py`, `src/footman/_shellcomp.py`, tests

The task docstring is already baked into the manifest, but `complete()` emits
bare candidate strings. Emit `name\tdescription` pairs and have the zsh
(`compadd -d`) and fish (`-a ... -d`) hooks render the description column, so
holding TAB teaches the whole CLI:

```text
build   — compile and bundle
deploy  — ship to an environment
```

- Keep the wire format backward-safe: description after a tab; bash (no
  description support) splits on tab and uses the first field only.
- Pairs with 9.7 — completions become both fresh and self-describing.
- Tests: resolver emits `name\tsummary`; per-shell functional tests assert the
  description reaches `compadd`/`complete`.

### 11.3 Auto-generated example invocation in `--help` — M

Files: `src/footman/manifest.py` (param_spec), `src/footman/_app.py` (help render), tests

Synthesize a realistic invocation straight from the signature and show it in
the task's help — the example can't drift because it's derived, not written:

```text
Example: fm deploy --env prod --dry-run
```

- Build from the param specs: required positionals as sample values, required
  options as `--name <value>`, one representative flag. Skip optional noise.
- The purest expression of "the signature is the CLI".
- Tests: `def deploy(env: str, dry_run: bool = False)` renders the example
  above; a no-arg task renders `fm <task>` with no trailing junk.

### 11.4 Bare `fm` → the task list — S

Files: `src/footman/_app.py`

`fm` with no args currently errors; fall through to the `--list` view instead —
a warmer empty state that shows a new user what's available. One branch in the
dispatch; coordinate with 7.2 (help-first) so precedence stays clean.

---

## Release notes to draft when this ships

User-visible behavior changes, all in the documented-contract direction:

- Exit codes: binding refusals 1→2; failed `run()` commands 1→N; failing
  `parallel()` branches no longer silently succeed.
- Env-sourced values are validated like CLI tokens (garbage now fails loudly).
- Subprocess output is decoded as UTF-8 by default; pass `encoding=` to
  `run()` for tools that speak something else.
- `--json`/`recording()` gain steps from inside `parallel()` and stderr from
  in-process tools.
- In-process tools run from the defining folder (cwd/env honored).
- Duplicate explicit chain segments each run.
- `fm --install-completion bash|zsh|pwsh` should be re-run to pick up the
  fixed hooks.
- `--no-color`/`NO_COLOR` now also suppress the live progress line.
- `**kwargs` tasks are rejected at manifest build (were silently broken).

## Traceability

All 65 findings map to items above: F00→4.3, F01→3.1, F02/F03→4.1, F04→10.2,
F05→10.3, F06→7.2, F07/F08→7.7, F09→5.1, F10/F11→2.1, F12→6.1, F13→6.2,
F14→7.8, F15→7.12, F16→9.3, F17→6.3, F18→7.1, F19→1.1, F20→7.14, F21→3.1,
F22→2.1, F23→3.2, F24→3.1, F25→4.4, F26/F27→3.4, F28→1.1, F29→7.11, F30→4.7,
F31→3.5, F32→4.5, F33→4.6, F34→3.3, F35→7.6, F36→7.1+7.3, F37→7.4, F38→7.9,
F39→2.2+8.1, F40→6.5, F41→6.4, F42→6.2, F43→7.13, F44→4.8, F45→4.2, F46→9.4,
F47→9.5, F48→9.6, F49→9.1, F50→8.1, F51→8.2, F52→7.5, F53→8.1, F54→10.1,
F55→10.4, F56→10.5, F57/F58→7.14, F59→5.2, F60→6.3, F61→9.2, F62→7.10,
F63→7.2, F64→10.6.
