#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Root ``scitex-todo`` group and core verbs (render-graph, list-tasks, board).

The §1a introspection / completion / skills groups live in sibling modules and
are attached to ``main`` at the bottom of this file.
"""

from __future__ import annotations

import json
import sys

import click

from .. import __version__
from .._mermaid import build_mermaid
from .._model import load_tasks
from .._paths import resolve_tasks_path
from .._render import render

_ROOT_EPILOG = (
    "Task store resolution (first existing wins): an explicit --tasks path, "
    "then $SCITEX_TODO_TASKS, then the project store "
    "<git-root>/.scitex/todo/tasks.yaml, then the user store "
    "~/.scitex/todo/tasks.yaml (relocatable via $SCITEX_DIR), then the bundled "
    "generic example. See the README 'Where your task data lives' section."
)


# --------------------------------------------------------------------------- #
# Top-level group (--help-recursive / --json universal flags)                 #
# --------------------------------------------------------------------------- #
def _iter_commands(cmd, ctx, prefix):
    """Yield ``(prefix, command, context)`` for ``cmd`` and every descendant."""
    yield prefix, cmd, ctx
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            yield from _iter_commands(sub, sub_ctx, f"{prefix} {name}")


def _command_tree(cmd, ctx):
    """Return a JSON-serializable ``{name, help, options, commands}`` tree."""
    node = {
        "name": ctx.info_name,
        "help": (cmd.help or "").strip(),
        "options": [p.opts[-1] for p in cmd.params if isinstance(p, click.Option)],
        "commands": {},
    }
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            node["commands"][name] = _command_tree(sub, sub_ctx)
    return node


def _emit_help_recursive(ctx, as_json):
    """Print flattened help (or the command tree as JSON) for every subcommand."""
    if as_json:
        click.echo(json.dumps(_command_tree(ctx.command, ctx), indent=2))
        return
    blocks: list[str] = []
    for prefix, cmd, sub_ctx in _iter_commands(ctx.command, ctx, ctx.info_name):
        blocks.append(f"### {prefix}\n{cmd.get_help(sub_ctx)}")
    click.echo("\n\n".join(blocks))


@click.group(
    invoke_without_command=True,
    help=f"scitex-todo (v{__version__}) — canonical YAML task store + adapters.",
    epilog=_ROOT_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--help-recursive",
    "help_recursive",
    is_flag=True,
    help="Show help for every subcommand, flattened.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON (the command tree for the top level).",
)
@click.version_option(__version__, "-V", "--version", prog_name="scitex-todo")
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    """scitex-todo CLI entry point."""
    if help_recursive or as_json:
        _emit_help_recursive(ctx, as_json=as_json)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


# --------------------------------------------------------------------------- #
# render-graph                                                                #
# --------------------------------------------------------------------------- #
@main.command(
    "render-graph",
    help=(
        "Render the task dependency graph to a PNG.\n\n"
        "Example:\n"
        "  scitex-todo render-graph --tasks ./.scitex/todo/tasks.yaml -o tasks.png"
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
def render_graph_cmd(tasks_path: str | None, output: str, print_mermaid: bool) -> None:
    """Render the resolved task store to a dependency PNG."""
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    mermaid_src = build_mermaid(tasks)

    if print_mermaid:
        sys.stdout.write(mermaid_src)
        return

    engine = render(mermaid_src, output)
    click.echo(f"{output}  (rendered via {engine}; source: {resolved})")


# --------------------------------------------------------------------------- #
# list-tasks                                                                  #
# --------------------------------------------------------------------------- #
@main.command(
    "list-tasks",
    help=(
        "List tasks with optional filters.\n\n"
        "Without any filter, prints the same plain-text table / JSON array\n"
        "as before (backward-compatible). With one or more filters,\n"
        "matches are AND-composed.\n\n"
        "Examples:\n"
        "  scitex-todo list-tasks --assignee proj-scitex-todo --json\n"
        "  scitex-todo list-tasks --project scitex-todo --status pending --status in_progress\n"
        "  scitex-todo list-tasks --blocking-me\n"
        "  scitex-todo list-tasks --id-prefix proj-scitex-\n"
        "  scitex-todo list-tasks --blocker __none  # rows with no blocker"
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
    "--scope",
    default=None,
    help="Match `scope` exactly (use '' to ignore $SCITEX_TODO_SCOPE).",
)
@click.option("--assignee", default=None, help="Match `assignee` exactly (PRIMARY linking field today).")
@click.option(
    "--agent",
    default=None,
    help="Match `agent` exactly (forward-compat alias for --assignee).",
)
@click.option("--project", default=None, help="Match `project` exactly.")
@click.option("--host", default=None, help="Match `host` exactly.")
@click.option(
    "--blocker",
    default=None,
    help="Match `blocker` exactly; `__none` matches rows with no blocker.",
)
@click.option(
    "--kind",
    default=None,
    help="Match `kind` exactly; `task` matches both explicit and absent rows.",
)
@click.option(
    "--id-prefix",
    "id_prefix",
    default=None,
    help="Match the front of `id` (cheap project-rollup lookup).",
)
@click.option(
    "--blocking-me",
    "blocking_me",
    is_flag=True,
    help="Predicate: status=blocked AND blocker=operator-decision (BLOCKING-YOU panel).",
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Match `status` exactly. Repeat for multi-status filter.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the resolved tasks as a JSON array.",
)
def list_tasks_cmd(
    tasks_path: str | None,
    scope: str | None,
    assignee: str | None,
    agent: str | None,
    project: str | None,
    host: str | None,
    blocker: str | None,
    kind: str | None,
    id_prefix: str | None,
    blocking_me: bool,
    statuses: tuple,
    as_json: bool,
) -> None:
    """Print the resolved task list (filtered or not)."""
    # Normalize: click's multiple=True returns a tuple; the helper
    # signature takes a list[str] | None. Empty tuple = no constraint.
    statuses_list: list[str] | None = list(statuses) if statuses else None
    # Did the caller pass ANY filter? Drive the dispatch off this.
    has_filter = any(
        v is not None for v in (
            scope, assignee, agent, project, host, blocker, kind, id_prefix,
        )
    ) or bool(statuses_list) or blocking_me

    if has_filter:
        from ._admin import list_tasks_filtered

        list_tasks_filtered(
            scope,
            assignee,
            # Legacy positional `status` (single) is None when --status
            # is empty / multi; the multi case feeds `statuses=`.
            None,
            as_json,
            tasks_path,
            statuses=statuses_list,
            agent=agent,
            project=project,
            host=host,
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
        )
        return
    # Plain path — backward-compatible plain table / JSON array.
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    if as_json:
        click.echo(json.dumps(tasks))
        return
    click.echo(f"# {resolved}  ({len(tasks)} tasks)")
    for task in tasks:
        click.echo(f"{task['id']:<24} {task['status']:<12} {task['title']}")


# --------------------------------------------------------------------------- #
# board                                                                       #
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Attach the §1a sub-groups (defined in sibling modules).                     #
# --------------------------------------------------------------------------- #
from . import _completion, _introspect, _loop, _mcp, _skills, _write  # noqa: E402

_introspect.register(main)
_completion.register(main)
_skills.register(main)
# Phase 1 mutation/admin verbs: add / update / done / list / summary /
# where / init / sync(stub). See GITIGNORED/ARCHITECTURE.md.
_write.register(main)
# Phase 1 MCP subgroup — §3 required four (start / doctor / list-tools /
# install). The module itself loads cleanly without fastmcp installed;
# individual verbs print a clear install hint when fastmcp is missing.
_mcp.register(main)
# P3b + P3d (lead-approved 2026-06-12) — self-consuming board loop.
# `scitex-todo next` returns the top runnable task for an agent;
# `scitex-todo watch --push` is the push side that wakes agents on
# new/commented/changed tasks. See _skills/scitex-todo/32_*.md for the
# 7-step agent self-consumption pattern.
_loop.register(main)


if __name__ == "__main__":
    main()

# EOF
