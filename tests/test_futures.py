"""Body calls as run-scoped futures: memo, waiting, refusals, `volatile`."""

from __future__ import annotations

import threading

import pytest

from footman import registry
from footman.registry import Group
from footman.split import ChainError
from footman.testing import Runner


def drive(reg: Group, line: str):
    """Run *line* against *reg* through the in-process CLI."""
    return Runner().invoke(line, tasks=reg)


def test_a_body_call_shares_the_runs_execution():
    # `pre=[build]` then `build()` in the body is ONE build: the prerequisite's
    # pristine return is memoised under (task, arguments), so the call is a
    # cache hit rather than a second execution.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build() -> str:
        runs.append("build")
        return "dist/app"

    @reg.task(pre=[build])
    def publish():
        artifact = build()  # the value pre= could never hand over
        print(f"published {artifact}")

    result = drive(reg, "publish")
    assert result.ok, result.stderr
    assert runs == ["build"]  # once, not twice
    assert "published dist/app" in result.stdout


def test_a_body_call_without_the_task_in_the_plan_runs_it():
    # No prerequisite: the call is the first execution, so it happens — and
    # returns its value like a plain call always did.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def measure() -> int:
        runs.append("measure")
        return 7

    @reg.task
    def report():
        print(f"got {measure()}")

    result = drive(reg, "report")
    assert result.ok, result.stderr
    assert runs == ["measure"] and "got 7" in result.stdout


def test_the_memo_keys_on_arguments_not_just_the_task():
    # Same task, different arguments, is different work; the same arguments
    # spelled positionally or by keyword is the same work.
    reg = Group("root")
    seen: list[str] = []

    @reg.task
    def render(target: str = "web") -> str:
        seen.append(target)
        return target.upper()

    @reg.task
    def build_all():
        assert render("web") == "WEB"
        assert render(target="web") == "WEB"  # same work, other spelling
        assert render("api") == "API"  # different work
        assert render() == "WEB"  # the default is the same work as "web"

    assert drive(reg, "build-all").ok
    assert seen == ["web", "api"]


def test_a_volatile_task_executes_on_every_call():
    reg = Group("root")
    runs: list[int] = []

    @reg.task(volatile=True)
    def notify() -> None:
        runs.append(1)

    @reg.task
    def ship():
        notify()
        notify()
        notify()

    assert drive(reg, "ship").ok
    assert len(runs) == 3


def test_a_volatile_prerequisite_is_never_shared():
    # Volatile means "not shared", and that is one rule for every spelling:
    # two dependents each get their own run, exactly as two calls would. No
    # one has to remember whether they reached the task by declaration or by
    # call.
    reg = Group("root")
    runs: list[int] = []

    @reg.task(volatile=True)
    def stamp() -> None:
        runs.append(1)

    @reg.task(pre=[stamp])
    def build_web(): ...

    @reg.task(pre=[stamp])
    def build_api(): ...

    assert drive(reg, "build-web build-api").ok
    assert len(runs) == 2  # one per requester


def test_volatility_propagates_down_the_subtree():
    # "Give me a fresh build" has to mean its inputs are fresh too, or fresh
    # is a half-truth: the property flows from a requester into what it needs.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def compile_() -> None:
        runs.append("compile")

    @reg.task(pre=[compile_])
    def bundle() -> None:
        runs.append("bundle")

    @reg.task(volatile=True, pre=[bundle])
    def release_() -> None:
        runs.append("release")

    @reg.task(pre=[bundle])
    def preview() -> None:
        runs.append("preview")

    assert drive(reg, "release preview").ok  # `release_` addresses as `release`
    # `release-` was requested freshly, so its bundle (and that bundle's
    # compile) are its own; `preview` gets the shared pair.
    assert runs.count("bundle") == 2
    assert runs.count("compile") == 2


def test_an_own_declaration_beats_an_inherited_one():
    # The pin for an expensive step that genuinely is reusable: declaring
    # volatile=False keeps it shared even under a freshly-requested parent.
    reg = Group("root")
    runs: list[str] = []

    @reg.task(volatile=False)
    def fetch_deps() -> None:
        runs.append("fetch")

    @reg.task(volatile=True, pre=[fetch_deps])
    def build_web() -> None: ...

    @reg.task(volatile=True, pre=[fetch_deps])
    def build_api() -> None: ...

    assert drive(reg, "build-web build-api").ok
    assert runs.count("fetch") == 1  # shared despite two volatile parents


def test_a_per_call_override_asks_for_one_fresh_run():
    # `.opts(volatile=True)` is the per-request spelling — it replaces the
    # `fresh()` idea, and works on a declared edge just as well as on a call.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def stamp() -> int:
        runs.append(1)
        return len(runs)

    @reg.task
    def go():
        first = stamp()  # runs, and fills the cell
        again = stamp()  # shared: the cell answers
        fresh = stamp.opts(volatile=True)()  # asked freshly: runs again
        assert (first, again, fresh) == (1, 1, 2)

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_the_first_result_is_the_one_the_run_remembers():
    # First-write-wins: a fresh re-run gets its own value, but never rewrites
    # what the run already remembers, so a later shared request is stable.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def stamp() -> int:
        runs.append(1)
        return len(runs)

    @reg.task
    def go():
        assert stamp() == 1
        assert stamp.opts(volatile=True)() == 2  # its own, fresh value
        assert stamp() == 1  # …and the remembered first result stands

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_a_failing_callee_raises_in_the_caller():
    reg = Group("root")

    @reg.task
    def flaky() -> None:
        raise RuntimeError("boom")

    @reg.task
    def caller():
        flaky()

    result = drive(reg, "caller")
    assert not result.ok
    assert "boom" in result.stderr


