"""The `pre_tasks` lifecycle hook: the invocation, before anything runs."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Annotated

import pytest

from footman import discover, manifest, registry
from footman.params import between, check, env
from footman.registry import Group, RegistrationError
from footman.testing import Runner


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def test_a_hook_edits_the_merged_tree(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @task
        def deploy_web(): ...

        @footman.pre_tasks
        def gate(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if t.name.startswith("deploy"):
                    t.add_pre(audit)
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["audit"].fn in view["deploy-web"].pre


def test_a_hook_reaches_a_subfolder_task(tmp_path):
    # A ROOT hook edits a task defined in a subfolder's file — the whole
    # merged tree, not just its own file.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate(inv):
            if "ship" in inv.tasks:
                inv.tasks["ship"].add_pre(inv.tasks["audit"])
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from footman import task

        @task
        def ship(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([root, sub]))
    assert view["audit"].fn in view["ship"].pre


def test_hooks_run_in_cascade_order_specific_last(tmp_path):
    # Root's hook runs first, the folder nearest cwd last — the same
    # "local overrides global" precedence the task cascade uses.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "root"]
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        import footman

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "svc"]
        """,
    )
    tree = discover.load_tree([root, sub])
    assert getattr(tree.tasks["x"], "_order") == ["root", "svc"]


def test_a_hook_that_raises_is_named(tmp_path):
    bad = _write(
        tmp_path / "tasks.py",
        """
        import footman

        @footman.pre_tasks
        def boom(inv):
            raise ValueError("nope")
        """,
    )
    with pytest.raises(discover.HookError, match="boom"):
        discover.load_tree([bad])


