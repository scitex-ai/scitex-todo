#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side CLI verbs: add, update, done, summary, where, init, sync.

Wraps :mod:`scitex_todo._store` (the Python API). Each verb is a thin
click command that resolves the store path through the usual precedence
chain (CLI ``--tasks`` → ``$SCITEX_TODO_TASKS`` → project → user →
bundled example), forwards keyword args, and prints either a human-
readable line or JSON via ``--json``.

The agent-facing convention these verbs honor (per
``GITIGNORED/ARCHITECTURE.md`` Req 1):

- ``--scope LABEL`` / ``--assignee LABEL`` on read verbs respect
  ``$SCITEX_TODO_SCOPE`` as the default. Pass ``--scope ""`` to opt out.
- ``--by NAME`` on ``done`` overrides the
  ``$SCITEX_TODO_AGENT`` → ``$USER`` precedence chain.

The ``sync`` verb is a deliberate Phase-1 no-op stub (Req 2 substrate
lands in Phase 2). The stable name + flag shape exist now so docs and
skills can reference them; the body just dry-prints the plan.
"""

from __future__ import annotations

import json

import click

from .. import _store
from .._paths import resolve_tasks_path

# --------------------------------------------------------------------------- #
# Shared option decorators                                                    #
# --------------------------------------------------------------------------- #
_TASKS_OPTION = click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)


def _emit(payload, *, as_json: bool, human: str) -> None:
    """Print `payload` as JSON or `human` as text, per the --json flag."""
    if as_json:
        click.echo(json.dumps(payload, default=str))
    else:
        click.echo(human)


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #
@click.command(
    "add",
    help=(
        "Append a new task to the store.\n\n"
        "Example:\n"
        "  scitex-todo add my-task 'Implement my-task' "
        "--scope agent:proj-scitex-todo"
    ),
)
@click.argument("id")
@click.argument("title")
@click.option(
    "--status",
    default="pending",
    show_default=True,
    help="Initial status (one of: pending, in_progress, blocked, done, "
    "deferred, failed, goal).",
)
@click.option("--scope", default=None, help="Audience label (free-form string).")
@click.option(
    "--assignee", default=None, help="Who should act on this (free-form string)."
)
@click.option("--priority", type=int, default=None, help="Integer priority (lower = earlier).")
@click.option("--parent", default=None, help="Parent task id (nests this task under it).")
@click.option("--note", default=None, help="Markdown note shown in the board detail drawer.")
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="Task id this task depends on (repeatable).",
)
@click.option(
    "--blocks",
    "blocks",
    multiple=True,
    help="Task id this task blocks (repeatable).",
)
@click.option("--repo", default=None, help="Repo association (free-form string).")
@click.option("--json", "as_json", is_flag=True, help="Emit the inserted task as JSON.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be added and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — add is non-interactive; reserved for §2).",
)
@_TASKS_OPTION
def add_cmd(
    id,
    title,
    status,
    scope,
    assignee,
    priority,
    parent,
    note,
    depends_on,
    blocks,
    repo,
    as_json,
    dry_run,
    yes,
    tasks_path,
) -> None:
    """Append a new task. Raises ``TaskValidationError`` on a duplicate id."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would add id={id!r} title={title!r} status={status!r} "
            f"scope={scope!r} assignee={assignee!r}"
        )
        return
    try:
        inserted = _store.add_task(
            tasks_path,
            id=id,
            title=title,
            status=status,
            scope=scope,
            assignee=assignee,
            priority=priority,
            parent=parent,
            note=note,
            depends_on=list(depends_on) if depends_on else None,
            blocks=list(blocks) if blocks else None,
            repo=repo,
        )
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        inserted,
        as_json=as_json,
        human=f"added {inserted['id']}  ({inserted['status']}) {inserted['title']}",
    )


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #
@click.command(
    "update",
    help=(
        "Mutate fields of an existing task by id.\n\n"
        "Pass an empty string (e.g. --scope '') to CLEAR a field.\n\n"
        "Example:\n"
        "  scitex-todo update my-task --status in_progress --priority 1"
    ),
)
@click.argument("task_id")
@click.option("--title", default=None)
@click.option("--status", default=None)
@click.option("--scope", default=None, help="New scope (use '' to clear).")
@click.option("--assignee", default=None, help="New assignee (use '' to clear).")
@click.option("--priority", type=int, default=None)
@click.option("--parent", default=None)
@click.option("--note", default=None)
@click.option("--repo", default=None)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print which fields would change and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — update is non-interactive; reserved for §2).",
)
@_TASKS_OPTION
def update_cmd(
    task_id,
    title,
    status,
    scope,
    assignee,
    priority,
    parent,
    note,
    repo,
    as_json,
    dry_run,
    yes,
    tasks_path,
) -> None:
    """Apply each provided field to the matching task."""
    _ = yes  # accepted for §2 compliance
    fields: dict = {}
    # Pass through only the fields the user actually provided (click's
    # `None` default = "not passed"). Empty string is the explicit
    # "clear this field" signal — translate to None for `update_task` so
    # the key is popped rather than stored as `""`.
    for key, value in (
        ("title", title),
        ("status", status),
        ("scope", scope),
        ("assignee", assignee),
        ("priority", priority),
        ("parent", parent),
        ("note", note),
        ("repo", repo),
    ):
        if value is None:
            continue
        fields[key] = None if value == "" else value

    if not fields:
        raise click.ClickException(
            "no fields to update; pass at least one of "
            "--title/--status/--scope/--assignee/--priority/--parent/--note/--repo"
        )

    if dry_run:
        click.echo(
            f"# dry-run: would update task_id={task_id!r} fields={fields!r}"
        )
        return
    try:
        merged = _store.update_task(tasks_path, task_id, **fields)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        merged,
        as_json=as_json,
        human=f"updated {merged['id']}  ({merged['status']}) {merged['title']}",
    )


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
@click.command(
    "done",
    help=(
        "Mark a task as done; stamps _log_meta.completed_{at,by}.\n\n"
        "Idempotent: re-doneing a `done` task keeps the original stamp.\n\n"
        "Example:\n"
        "  scitex-todo done my-task --by agent:proj-scitex-todo"
    ),
)
@click.argument("task_id")
@click.option(
    "--by",
    default=None,
    help="Override completed_by (default: $SCITEX_TODO_AGENT, then $USER).",
)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def done_cmd(task_id, by, as_json, tasks_path) -> None:
    """Set status=done and stamp the completion meta."""
    try:
        done = _store.complete_task(tasks_path, task_id, by=by)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    stamp = done.get("_log_meta", {}).get("completed_at", "?")
    who = done.get("_log_meta", {}).get("completed_by", "?")
    _emit(
        done,
        as_json=as_json,
        human=f"done {done['id']}  (by {who} at {stamp})",
    )


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
@click.command(
    "summary",
    help=(
        "Print counts by status / scope / assignee.\n\n"
        "Example:\n  scitex-todo summary --json"
    ),
)
@click.option("--scope", default=None, help="Filter to this scope before counting.")
@click.option("--assignee", default=None)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def summary_cmd(scope, assignee, as_json, tasks_path) -> None:
    """Counts by status, scope, assignee for the resolved store."""
    info = _store.summarize_tasks(tasks_path, scope=scope, assignee=assignee)
    if as_json:
        click.echo(json.dumps(info))
        return
    click.echo(f"# {info['store']}  ({info['total']} tasks)")
    click.echo("by_status:")
    for s, n in info["by_status"].items():
        click.echo(f"  {s:<12} {n}")
    click.echo("by_scope:")
    for s, n in sorted(info["by_scope"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")
    click.echo("by_assignee:")
    for s, n in sorted(info["by_assignee"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #
def register(main: click.Group) -> None:
    """Attach the Phase-1 mutation verbs (add / update / done / summary).

    The admin verbs (`resolve-store` / `init-store` / `sync-store`) and the
    `list_tasks_filtered` helper live in the sibling `_admin.py` module — see
    its `register()`. `list-tasks` itself is owned by `_cli/_main.py` (the
    filter flags from the old `list` verb were folded in there; the `list`
    Click verb was removed per audit §1 — bare transitive verb at top level).
    """
    from . import _admin

    main.add_command(add_cmd, name="add")
    main.add_command(update_cmd, name="update")
    main.add_command(done_cmd, name="done")
    main.add_command(summary_cmd, name="summary")
    _admin.register(main)


# EOF