def test_a_body_call_is_reported_as_its_own_unit():
    # The body-call hole in the result table closes: the callee ran as a real
    # task, so it has a TaskResult of its own.
    reg = Group("root")

    @reg.task
    def inner() -> str:
        return "v1"

    @reg.task
    def outer():
        inner()

    result = drive(reg, "outer")
    assert result.ok, result.stderr
    assert [r.task for r in result.results] == ["outer", "inner"]
    assert next(r for r in result.results if r.task == "inner").returned == "v1"


def test_self_recursion_is_a_taught_refusal():
    # Today this is a stack overflow; as a future it is a wait on itself,
    # caught and named.
    reg = Group("root")

    @reg.task
    def loop():
        loop()

    result = drive(reg, "loop")
    assert not result.ok
    assert "can never return" in result.stderr and "loop" in result.stderr


def test_a_call_cycle_between_two_tasks_is_taught():
    reg = Group("root")

    @reg.task
    def ping():
        pong()

    @reg.task
    def pong():
        ping()

    result = drive(reg, "ping")
    assert not result.ok
    assert "can never return" in result.stderr


def test_calling_a_serial_task_from_a_body_is_refused():
    # The arbiter lane is acquired at the task boundary, never mid-body —
    # the invariant that keeps it deadlock-free. The refusal names the fix.
    reg = Group("root")

    @reg.task(serial=True)
    def migrate(): ...

    @reg.task
    def deploy():
        migrate()

    result = drive(reg, "deploy")
    assert not result.ok
    assert "serial/exclusive lane" in result.stderr
    assert "pre=[migrate]" in result.stderr


def test_calling_an_infinite_task_from_a_body_is_refused():
    reg = Group("root")

    @reg.task(infinite=True)
    def serve(): ...

    @reg.task
    def dev():
        serve()

    result = drive(reg, "dev")
    assert not result.ok
    assert "infinite task" in result.stderr


def test_a_call_outside_a_run_is_a_plain_call():
    # Importing a tasks file and calling a function must keep working: no run,
    # no machinery, no context — just the function.
    reg = Group("root")

    @reg.task
    def double(n: int = 2) -> int:
        return n * 2

    assert double(21) == 42


def test_unhashable_arguments_run_rather_than_pretend_to_be_cached():
    # A value with no frozen form can't key a cell. The call runs — honest
    # work every time — instead of guessing at a cache hit.
    reg = Group("root")
    runs: list[int] = []

    class Opaque:
        __hash__ = None  # type: ignore[assignment]

    @reg.task
    def consume(thing: object = None) -> None:
        runs.append(1)

    @reg.task
    def go():
        blob = Opaque()
        consume(blob)
        consume(blob)

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_list_arguments_key_by_value():
    # Collections get a frozen shape, so the same list contents are the same
    # work — the common case for a `list[str]` parameter.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def lint(paths: list[str] | None = None) -> int:
        runs.append(1)
        return len(paths or [])

    @reg.task
    def gate():
        assert lint(["a", "b"]) == 2
        assert lint(["a", "b"]) == 2  # same contents: the memo answers

    assert drive(reg, "gate").ok
    assert len(runs) == 1


def test_two_threads_calling_one_task_share_a_single_execution():
    # The second caller blocks on the first's future rather than duplicating
    # the work; both see the same value.
    reg = Group("root")
    runs: list[int] = []
    seen: list[str] = []

    @reg.task
    def slow() -> str:
        runs.append(1)
        threading.Event().wait(0.05)
        return "once"

    @reg.task
    def fan():
        from footman import parallel

        def one() -> None:
            seen.append(slow())

        parallel(one, one, one)

    assert drive(reg, "fan").ok
    assert len(runs) == 1
    assert seen == ["once", "once", "once"]


def test_volatility_is_a_tri_state():
    # Unset is a third state, not False: it means "whoever asks decides", which
    # is what lets the property propagate and what makes volatile=False a
    # deliberate pin rather than a no-op.
    reg = Group("root")

    @reg.task(volatile=True)
    def always(): ...

    @reg.task(volatile=False)
    def never(): ...

    @reg.task
    def unset(): ...

    assert registry.volatility(always) is True
    assert registry.volatility(never) is False
    assert registry.volatility(unset) is None
    # `.opts()` overrides the declaration for one request.
    assert registry.volatility(unset.opts(volatile=True)) is True
    assert registry.volatility(always.opts(volatile=False)) is False


def test_the_machinery_refuses_before_it_memoises():
    # A refusal is not an execution: nothing is cached, so the second call
    # teaches the same lesson rather than silently returning None.
    reg = Group("root")

    @reg.task(serial=True)
    def locked(): ...

    with pytest.raises(ChainError):
        from footman import _futures

        with _futures.session():
            _futures.call(locked, (), {})