def test_tasks_view_iterates_nested_tasks(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task, group

        @task
        def a(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert sorted(t.name for t in view) == ["a", "build"]


def test_a_hook_disable_reaches_the_manifest(tmp_path):
    # Finalizers run at discovery, before the manifest is built — so a
    # `disable()` shows up in the baked node (`disabled`).
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["x"].disable("off by policy")
        """,
    )
    tree = discover.load_tree([src])
    node = manifest.build_manifest(tree)["tree"]["tasks"]["x"]
    assert node["disabled"] == "off by policy"


def test_task_view_add_post_and_read_post(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def deploy(): ...

        @task
        def notify(): ...

        @footman.pre_tasks
        def wire(inv):
            inv.tasks["deploy"].add_post(inv.tasks["notify"])
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["notify"].fn in view["deploy"].post
    assert "missing" not in view
    with pytest.raises(KeyError):
        _ = view["missing"]


def test_task_view_disabled_reads_the_reason(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def a(): ...

        @task
        def b(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["a"].disable("off by policy")
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["a"].disabled == "off by policy"
    assert view["b"].disabled is None


def test_task_view_reads_policy_flags(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task(keep_going=False, atomic=True, confirm="sure?")
        def gated(): ...

        @task
        def plain(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    gated, plain = view["gated"], view["plain"]
    assert gated.keep_going is False
    assert gated.atomic is True
    assert gated.confirm == "sure?"
    # A plain task reports the neutral defaults, not None-vs-missing noise.
    assert plain.keep_going is None
    assert plain.atomic is False
    assert plain.infinite is False
    assert plain.interactive is False
    assert plain.timed is True
    assert plain.confirm == ""


def test_task_view_infinite_is_untimed(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task(infinite=True)
        def serve(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["serve"].infinite is True
    assert view["serve"].timed is False  # infinite implies no timing history


def test_task_view_owning_group(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task, group

        @task
        def top(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["top"].group is None  # top-level task is in no named group
    assert view["build"].group is not None
    assert view["build"].group.name == "docs"


def test_task_view_provenance_single_file(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task
        def x(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    x = view["x"]
    assert x.defining_dir == str(src.parent)
    assert x.source_file is not None and x.source_file.endswith("tasks.py")
    assert x.shadowed is None
    assert x.shadow_chain == (x.fn,)


def test_task_view_shadow_chain_across_cascade(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        """
        from footman import task

        @task
        def x():
            "root version"
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from footman import task

        @task
        def x():
            "svc version"
        """,
    )
    view = registry.Tasks(discover.load_tree([root, sub]))
    x = view["x"]  # the winning (nearest-cwd) definition
    assert x.defining_dir == str(sub.parent)
    assert x.fn.__doc__ == "svc version"
    assert x.shadowed is not None and x.shadowed.__doc__ == "root version"
    chain = x.shadow_chain
    assert [t.__doc__ for t in chain] == ["svc version", "root version"]


def test_task_view_set_opts_is_permanent(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def policy(inv):
            inv.tasks["x"].set_opts(keep_going=False, atomic=True)
        """,
    )
    view = registry.Tasks(discover.load_tree([src]))
    assert view["x"].keep_going is False
    assert view["x"].atomic is True
    # It writes the same attributes the policy accessors read.
    assert registry.keeps_going(view["x"].fn) is False


def test_task_view_set_opts_rejects_a_task_parameter(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def bad(inv):
            inv.tasks["x"].set_opts(fix=True)  # a task parameter, not a policy option
        """,
    )
    # `set_opts` reuses `.opts()`'s validation, so a stray parameter is a taught
    # error surfaced (named) through the hook.
    with pytest.raises(discover.HookError, match="bad"):
        discover.load_tree([src])


def test_a_hook_uses_defining_dir_for_a_cascade_decision(tmp_path):
    # The motivating case: gate every task defined under an `infra/` folder,
    # deciding purely from provenance the hook reads off the view.
    root = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate_infra(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if (t.defining_dir or "").endswith("infra"):
                    t.add_pre(audit)
        """,
    )
    infra = _write(
        tmp_path / "infra" / "tasks.py",
        """
        from footman import task

        @task
        def deploy(): ...
        """,
    )
    view = registry.Tasks(discover.load_tree([root, infra]))
    assert view["audit"].fn in view["deploy"].pre


# --- what the invocation adds over the tree view it replaced ------------------


def test_a_hook_sets_the_environment_every_task_sees(tmp_path, monkeypatch):
    # The moment is pre-DAG and single-threaded, so os.environ is ordinary code
    # here — and it lands before availability gates, which is the point: a
    # requires_env task is available because a hook supplied the variable.
    monkeypatch.delenv("LIFECYCLE_TOKEN", raising=False)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import os
            import footman
            from footman import task, requires_env

            @footman.pre_tasks
            def supply(inv):
                os.environ["LIFECYCLE_TOKEN"] = "from-the-hook"

            @task
            @requires_env("LIFECYCLE_TOKEN", reason="needs a token")
            def publish():
                print("token:", os.environ["LIFECYCLE_TOKEN"])
            """
        )
    )
    result = Runner().invoke("publish", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "token: from-the-hook" in result.stdout


def test_the_invocation_carries_what_the_line_asked(tmp_path):
    seen: dict[str, object] = {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import json, pathlib
            import footman
            from footman import task

            @footman.pre_tasks
            def record(inv):
                pathlib.Path("seen.json").write_text(json.dumps({
                    "keep_going": inv.cli.get("keep_going"),
                    "cwd_is_set": bool(inv.cwd),
                    "root_is_set": bool(inv.root),
                    "task_names": sorted(t.name for t in inv.tasks),
                }))

            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("-k build", cwd=tmp_path)
    assert result.ok, result.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["keep_going"] is True  # the line's own globals
    assert seen["cwd_is_set"] and seen["root_is_set"]
    assert seen["task_names"] == ["build"]


def test_a_hook_with_the_wrong_arity_is_refused_at_registration():
    # The lifecycle names its moments and they differ by arity, so a typo is a
    # taught error naming the hook rather than a TypeError at the first task.
    reg = Group("root")

    with pytest.raises(RegistrationError, match=r"pre_tasks is handed 1"):

        @reg.pre_tasks
        def no_args(): ...

    with pytest.raises(RegistrationError, match=r"takes 2 argument"):

        @reg.pre_tasks
        def too_many(inv, extra): ...


def test_finalize_is_gone():
    # Removed outright rather than kept as a refusing shim: the lifecycle has
    # one name per moment, and a retired alias is a second one.
    import footman

    reg = Group("root")
    assert not hasattr(reg, "finalize")
    assert not hasattr(footman, "finalize")
    assert "finalize" not in footman.__all__


def test_the_invocation_refuses_writes_once_frozen():
    from footman.invocation import Frozen, Invocation

    inv = Invocation(cwd="/tmp")
    inv.root = "/tmp"  # editable at pre_tasks
    inv.freeze()
    with pytest.raises(Frozen, match=r"editable only at pre_tasks"):
        inv.root = "/elsewhere"


# --- the per-task ladder: pre_task / post_task -------------------------------


def test_pre_and_post_fire_around_every_execution():
    # A chain segment, a prerequisite and a body call are all executions, and
    # each gets the pair. pre_task fires post-bind, so `task.args` holds what
    # the body receives — defaults included.
    reg = Group("root")
    log: list[tuple] = []

    @reg.pre_task
    def opened(inv, task):
        log.append(("pre", task.name, dict(task.args)))

    @reg.post_task
    def closed(inv, task, result):
        log.append(("post", task.name, result.ok))

    @reg.task
    def build(target: str = "web") -> str:
        return target.upper()

    @reg.task(pre=[build])
    def publish():
        build()  # the shared execution: no second pair

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        ("pre", "build", {"target": "web"}),
        ("post", "build", True),
        ("pre", "publish", {}),
        ("post", "publish", True),
    ]


def test_task_state_is_private_to_the_plugin_and_the_execution():
    # Two plugins each get their own namespace on the same execution, and the
    # same plugin gets a fresh one per execution — nothing leaks sideways or
    # forward.
    reg = Group("root")
    seen: list[tuple] = []

    def a_pre(inv, task):
        assert not vars(task.state)  # fresh per execution
        task.state.token = f"a:{task.name}"

    def a_post(inv, task, result):
        seen.append(("a", task.name, task.state.token, vars(task.state)))

    def b_pre(inv, task):
        assert not vars(task.state)  # b never sees a's writes
        task.state.token = f"b:{task.name}"

    def b_post(inv, task, result):
        seen.append(("b", task.name, task.state.token))

    for fn in (a_pre, a_post):
        fn.__module__ = "plugin_a"
    for fn in (b_pre, b_post):
        fn.__module__ = "plugin_b"
    reg.pre_task(a_pre)
    reg.post_task(a_post)
    reg.pre_task(b_pre)
    reg.post_task(b_post)

    @reg.task
    def one(): ...

    @reg.task
    def two(): ...

    result = Runner().invoke("--sequential one two", tasks=reg)
    assert result.ok, result.stderr
    # Posts unwind in reverse plugin order: b speaks first, a last.
    assert seen == [
        ("b", "one", "b:one"),
        ("a", "one", "a:one", {"token": "a:one"}),
        ("b", "two", "b:two"),
        ("a", "two", "a:two", {"token": "a:two"}),
    ]


def test_a_raising_pre_fails_the_task_and_skips_the_body():
    # A raising pre fails the task and the body never runs — but a post is
    # the task-finished event: once the execution reached the body stage,
    # every registered post fires when it concludes, the raiser's own
    # included, irrespective of how any pre fared. The failure names the
    # plugin and the hook.
    reg = Group("root")
    log: list[str] = []
    ran: list[str] = []

    def a_pre(inv, task):
        log.append("a:pre")

    def a_post(inv, task, result):
        log.append(f"a:post ok={result.ok}")

    def b_pre(inv, task):
        raise ValueError("no thanks")

    def b_post(inv, task, result):
        log.append(f"b:post ok={result.ok}")  # fires: its span still closes

    for fn in (a_pre, a_post):
        fn.__module__ = "plugin_a"
    for fn in (b_pre, b_post):
        fn.__module__ = "plugin_b"
    reg.pre_task(a_pre)
    reg.post_task(a_post)
    reg.pre_task(b_pre)
    reg.post_task(b_post)

    @reg.task
    def build():
        ran.append("body")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert ran == []  # a raising pre fails the task like a failed pre-dep
    assert log == ["a:pre", "b:post ok=False", "a:post ok=False"]
    assert "pre_task hook 'b_pre' from plugin_b failed" in result.stderr
    assert "ValueError: no thanks" in result.stderr


def test_a_raising_post_fails_a_green_task():
    reg = Group("root")

    @reg.pre_task
    def opened(inv, task): ...

    @reg.post_task
    def crash(inv, task, result):
        raise RuntimeError("reporter went down")

    @reg.task
    def build() -> str:
        return "fine"

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok  # a reporter that crashed must not pass silently
    assert "post_task hook 'crash'" in result.stderr
    assert "reporter went down" in result.stderr


def test_a_post_failure_never_masks_the_bodys_own():
    # Context-manager semantics: a cleanup error during an exception loses to
    # the original.
    reg = Group("root")

    @reg.post_task
    def crash(inv, task, result):
        raise RuntimeError("reporter went down")

    @reg.task
    def build():
        raise ValueError("the real failure")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "the real failure" in result.stderr


def test_set_returned_rewrites_the_report_never_the_value():
    # The pristine return was snapshotted at the body's exit: a dependent's
    # body call still receives it, while the report and `--json` carry the
    # rewrite.
    reg = Group("root")

    @reg.post_task
    def redact(inv, task, result):
        if task.name == "build":
            result.set_returned("[redacted]")

    @reg.task
    def build() -> str:
        return "secret-artifact"

    @reg.task(pre=[build])
    def publish():
        assert build() == "secret-artifact"  # the value, not the report

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    build_rows = [r.returned for r in result.results if r.task == "build"]
    # The execution's report carries the rewrite. The `shared` row that the
    # body call added reports what its requester actually received — the
    # pristine value, deliberately: sharing hands over the real object, and
    # the row records that handover rather than the reporter's edit.
    assert build_rows == ["[redacted]", "secret-artifact"]


def test_a_pre_returning_a_value_is_noted_as_reserved():
    # The return channel is reserved for a future "supply the result, skip
    # the body" power — today it is a note, never a failure.
    reg = Group("root")

    @reg.pre_task
    def eager(inv, task):
        return "something"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    text = result.stdout + result.stderr
    assert "reserved" in text
    assert "task.state" in text


def test_env_written_in_pre_reaches_the_body():
    # task.env is the task's own overlay: the environ router serves it to
    # in-body reads, and it never touches the process globals.
    import os as real_os

    reg = Group("root")

    @reg.pre_task
    def inject(inv, task):
        task.env["HOOKED_VALUE"] = "from-pre"

    @reg.task
    def build():
        import os

        print(os.environ.get("HOOKED_VALUE", "missing"))

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert "from-pre" in result.stdout
    assert "HOOKED_VALUE" not in real_os.environ


def test_dry_run_fires_no_hooks():
    reg = Group("root")
    fired: list[str] = []

    @reg.pre_task
    def opened(inv, task):
        fired.append(task.name)

    @reg.task
    def build(): ...

    result = Runner().invoke("--dry-run build", tasks=reg)
    assert result.ok, result.stderr
    assert fired == []


def test_a_post_only_plugin_observes_every_execution():
    # A plugin with no pre has nothing to pair with, so its post rides the
    # moment itself: it fires for every execution that ran.
    reg = Group("root")
    seen: list[tuple] = []

    @reg.post_task
    def report(inv, task, result):
        seen.append((task.name, result.ok))

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert seen == [("build", True)]


def test_hook_arity_is_taught_at_registration():
    reg = Group("root")
    with pytest.raises(RegistrationError, match=r"def lonely\(inv, task\)"):

        @reg.pre_task
        def lonely(inv): ...

    with pytest.raises(RegistrationError, match=r"def eager\(inv, task, result\)"):

        @reg.post_task
        def eager(inv, task): ...


def test_the_handle_is_read_only_where_it_should_be():
    reg = Group("root")
    probed: list[str] = []

    @reg.pre_task
    def probe(inv, task):
        with pytest.raises(AttributeError, match="read-only"):
            task.name = "renamed"
        with pytest.raises(TypeError):
            task.args["x"] = 1  # a mapping proxy, not a dict
        assert task.source_hash is not None  # the tripwire, exposed
        probed.append(task.name)

    @reg.post_task
    def probe_result(inv, task, result):
        with pytest.raises(AttributeError, match="set_returned"):
            result.ok = True
        probed.append("post")

    @reg.task
    def build(target: str = "web"): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert probed == ["build", "post"]


def test_the_ladder_reaches_a_cascade_file(tmp_path):
    # The disk path: hooks in a tasks.py ride the merged tree's contributions
    # into the run — the same ladder the in-memory path fires.
    src = _write(
        tmp_path / "tasks.py",
        """
        import footman
        from footman import task

        @footman.pre_task
        def opened(inv, task):
            print(f"pre {task.name}")

        @footman.post_task
        def closed(inv, task, result):
            print(f"post {task.name} ok={result.ok}")

        @task
        def build(): ...
        """,
    )
    result = Runner().invoke("build", tasks=src)
    assert result.ok, result.stderr
    assert "pre build" in result.stdout
    assert "post build ok=True" in result.stdout


# --- pre_bind: the bind boundary ---------------------------------------------


def test_pre_bind_env_reaches_env_fallbacks():
    # The headline: the managed window opens before binding, so what pre_bind
    # writes into task.env is what env() fallbacks resolve — the one moment a
    # plugin can influence what the body is handed.
    import os as real_os

    reg = Group("root")

    @reg.pre_bind
    def creds(inv, task):
        task.env["LADDER_TOKEN"] = f"tok-{task.name}"

    @reg.task
    def deploy(
        token: Annotated[str, env("LADDER_TOKEN")] = "anon",
    ) -> str:
        print(token)
        return token

    result = Runner().invoke("deploy", tasks=reg)
    assert result.ok, result.stderr
    assert "tok-deploy" in result.stdout
    assert "LADDER_TOKEN" not in real_os.environ  # scoped, never global


def test_pre_bind_env_reaches_a_body_calls_binding():
    # A body call binds like a segment, so its omitted parameters see the
    # same pre_bind-injected environment the declared path sees.
    reg = Group("root")

    @reg.pre_bind
    def creds(inv, task):
        task.env["CALL_TOKEN"] = f"tok-{task.name}"

    @reg.task
    def build(token: Annotated[str, env("CALL_TOKEN")] = "anon") -> str:
        return token

    @reg.task
    def release():
        assert build() == "tok-build"

    result = Runner().invoke("release", tasks=reg)
    assert result.ok, result.stderr


def test_task_args_is_guarded_at_pre_bind_and_readable_at_pre_task():
    reg = Group("root")
    seen: list[object] = []

    @reg.pre_bind
    def early(inv, task):
        with pytest.raises(RuntimeError, match="pre_task, the post-bind moment"):
            task.args

    @reg.pre_task
    def later(inv, task):
        seen.append(dict(task.args))

    @reg.task
    def build(target: str = "web"): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert seen == [{"target": "web"}]


def test_a_bind_failure_still_fires_the_posts():
    # The attempt concluded — a bind-time span needs closing — so the posts
    # fire with the refusal result, exactly as the finished-event rule says.
    reg = Group("root")
    closed: list[tuple] = []

    @reg.pre_bind
    def poison(inv, task):
        task.env["LADDER_JOBS"] = "40"  # out of bounds: binding will refuse

    @reg.post_task
    def observe(inv, task, result):
        closed.append((task.name, result.ok, result.code))

    @reg.task
    def build(
        jobs: Annotated[int, env("LADDER_JOBS"), between(1, 10)] = 1,
    ):
        raise AssertionError("never runs")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "must be between 1 and 10" in result.stderr
    assert closed == [("build", False, 64)]  # EX_USAGE: a refusal, observed


def test_a_raising_pre_bind_fails_the_task_before_binding():
    reg = Group("root")
    closed: list[str] = []

    @reg.pre_bind
    def refuse(inv, task):
        raise ValueError("vault is sealed")

    @reg.post_task
    def observe(inv, task, result):
        closed.append(task.name)

    @reg.task
    def build():
        raise AssertionError("never runs")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "pre_bind hook 'refuse'" in result.stderr
    assert "vault is sealed" in result.stderr
    assert closed == ["build"]  # the attempt concluded: the post still fired


def test_pre_bind_fires_per_request_a_shared_row_pairs_nothing():
    # Binding happens per request, before sharing is decided; execution
    # happens per work. So a body call whose row ends up `shared` bound —
    # and fired pre_bind — while the execution pair fired exactly once.
    reg = Group("root")
    log: list[str] = []

    @reg.pre_bind
    def bound(inv, task):
        log.append(f"bind:{task.name}")

    @reg.pre_task
    def opened(inv, task):
        log.append(f"pre:{task.name}")

    @reg.post_task
    def closed(inv, task, result):
        log.append(f"post:{task.name}")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()  # shared: binds (pre_bind fires), runs nothing

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        "bind:build",
        "pre:build",
        "post:build",
        "bind:publish",
        "pre:publish",
        "bind:build",  # the call's request bound; its row is `shared`
        "post:publish",
    ]


def _bind_stamp(value):
    # module-level: eval_str resolves annotation names in module globals
    import os

    os.environ["BIND_STAMP"] = "set-by-validator"


def test_an_environ_write_during_bind_is_scoped_not_global():
    # The widened window covers user code that binding runs — a check(fn)
    # validator writing os.environ is captured into the task's overlay, so a
    # parallel sibling never sees it.
    import os as real_os

    reg = Group("root")

    @reg.task
    def build(target: Annotated[str, check(_bind_stamp)] = "web"):
        import os

        print(os.environ.get("BIND_STAMP", "missing"))

    result = Runner().invoke("build --target=web", tasks=reg)
    assert result.ok, result.stderr
    assert "set-by-validator" in result.stdout
    assert "BIND_STAMP" not in real_os.environ


def test_pre_bind_arity_is_taught_at_registration():
    reg = Group("root")
    with pytest.raises(RegistrationError, match=r"def alone\(inv, task\)"):

        @reg.pre_bind
        def alone(inv): ...
