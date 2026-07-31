# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Misuse the work-item spec makes STRUCTURAL — move 3's negative half.

Never executed; checked by basedpyright and mypy only (ty and pyrefly do
not honour per-line ignores, so this file stays out of their scope, like
`typecheck_api_negative.py`). Every line is a deliberate type error
carrying `# type: ignore[code]`: if the skeleton loosens and the error
disappears, the ignore goes stale and both checkers turn the line red.
The ignore is the assertion.

These are the spec's taught errors, promoted to type errors by the
skeleton in `typecheck_workitem.py`: the walls of I13 and I3, the closed
yield vocabulary, and (conditional on decision 8) the bare-callable ban.
"""

from typecheck_workitem import (
    ResultView,
    StepBody,
    covered,
    lint,
    parallel,
    step,
)


def _declared_means_recorded() -> None:
    # walk 4 / I13: declared ⟹ recorded — the keyword does not exist
    # at task grain, so hiding a declared row is unspellable.
    lint.opts(recorded=False)  # type: ignore[call-arg]  # a task is part of the story


def _boundary_policy_needs_a_declaration() -> None:
    # walk 4 / I13: confirm resolves at the request boundary; an
    # anonymous item has no boundary to resolve it at.
    covered.opts(confirm="really?")  # type: ignore[call-arg]  # steps take execution policy only


def _the_yield_vocabulary_is_closed() -> None:
    # I7: bare yield = checkpoint; yielding a value is a taught error —
    # and a static one: StepBody's yield type is None.
    @step
    def chatty() -> StepBody[None]:
        yield "progress"  # type: ignore[misc]  # the channel is reserved

    del chatty


def _the_forged_receipt_is_unspellable() -> None:
    # I3: no record without work. The rejected receipt primitive —
    # a plain call minting a titled verdict — matches no overload; the
    # with-form binds a record to a real block by construction.
    step("done", code=0)  # type: ignore[call-overload]  # a record needs work


def _the_verdict_follows_the_code() -> None:
    # I2: ok derives from code — code=1 with ok=True cannot be written.
    def hook(view: ResultView) -> None:
        view.ok = True  # type: ignore[misc]  # write the code; ok follows
        view.stdout = ""  # type: ignore[misc]  # review sees what was captured

    del hook


def _bare_callables_are_refused() -> None:
    # decision 8's ban, structural: a lambda has no chosen grain (no
    # .opts), so neither maker protocol matches. Conditional on the
    # decision landing; delete this line if the ban is rejected.
    parallel(lambda: 0)  # type: ignore[call-overload]  # lift it: step(fn)
