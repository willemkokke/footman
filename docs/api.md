# API reference

Auto-generated from the source via
[mkdocstrings](https://mkdocstrings.github.io/). Everything here is importable
straight from the `footman` package (`from footman import task, run, App`).

## Defining tasks

::: footman.registry.task

::: footman.registry.group

::: footman.registry.Group

## Availability gates

Stack these above `@task` to list a task as unavailable (with a reason) where
it can't run. Every gate is evaluated live, and all failures are collected.

::: footman.registry.requires

::: footman.registry.requires_dep

::: footman.registry.requires_tool

::: footman.registry.requires_env

## Running commands

::: footman.context.run

::: footman.context.cwd

::: footman.context.chdir

::: footman.context.Result

::: footman.context.parallel

::: footman.context.passthrough

::: footman.context.inherited

::: footman.context.progress

::: footman.context.track

## Asking the person running it

::: footman.context.prompt

::: footman.context.confirm

::: footman.context.select

## Fetching

::: footman._fetch.fetch

::: footman._fetch.FetchError

::: footman.context.Context

::: footman.context.RunFailed

::: footman.context.fail

::: footman.context.Failed

## Composing tasks

::: footman.compose.include

::: footman.compose.plugin

::: footman.registry.capture

## The invocation, and editing the discovered tree

`@pre_tasks` runs a hook once per invocation, over the fully-merged cascade and
before anything else — see
[Composing tasks](composing.md#editing-the-discovered-tree). It is handed the
`Invocation`, whose `tasks` is a `Tasks` view; iterating or indexing that yields
a `TaskView` that reads and edits one task. The per-task pair — `@pre_task` and
`@post_task` — runs around every execution; see
[Around every task](composing.md#around-every-task-pre_task-and-post_task).

::: footman.registry.pre_tasks

::: footman.registry.pre_bind

::: footman.registry.pre_task

::: footman.registry.post_task

::: footman.registry.post_tasks

::: footman.registry.wrap_task

::: footman.registry.wrap_bind

::: footman.invocation.Invocation

::: footman.registry.Tasks

::: footman.registry.TaskView

## Custom CLI

::: footman.app.App

::: footman.app.Brand

## Typed-parameter helpers

::: footman.params.Many

::: footman.params.Arg

::: footman.params.nosplit

::: footman.params.suggest

::: footman.params.exists

::: footman.params.isfile

::: footman.params.isdir

::: footman.params.between

::: footman.params.env

::: footman.params.check

::: footman.params.doc

::: footman.params.ask

::: footman.params.Secret

## Docstrings

Standalone (stdlib-only, no footman imports) — reusable outside footman.

::: footman.docstrings.parse

::: footman.docstrings.Docstring

## Markdown export

Pure functions over manifest tree nodes — see
[Your tasks, documented](taskdocs.md) for the task-level surface.

::: footman.markdown.render_page

::: footman.markdown.render_site

## Testing

::: footman.context.use_context

::: footman.testing.Runner

::: footman.testing.InvokeResult

::: footman.testing.recording
