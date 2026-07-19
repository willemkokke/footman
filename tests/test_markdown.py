"""The markdown renderer: pages, sites, flavors, determinism."""

from __future__ import annotations

from typing import Annotated, Literal

import pytest

from footman import manifest, markdown, registry, task
from footman.params import doc
from footman.registry import group


@pytest.fixture
def sample_tree():
    with registry.capture() as root:

        @task
        def build(target: Literal["web", "api"], fix: bool = False, jobs: int = 4):
            """Build one target.

            The long story about building.

            Args:
                target: which target to build
                fix: repair as we go
                jobs: parallel workers
            """

        docs = group("docs", help="Documentation")

        @docs.task
        def serve(port: Annotated[int, doc("port to bind")] = 8000):
            "Serve the docs."

    return manifest.build_manifest(root)["tree"]


def test_page_whole_tree(sample_tree):
    page = markdown.render_page(sample_tree)
    assert page.startswith("# fm tasks\n")
    assert "## build" in page and "## docs" in page and "### docs serve" in page
    assert "```text\nfm build <target> [--fix] [--jobs INT]\n```" in page
    assert "| Parameter | Type | Default | Description |" in page
    assert (
        "| `<target>` | `web` \\| `api` | *required* | which target to build |" in page
    )
    assert "| `--jobs INT` | int | `4` | parallel workers |" in page
    assert "The long story about building." in page
    assert "**Example:** `fm build web --fix`" in page
    assert "port to bind" in page  # the doc() marker text rides along


def test_page_scoped_to_group_and_task(sample_tree):
    group_page = markdown.render_page(sample_tree, path=("docs",))
    assert group_page.startswith("# docs\n")
    assert "Documentation" in group_page and "## docs serve" in group_page
    assert "build" not in group_page  # scoped: the sibling task is absent

    task_page = markdown.render_page(sample_tree, path=("docs", "serve"))
    assert task_page.startswith("# docs serve\n")
    assert "fm docs serve [--port INT]" in task_page


def test_page_heading_level_nests(sample_tree):
    page = markdown.render_page(sample_tree, path=("docs", "serve"), heading=3)
    assert page.startswith("### docs serve\n")


def test_page_unknown_target_teaches(sample_tree):
    with pytest.raises(ValueError, match=r"no task or group named 'nope'"):
        markdown.render_page(sample_tree, path=("nope",))


def test_material_flavor_adds_anchors_and_admonition(sample_tree):
    page = markdown.render_page(sample_tree, flavor="material")
    assert "## build { #build }" in page
    assert "### docs serve { #docs-serve }" in page
    assert "!!! example" in page
    plain = markdown.render_page(sample_tree)
    assert "{ #" not in plain and "!!!" not in plain  # plain stays portable


def test_prog_threads_through(sample_tree):
    page = markdown.render_page(sample_tree, prog="acme")
    assert page.startswith("# acme tasks\n")
    assert "acme build <target>" in page


def test_site_layout_and_links(sample_tree):
    files = markdown.render_site(sample_tree)
    assert set(files) == {"index.md", "build.md", "docs/index.md", "docs/serve.md"}
    index = files["index.md"]
    assert "[`build`](build.md)" in index
    assert "[`docs`](docs/index.md)" in index
    assert "Build one target." in index  # the task's help line in the table
    sub = files["docs/index.md"]
    assert "[`serve`](serve.md)" in sub  # links are relative to their folder
    assert files["docs/serve.md"].startswith("# docs serve\n")


def test_site_scoped_to_task_is_one_file(sample_tree):
    files = markdown.render_site(sample_tree, path=("docs", "serve"))
    assert set(files) == {"serve.md"}


def test_render_is_deterministic(sample_tree):
    assert markdown.render_page(sample_tree) == markdown.render_page(sample_tree)
    assert markdown.render_site(sample_tree) == markdown.render_site(sample_tree)
