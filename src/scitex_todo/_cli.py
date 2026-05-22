#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo`` command-line interface (Click, noun-verb).

Verbs:
    render   Resolve a task store and render its dependency graph to PNG.
    list     Print the resolved tasks (id / status / title) to stdout.
"""

from __future__ import annotations

import sys

import click

from . import __version__
from ._mermaid import build_mermaid
from ._model import load_tasks
from ._paths import resolve_tasks_path
from ._render import render


@click.group(
    help=f"scitex-todo (v{__version__}) — canonical YAML task store + adapters.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="scitex-todo")
def main() -> None:
    """scitex-todo CLI entry point."""


@main.command(
    "render",
    help=(
        "Render the task dependency graph to a PNG.\n\n"
        "Example:\n"
        "  scitex-todo render --tasks ./.scitex/todo/tasks.yaml -o tasks.png"
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "-o",
    "--output",
    default="tasks.png",
    show_default=True,
    help="Output PNG path.",
)
@click.option(
    "--print-mermaid",
    is_flag=True,
    help="Print the generated mermaid source to stdout and exit (no render).",
)
def render_cmd(tasks_path: str | None, output: str, print_mermaid: bool) -> None:
    """Render the resolved task store to a dependency PNG."""
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    mermaid_src = build_mermaid(tasks)

    if print_mermaid:
        sys.stdout.write(mermaid_src)
        return

    engine = render(mermaid_src, output)
    click.echo(f"{output}  (rendered via {engine}; source: {resolved})")


@main.command(
    "list",
    help=(
        "List the resolved tasks (id, status, title).\n\nExample:\n  scitex-todo list"
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
def list_cmd(tasks_path: str | None) -> None:
    """Print the resolved task list to stdout."""
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    click.echo(f"# {resolved}  ({len(tasks)} tasks)")
    for task in tasks:
        click.echo(f"{task['id']:<24} {task['status']:<12} {task['title']}")


if __name__ == "__main__":
    main()

# EOF
