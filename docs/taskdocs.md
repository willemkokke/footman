# Your tasks, documented

footman ships a first-party plugin that renders a project's task tree as
markdown — the same names, params, docstring help, defaults, and examples
that `fm --help` shows, as pages you can publish. Everything on this page is
dogfooded: the [task reference](tasks/index.md) in this site's nav is
generated output, and the sample further down is embedded live.

## Mount it

The plugin mounts like any other — two lines of config, no tasks-file edit:

```toml
# pyproject.toml (or footman.toml)
[tool.footman]
plugins = ["footman"]
```

That's also the two-line demo of the [plugin system](composing.md): after it,
`fm --list` shows `footman docs page` and `footman docs site`. (Cherry-pick
or remount with `include(plugin("footman"), …)` if you'd rather not take the
whole group.)

## One page: `fm footman docs page`

```sh
fm footman docs page > TASKS.md          # the whole tree, one document
fm footman docs page --target docs       # just one group…
fm footman docs page --target docs.build # …or one task
fm footman docs page --out TASKS.md      # write the file directly
```

The page goes to stdout (stdout is the answer; footman's summary is stderr
commentary), so it pipes:

```sh
fm footman docs page | pandoc -o tasks.pdf     # or .html, .docx, …
```

`--heading 2` (up to 6) makes the headings start deeper, so the output nests
under a host page's own title — which is exactly how the sample below is
embedded, via a [`pymdownx.snippets`](https://facelessuser.github.io/pymdown-extensions/extensions/snippets/)
include of a file the docs build regenerates:

```markdown
--8<-- "docs/_generated/tasks-page.md"
```

`--flavor plain` (the default) is pure CommonMark and pipe tables — safe for
pandoc and any renderer. `--flavor material` opts into what a
zensical/mkdocs-material site already understands: heading anchors for
stable deep links and an `!!! example` admonition for the synthesized
invocation.

## A linked site: `fm footman docs site`

```console
$ fm footman docs site docs/tasks
wrote 18 pages under docs/tasks
```

One file per task, an `index.md` per group with relative links, directories
mirroring your group tree — drop it into your docs source and put the index
in your nav. This site's **Task reference** section is exactly that, wired
into [`zensical.toml`](https://github.com/willemkokke/footman/blob/main/zensical.toml)'s
nav. `site` defaults to `--flavor material` because a docs site is where it
lands; pass `--flavor plain` for anything else.

## The runner itself: `fm footman docs globals`

Your tasks aren't the only thing worth documenting — the runner's global
options deserve a page too. `globals` renders them as a markdown table
straight from the CLI grammar: the same rows, in the same order, with the
same words `--help` prints. This site's [CLI reference](reference.md) table
is exactly that, regenerated on every docs build — it *cannot* drift,
because it was never written by hand.

```console
$ fm footman docs globals --out docs/_generated/globals.md
wrote docs/_generated/globals.md
```

## Keep it fresh

Generated pages drift unless a build regenerates them. The tasks are plain
functions, so footman's own docs task calls them directly — copy the shape:

```python
@docs.task(name="build")
def docs_build(check: bool = False):
    "Build the docs site; regenerates the task reference first."
    from footman.tasks.docs import globals_, page, site

    site(Path("docs/tasks"))
    page(target="docs", heading=3, out=Path("docs/_generated/tasks-page.md"))
    globals_(out=Path("docs/_generated/globals.md"))
    tools.zensical.build(clean=True, strict=check)
```

Add the generated paths to `.gitignore` — they're build output, not source.
Under [`--json`](json.md), both tasks `return` the list of files they wrote,
so `returned` carries it for CI to verify.

Two flags to know: usage lines and examples carry **the CLI you invoked** —
a [branded CLI](custom-cli.md) documents itself as `acme` with no flag at
all, and `--prog` overrides the name when you need to. `--all` includes the
mounted `footman` group itself (excluded by default — the documenter
doesn't document itself unless asked).

## The live sample

Everything below this line is `fm footman docs page --target docs
--heading 3 --flavor material`, regenerated on every docs build:

--8<-- "docs/_generated/tasks-page.md"
