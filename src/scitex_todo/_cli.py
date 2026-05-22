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


@main.command(
    "board",
    help=(
        "Launch the dependency-graph board in a browser (read-only).\n\n"
        "Requires the web extra: pip install scitex-todo[web]\n\n"
        "Example:\n  scitex-todo board --port 8051"
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
    "--port",
    type=int,
    default=8051,
    show_default=True,
    help="Server port.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Don't open a browser automatically.",
)
def board_cmd(tasks_path: str | None, port: int, no_browser: bool) -> None:
    """Launch the standalone scitex-todo board server."""
    import os

    try:
        import django  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "The board needs the web extra. Install it with:\n"
            "  pip install scitex-todo[web]"
        ) from None

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_todo._django.settings")

    import django as _dj

    _dj.setup()
    from django.core.management import call_command

    args = ["scitex_todo_board", "--port", str(port)]
    if tasks_path:
        args += ["--tasks", tasks_path]
    if no_browser:
        args += ["--no-browser"]
    call_command(*args)


if __name__ == "__main__":
    main()

# EOF
