"""Move 3 of the work-item spec: the type checkers as the loom.

A stub-only skeleton of the spec's surface — the view, the lifters, the
yield vocabulary, item/record shapes — checked by all four checkers so
that fragments which merely *sound* compatible are forced to actually
meet. Nothing here is implementation and nothing imports from footman:
every type is declared in this file, marked where it restates a shipped
shape and where it is spec-only. Never executed: pytest skips
non-`test_` files and nothing imports this module. The taught errors the
skeleton makes *structural* live in `typecheck_workitem_negative.py`
(mypy + basedpyright only, per the `typecheck_api_negative.py` pattern).

The spec: notes/20260731-work-item-spec.md. The thinking record:
notes/20260731-work-item-model.md. Where a shape below forecloses or
answers an open decision, the comment says which; the note's move-3
section carries the findings this file forced.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager
from typing import (
    Any,
    Generic,
    Literal,
    NamedTuple,
    NoReturn,
    ParamSpec,
    Protocol,
    Self,
    TypeAlias,
    TypedDict,
    TypeVar,
    Unpack,
    assert_type,
    overload,
)

P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)
F = TypeVar("F")


# --- the record family -------------------------------------------------


class Address:
    """A node's tree-derived name: parent-path + label + ordinal (I6).

    Because every committed record carries its address, and an address
    encodes its own parent chain, a flat creation-order list of records
    derives the report tree with no extra storage — I11's two
    projections stay cheap whichever container shape decision 1 picks.
    """

    parent: Address | None
    label: str
    ordinal: int

    def __str__(self) -> str:
        raise NotImplementedError


# Where in the grain's lifecycle a failure happened (ruled 2026-07-31:
# every error indicates its moment). The union of all moments; a grain
# without a moment (steps have no bind) simply never reports it.
Phase: TypeAlias = Literal["bind", "enter", "body", "review", "observe"]


class AuditEntry(NamedTuple):
    """One entry of the audit (decision 10, ruled 2026-07-31): a
    lifecycle moment that acted on — or failed — the grain's verdict.
    `code` None means involved but left the verdict alone (a reviewer
    that only set a title). The tuple order in `Result.audit` is
    execution order, which is also decision 9's reviewer order,
    documented by the data itself."""

    moment: Phase
    actor: str | None
    code: int | None


class Result(int):
    """The committed record IS its exit code (I2): an int subclass, so
    every consumer that read codes keeps reading them. Extends the
    shipped `run()` Result shape (command/stdout/stderr/duration/raw)
    with the record surface the spec adds (title, address, failed_at).
    Immutable — statically: every field is a read-only property, and
    this is the ONLY type an observer ever holds (ruled 2026-07-31), so
    "observers see, never judge" is unspellable rather than enforced.
    """

    @property
    def title(self) -> str:
        raise NotImplementedError

    @property
    def command(self) -> str:
        raise NotImplementedError

    @property
    def stdout(self) -> str:
        raise NotImplementedError

    @property
    def stderr(self) -> str:
        raise NotImplementedError

    @property
    def duration(self) -> float:
        raise NotImplementedError

    @property
    def raw(self) -> str:
        raise NotImplementedError

    @property
    def address(self) -> Address:
        raise NotImplementedError

    @property
    def code(self) -> int:
        raise NotImplementedError

    @property
    def ok(self) -> bool:
        raise NotImplementedError

    @property
    def audit(self) -> tuple[AuditEntry, ...]:
        """The verdict's provenance, the storage truth (decision 10):
        every lifecycle moment that acted on or failed the grain, in
        execution order. The body/capture entry always enters with the
        work's own raw code; a failing moment always enters, declared
        or not; quiet undeclared moments are skipped; verdict-scope
        only (no title-diff log)."""
        raise NotImplementedError

    @property
    def failed_at(self) -> Phase | None:
        """The lifecycle moment the grain failed at, None on success —
        a derived reading of the audit (the failing entry's moment),
        kept as a property so the common question needs no scan. A
        raising hook fails the grain like any other error, tagged with
        ITS moment; no hook ever rewrites a verdict."""
        raise NotImplementedError

    @property
    def work_code(self) -> int | None:
        """The code the grain carried when the failing moment began —
        a vetoed green shows its 0 here. A derived reading of the
        audit (the last code-bearing entry before the failure), not a
        stored field; the old naming question is moot (register)."""
        raise NotImplementedError


class ResultView:
    """THE view — one type across grains (ruled 2026-07-31): the
    record's draft, `Result`'s only mutable phase.

    This type IS the review window: `title` and `code` are plain
    writable attributes (the maker's `pre_record`, the item's own
    `with`-handle, and the generator's sent-in view all write them).
    `ok` DERIVES from `code` — a read-only property, so `code = 1` with
    `ok = True` is unspellable; the verdict follows the code (I2).
    What was captured is read-only everywhere: review sees what the run
    kept, never edits it (walk 2). Observers never hold this type at
    all (ruled 2026-07-31): `post_task` receives the immutable
    `Result`, so the phase gate is the type split, not enforcement.
    `returned` is on the shared view, None by circumstance (I10);
    `set_returned` is the review-window display write — per-maker,
    contract-aware shaping of the reported value; its old observer-
    phase home is gone, and the contract-free global scrub it never
    soundly delivered belongs to display policy (decision 4).
    """

    title: str
    code: int

    @property
    def ok(self) -> bool:
        raise NotImplementedError

    @property
    def stdout(self) -> str:
        raise NotImplementedError

    @property
    def stderr(self) -> str:
        raise NotImplementedError

    @property
    def duration(self) -> float:
        raise NotImplementedError

    @property
    def raw(self) -> str:
        raise NotImplementedError

    @property
    def address(self) -> Address:
        raise NotImplementedError

    @property
    def returned(self) -> object:
        raise NotImplementedError

    def set_returned(self, value: object) -> None:
        raise NotImplementedError


class RecordHook(Protocol):
    """A reviewer: receives the draft in the review window (I5). A
    raising reviewer fails the item with the hook's error (walk 2)."""

    def __call__(self, view: ResultView, /) -> None: ...


# --- the yield vocabulary ----------------------------------------------

# Bare `yield` = checkpoint; every yield evaluates to the item's
# ResultView (the `result = yield` idiom wrap_task ships). The None
# yield-type makes "yielding a value" a STATIC error — the taught error
# is structural, not just taught (move-3 finding; negative file).
StepBody: TypeAlias = Generator[None, ResultView, R]


# --- the substrate and its policy surfaces (I13) -----------------------


class WorkItem(Generic[R_co]):
    """An unstarted piece of managed work with a record to come. Building
    one runs nothing — the `range(10)` precedent, owned: the expression's
    static type says work-item-not-result."""


class Lane:
    """A serialised claim on a named resource — process globals and, by
    ruling, custom user resources. Lanes appear only in declaration
    surfaces (opts below): acquisition is boundary-atomic (I8), so a
    mid-body acquire has no spelling on this surface at all."""

    name: str


def lane(name: str) -> Lane:
    raise NotImplementedError


class ExecutionOpts(TypedDict, total=False):
    """Execution policy: item-general (I13) — every grain accepts these."""

    cwd: str
    env: dict[str, str]
    timeout: float
    lanes: tuple[Lane, ...]
    capture: bool


class StepOpts(ExecutionOpts, total=False):
    """What an undeclared item can be told. `recorded` lives HERE and not
    on TaskOpts: declared ⟹ recorded (walk 4), so at task grain the
    keyword does not exist — the taught error is structural."""

    recorded: bool
    pre_record: RecordHook


class BoundaryOpts(TypedDict, total=False):
    """Boundary policy: resolves at the request boundary, so it exists
    exactly where a declaration does (I13, walk 4)."""

    confirm: str
    shared: bool
    keep_going: bool


class TaskOpts(ExecutionOpts, BoundaryOpts, total=False):
    """Full policy: execution + boundary, plus declared-grain review.
    No `recorded` key — see StepOpts."""

    pre_record: RecordHook


class StepFn(Protocol[P, R_co]):
    """What a lifter returns. Calling it BUILDS the bound, deferrable
    item — where a TaskFn call RUNS (a body call is a request). The
    build/run asymmetry is the declared/deferred seam, stated in the
    types."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> WorkItem[R_co]: ...
    def opts(self, **overrides: Unpack[StepOpts]) -> StepFn[P, R_co]: ...


class TaskFn(Protocol[P, R_co]):
    """Restates the shipped TaskFn Protocol (registry.py), narrowed to
    what move 3 needs: the call keeps the task's own signature; `.opts()`
    takes the full policy set."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co: ...
    def opts(self, **overrides: Unpack[TaskOpts]) -> TaskFn[P, R_co]: ...


def task(fn: Callable[P, R], /) -> TaskFn[P, R]:
    raise NotImplementedError


# --- the lifters -------------------------------------------------------

# One name, three grammatical positions (settled 2026-07-31). The
# decorator and expression positions are the SAME expression `step(fn)`
# — Python cannot tell them apart — so both return the maker, never a
# built item: decision 8's cheap spelling hands parallel() a maker
# (move-3 finding).


@overload
def step(
    fn: Callable[P, StepBody[R]], /, *, title: str | None = None
) -> StepFn[P, R]: ...
@overload
def step(fn: Callable[P, R], /, *, title: str | None = None) -> StepFn[P, R]: ...
@overload
def step(title: str, /) -> AbstractContextManager[ResultView]: ...
def step(target: object = None, /, *, title: str | None = None) -> object:
    raise NotImplementedError


def pre_record(hook: RecordHook, /) -> Callable[[F], F]:
    """Stacked attachment for makers — identity-typed like the gates, so
    it reads order-free above or below the lifter (decision 2's typing
    residue, answered: `Callable[[F], F]` preserves a plain function, a
    StepFn, and a TaskFn alike)."""
    raise NotImplementedError


# --- observation (ruled 2026-07-31: purely read-only) ------------------


class ObserverHook(Protocol):
    """`post_task`, narrowed to the record argument (inv/task ride the
    shipped signature unchanged): observation holds the immutable
    `Result` — judging is unspellable, not discouraged. A raising
    observer is an error like any other error: the grain fails, tagged
    `failed_at="observe"` by the machinery, never by a write."""

    def __call__(self, result: Result, /) -> None: ...


def post_task(hook: ObserverHook, /) -> ObserverHook:
    raise NotImplementedError


def fail(reason: str, code: int = 1) -> NoReturn:
    """Restates the shipped blessed failure verb. In an observer it is
    the VETO: it rides the error channel — loud, attributed to the
    observe moment (`failed_at="observe"`), its code the grain's final
    int (I2) — never a forged verdict: observers may veto, never
    forge. `code=0` is a taught error EVERYWHERE (ruled, move-4
    follow-up: fail is the failure verb) — a runtime refusal, since
    int carries no nonzero static type for the loom to lean on."""
    raise NotImplementedError


# --- the tools bridge sliver (attachment-as-dispatch, I1) --------------


class Tool:
    """Just enough of the bridge to type walk 2: a tool's calls make
    steps, so per-tool policy is StepOpts, and `pre_record` rides it."""

    def opts(self, **overrides: Unpack[StepOpts]) -> Self:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Callable[..., Result]:
        raise NotImplementedError


def tool(name: str) -> Tool:
    raise NotImplementedError


# --- execution and fan-out ---------------------------------------------


def run(
    command: str,
    *,
    title: str | None = None,
    capture: bool = True,
    timeout: float | None = None,
    cwd: str | None = None,
    recorded: bool = True,
) -> Result:
    """The default composition of execution and record (I4);
    `recorded=False` is execution alone — off the record. `command` is a
    str only: `run(callable)` retires under decision 8's ban (the lifted
    spelling replaces it); that narrowing is conditional on decision 8."""
    raise NotImplementedError


class Fanout(list[Result]):
    """The block form of `parallel()`: an anonymous grouping item whose
    children are the fanned items (walk 1) — just an anonymous parent.
    A list of committed records underneath (walk 1's promotion: Results
    over codes; I2 keeps every code-reader working). Owned calls inside
    the block queue themselves, carrying arguments naturally; `p.also`
    does not exist here — under decision 8's ban foreign code lifts
    instead (conditional on that decision)."""

    results: list[object]

    def __enter__(self) -> Self:
        raise NotImplementedError

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        raise NotImplementedError


@overload
def parallel(*, keep_going: bool = False) -> Fanout: ...
@overload
def parallel(
    *work: WorkItem[Any] | StepFn[..., Any] | TaskFn[..., Any],
    keep_going: bool = False,
) -> list[Result]: ...
def parallel(
    *work: WorkItem[Any] | StepFn[..., Any] | TaskFn[..., Any],
    keep_going: bool = False,
) -> object:
    """Takes work items and makers only — the acceptance test at full
    strength, structural because both maker Protocols demand `.opts`:
    a bare lambda fails to match (conditional on decision 8)."""
    raise NotImplementedError


# =======================================================================
# Consumer exercises: the walks, retyped. Everything below must check
# as written — these are the fragments the loom forces to meet.
# =======================================================================


# --- walk 2: the djlint gate — one line and a reviewer -----------------


def dj_outcome(view: ResultView) -> None:
    text = view.stdout + view.stderr  # review sees what was captured
    if "reformatted" in text or "0 files" in text:
        view.title = "djlint: reformatted"
        view.code = 0  # the verdict is the code; ok follows (I2)
    else:
        view.title = "djlint: needs formatting"


def djlint_gate() -> None:
    djlint = tool("djlint")
    outcome = djlint.opts(pre_record=dj_outcome).reformat("templates/")
    assert_type(outcome, Result)


# --- the lifters and the yield vocabulary ------------------------------


@step
def covered() -> int:
    raise NotImplementedError


@step
def fmt_html(target: str) -> StepBody[int]:
    view = yield  # every yield evaluates to the item's view
    assert_type(view, ResultView)
    view.title = f"fmt {target}"  # a title decided mid-work: plain write
    yield  # bare checkpoint: a cancellation window
    return 0


def building_is_not_running() -> None:
    item = fmt_html("docs/")  # builds the bound item; runs nothing
    assert_type(item, WorkItem[int])
    assert_type(covered(), WorkItem[int])


def observed_block() -> None:
    with step("prepare fixtures") as s:
        assert_type(s, ResultView)
        s.title = "prepared 3 fixtures"  # self-review via the own handle


def expression_lift() -> None:
    def sweep() -> None:
        raise NotImplementedError

    lifted = step(sweep, title="sweep tmp")
    assert_type(lifted, StepFn[[], None])


# --- pre_record stacks order-free, on steps and tasks alike ------------


def tidy(view: ResultView) -> None:
    view.title = view.title.strip()


@pre_record(tidy)
@step
def above() -> None:
    raise NotImplementedError


@step
@pre_record(tidy)
def below() -> None:
    raise NotImplementedError


@pre_record(tidy)
@task
def lint(fix: bool = False) -> int:
    raise NotImplementedError


def stacking_kept_every_shape() -> None:
    assert_type(above(), WorkItem[None])
    assert_type(below(), WorkItem[None])
    assert_type(lint(fix=True), int)


# --- walk 1: footman's own check, under the model + the ban ------------


@task
def format(check: bool = False) -> int:
    raise NotImplementedError


@task
def typecheck() -> None:
    raise NotImplementedError


def check_direct() -> None:
    results = parallel(lint, typecheck, covered())
    assert_type(results, list[Result])
    for r in results:
        assert_type(r.ok, bool)  # Results read backward as codes (I2)
        assert_type(r.code, int)


def check_block() -> None:
    with parallel() as p:
        format(check=True)  # owned calls carry arguments naturally
        typecheck()
    assert_type(p[0], Result)  # the block IS its list of records


def policy_split_in_use() -> None:
    # boundary policy exists only at declared grain; execution policy is
    # item-general — both on TaskOpts, only the latter on StepOpts.
    opted = lint.opts(confirm="rewrites history", cwd="sub")
    assert_type(opted(fix=True), int)  # an opted call checks like a bare one
    pinned = covered.opts(lanes=(lane("cwd"),), recorded=False)
    assert_type(pinned(), WorkItem[int])
    reviewed = typecheck.opts(pre_record=tidy)
    assert_type(reviewed(), None)


# --- observation: read everything, write nothing -----------------------


@post_task
def triage(result: Result) -> None:
    if not result.ok:
        assert_type(result.failed_at, Phase | None)  # which moment broke
        assert_type(result.work_code, int | None)  # a vetoed green shows its 0
        assert_type(result.stderr, str)
        assert_type(result.address, Address)
        for moment, actor, code in result.audit:  # the whole verdict story
            assert_type(moment, Phase)
            assert_type(actor, str | None)
            assert_type(code, int | None)


@post_task
def budget(result: Result) -> None:
    # The veto: a policy gate at the observe moment fails the grain
    # through the error channel — attributed, never a record write.
    if result.duration > 60.0:
        fail(f"duration budget exceeded: {result.duration:.0f}s")


# --- off the record: how a task learns something (I4) ------------------


def learn() -> str:
    head = run("git rev-parse HEAD", recorded=False)
    return head.stdout.strip()


# --- walk 6: the horizon sketch — typed returns meet the I6 key --------


@task
def parse(source: str = "data.csv") -> list[dict[str, str]]:
    raise NotImplementedError


def summarise() -> int:
    rows = parse()  # a request; two consumers share one execution (I6)
    assert_type(rows, list[dict[str, str]])
    return len(rows)
