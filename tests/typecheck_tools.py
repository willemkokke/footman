# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Type-level tests for the generated `tools.*` stubs. Never executed.

Nothing here is called, and pytest does not collect it (the filename does
not match `test_*.py`) — basedpyright is the test runner. A function body
is fully type-checked whether or not anything calls it, so this file asks
the checker the questions a stub should be able to answer, and CI fails
when the answer changes.

Two kinds of assertion, and the second is the load-bearing one:

* **the positive calls** are the shapes that appear in the docs and in real
  tasks. They must type-check, because a stub whose contract is "suggest,
  never forbid" fails the moment it rejects code that works.

* **the negative calls** are the actual test of coverage. `**flags: Any`
  swallows any keyword the stub has never heard of, so a *passing* call
  proves nothing — misspell a flag and it still passes. But a call that is
  required to *fail* proves the flag is declared and typed: if
  `mkdocs.build(strict=...)` ever stopped being stubbed, `strict="yes"`
  would be swallowed by `**flags`, the error would vanish, and the
  `# pyright: ignore` above it would become unnecessary — which this file
  turns into an error with the pragma on line 1.

So: to assert that a flag exists, pass it something wrong.
"""

from __future__ import annotations

from footman import tools
from footman.tools import Tool, off


def _ruff() -> None:
    tools.ruff.check("src", "tests", fix=True, select=["E", "F"])
    tools.ruff.check("src", output_format="github", exit_zero=True)
    tools.ruff.check("src", unsafe_fixes=off, respect_gitignore=off)
    tools.ruff.format("src", check=True, diff=True)
    tools.ruff_format("src", "tests", check=True)
    tools.ruff.check("src", nofail=True)
    tools.ruff.check("src", a_flag_ruff_grew_last_week=True)  # never a type error


def _uv() -> None:
    tools.uv.sync(frozen=True, group=["dev", "docs"])
    tools.uv.run("pytest", "-q")
    tools.uv.lock(check=True)
    tools.uv.build(sdist=True, wheel=True)
    tools.uv.add("httpx", dev=True)
    tools.uv.pip.install("footman", editable=".")
    tools.uv.tool.install("footman")


def _git() -> None:
    tools.git.add("-A")
    tools.git.commit(message="feat: a thing", signoff=True, gpg_sign=off)
    tools.git.push(set_upstream=True, force=off)
    tools.git.tag("v1.0.0", annotate=True, message="release")
    tools.git.status(short=True, branch=True)
    tools.git.switch("main", create=False)
    tools.git.clone("https://example.invalid/x.git", depth=1, quiet=True)
    # An optional-value option works both ways: bare (sign with the default
    # key) and with a value (a specific key). Both must type-check — a stub
    # that typed it bool-only would reject the second, one that typed it
    # value-only would reject the first.
    tools.git.commit(message="signed", gpg_sign=True)
    tools.git.commit(message="signed", gpg_sign="ABCD1234")
    tools.git.status(untracked_files=True)
    tools.git.status(untracked_files="all")
    tools.ruff.check("src", add_noqa=True)
    tools.ruff.check("src", add_noqa="suppressed for release")


def _docker() -> None:
    tools.docker.build(".", tag="app:latest", file="Dockerfile")
    tools.docker.compose.up(detach=True, build=True)
    tools.docker.compose.down(volumes=True, remove_orphans=True)
    tools.docker.compose.logs("web", follow=True, tail="100")
    tools.docker.ps(all=True, quiet=True)


def _docs_tools() -> None:
    tools.mkdocs.build(strict=True, clean=off)
    tools.mkdocs.build(use_directory_urls=off, site_dir="site")
    tools.mkdocs.serve(dirty=True, dev_addr="127.0.0.1:8000")
    tools.mkdocs.gh_deploy(force=True, message="deploy")
    tools.zensical.build(clean=True, strict=True)


def _coverage() -> None:
    tools.coverage.run("-m", "pytest", source=["footman"], parallel_mode=True)
    tools.coverage.report(fail_under=92, show_missing=True)
    tools.coverage.html(directory="htmlcov", skip_covered=True)
    tools.coverage.combine(append=True)
    tools.coverage.xml(quiet=True)


def _node_and_rust() -> None:
    tools.basedpyright("src", outputjson=True)
    tools.bun.add("left-pad", global_=True)
    tools.bun.install(frozen_lockfile=True)
    tools.cspell.lint("**/*.md", quiet=True, gitignore=True)
    tools.prek.run(all_files=True)
    tools.markdownlint("**/*.md", fix=True)


def _undeclared_and_reserved() -> None:
    """A tool footman has never heard of still works, and so do the two
    reserved keywords."""
    tools.terraform("plan", out="tf.plan")
    tools.helm.upgrade("app", "./chart", install=True)
    tools.sh("echo hello")
    tools.python("-c", "print(1)")
    tools.pytest("-q", in_process=True)
    tools.mkdocs.build(in_process=False, nofail=True)
    custom = Tool("helmfile", "--environment", "prod", in_process=False)
    custom("apply", skip_deps=True)


def _a_variable_drives_the_negation(pretty_urls: bool) -> None:
    """The shape from the docs: `True` → the flag, `off` → its negation."""
    tools.mkdocs.build(use_directory_urls=pretty_urls or off)


def _flags_are_declared_and_typed() -> None:
    """Each of these MUST fail to type-check.

    An unnecessary `# pyright: ignore` is an error here, so if a flag stops
    being declared — and `**flags: Any` starts swallowing it — this file
    fails rather than quietly testing nothing.
    """
    tools.mkdocs.build(strict="yes")  # pyright: ignore[reportArgumentType]
    tools.mkdocs.build(clean="no")  # pyright: ignore[reportArgumentType]
    tools.ruff.check(fix="always")  # pyright: ignore[reportArgumentType]
    tools.ruff.check(output_format="nope")  # pyright: ignore[reportArgumentType]
    tools.ruff_format(check="yes")  # pyright: ignore[reportArgumentType]
    tools.uv.sync(frozen="yes")  # pyright: ignore[reportArgumentType]
    tools.git.commit(signoff="yes")  # pyright: ignore[reportArgumentType]
    tools.docker.compose.up(detach="yes")  # pyright: ignore[reportArgumentType]
    tools.coverage.report(show_missing="yes")  # pyright: ignore[reportArgumentType]
    tools.zensical.build(clean="yes")  # pyright: ignore[reportArgumentType]
    tools.basedpyright(outputjson="yes")  # pyright: ignore[reportArgumentType]
    tools.bun.add(dev="yes")  # pyright: ignore[reportArgumentType]
    tools.cspell.lint(quiet="yes")  # pyright: ignore[reportArgumentType]
    tools.prek.run(all_files="yes")  # pyright: ignore[reportArgumentType]
    tools.markdownlint(fix="yes")  # pyright: ignore[reportArgumentType]
