# API reference

Auto-generated from the source via
[mkdocstrings](https://mkdocstrings.github.io/). Everything here is importable
straight from the `footman` package (`from footman import task, run, App`).

## Defining tasks

::: footman.registry.task

::: footman.registry.group

::: footman.registry.Group

## Running commands

::: footman.context.run

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

## Composing tasks

::: footman.compose.include

::: footman.compose.plugin

::: footman.registry.capture

## Custom CLI

::: footman.app.App

::: footman.app.Brand

## Typed-parameter helpers

::: footman.params.Many

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

::: footman.testing.Result

::: footman.testing.recording
