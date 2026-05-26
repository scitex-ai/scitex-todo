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
    tasks_path,
) -> None:
    """Append a new task. Raises ``TaskValidationError`` on a duplicate id."""
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
    tasks_path,
) -> None:
    """Apply each provided field to the matching task."""
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
# list (extended — filter flags; backward-compatible default output)          #
# --------------------------------------------------------------------------- #
@click.command(
    "list",
    help=(
        "List tasks with optional scope/assignee/status filters.\n\n"
        "Without filters and without --json, prints the same plain-text\n"
        "table as `list-tasks` (backward-compatible).\n\n"
        "Example:\n  scitex-todo list --scope agent:proj-scitex-todo --json"
    ),
)
@click.option("--scope", default=None, help="Match `scope` exactly (use '' to ignore $SCITEX_TODO_SCOPE).")
@click.option("--assignee", default=None, help="Match `assignee` exactly.")
@click.option("--status", default=None, help="Match `status` exactly.")
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def list_cmd(scope, assignee, status, as_json, tasks_path) -> None:
    """Filter the store and print the matching tasks."""
    rows = _store.list_tasks(
        tasks_path, scope=scope, assignee=assignee, status=status
    )
    if as_json:
        click.echo(json.dumps(rows))
        return
    resolved = resolve_tasks_path(tasks_path)
    click.echo(f"# {resolved}  ({len(rows)} tasks)")
    for task in rows:
        sc = task.get("scope") or "-"
        click.echo(
            f"{task['id']:<24} {task['status']:<12} "
            f"{sc:<28} {task['title']}"
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
    info = _store.summary(tasks_path, scope=scope, assignee=assignee)
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
# where                                                                       #
# --------------------------------------------------------------------------- #
@click.command(
    "where",
    help=(
        "Show which store would be used and the precedence chain.\n\n"
        "Example:\n  scitex-todo where"
    ),
)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def where_cmd(as_json, tasks_path) -> None:
    """Resolve the store path and print the chain so agents can verify."""
    import os
    from pathlib import Path

    from .._paths import ENV_TASKS, PKG_SHORT, _user_root, bundled_example

    resolved = resolve_tasks_path(tasks_path)
    info = {
        "resolved": str(resolved),
        "explicit": tasks_path,
        "env_tasks": os.environ.get(ENV_TASKS),
        "user_store": str(_user_root() / "tasks.yaml"),
        "bundled_example": str(bundled_example()),
        "pkg_short": PKG_SHORT,
        "exists": Path(resolved).exists(),
    }
    if as_json:
        click.echo(json.dumps(info))
        return
    click.echo(f"resolved:        {info['resolved']}")
    click.echo(f"exists:          {info['exists']}")
    click.echo(f"explicit:        {info['explicit']}")
    click.echo(f"$SCITEX_TODO_TASKS: {info['env_tasks']}")
    click.echo(f"user store:      {info['user_store']}")
    click.echo(f"bundled example: {info['bundled_example']}")


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #
@click.command(
    "init",
    help=(
        "Create an empty task store at the chosen scope (idempotent).\n\n"
        "  --shared  -> ~/.scitex/todo/tasks.yaml (user scope, the default)\n"
        "  --project -> <git-root>/.scitex/todo/tasks.yaml\n\n"
        "Example:\n  scitex-todo init --shared"
    ),
)
@click.option(
    "--shared",
    "scope_choice",
    flag_value="shared",
    default="shared",
    help="Create the user-scope store (~/.scitex/todo/tasks.yaml).",
)
@click.option(
    "--project",
    "scope_choice",
    flag_value="project",
    help="Create <git-root>/.scitex/todo/tasks.yaml instead.",
)
def init_cmd(scope_choice) -> None:
    """Materialize an empty `tasks: []` store at the chosen scope."""
    from pathlib import Path

    from .._model import save_tasks
    from .._paths import _find_git_root, _user_root

    if scope_choice == "project":
        git_root = _find_git_root(Path.cwd())
        if git_root is None:
            raise click.ClickException(
                "`--project` requires running inside a git repo; "
                "no `.git` directory found in any parent of "
                f"{Path.cwd()}"
            )
        target = git_root / ".scitex" / "todo" / "tasks.yaml"
    else:
        target = _user_root() / "tasks.yaml"

    if target.exists():
        click.echo(f"exists: {target}  (no-op)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    save_tasks([], target)
    click.echo(f"created: {target}")


# --------------------------------------------------------------------------- #
# sync (Phase 1 STUB — Req 2 body in Phase 2)                                 #
# --------------------------------------------------------------------------- #
@click.command(
    "sync",
    help=(
        "Sync the user-scope store across hosts. PHASE-1 STUB.\n\n"
        "Phase 2 body: `git -C ~/.scitex/todo pull --rebase --autostash "
        "&& git push` against an operator-owned remote. The stub prints\n"
        "the plan and exits 0 so docs/skills can reference the verb today.\n\n"
        "Example:\n  scitex-todo sync --dry-run"
    ),
)
@click.option(
    "--apply",
    "mode",
    flag_value="apply",
    help="Execute the sync (NOT IMPLEMENTED in Phase 1; will exit non-zero).",
)
@click.option(
    "--dry-run",
    "mode",
    flag_value="dry_run",
    default="dry_run",
    help="Print what would happen and exit 0 (the Phase-1 default).",
)
@click.option(
    "--remote",
    default=None,
    help="Optional remote name override; Phase 2 default = 'origin'.",
)
def sync_cmd(mode, remote) -> None:
    """Sync stub. Prints the planned operations; --apply errors in Phase 1."""
    from .._paths import _user_root

    root = _user_root()
    remote = remote or "origin"
    plan = [
        f"git -C {root} pull --rebase --autostash {remote}",
        f"git -C {root} push {remote}",
    ]
    click.echo("# scitex-todo sync (PHASE-1 STUB)")
    click.echo(f"# store dir: {root}")
    click.echo(f"# remote:    {remote}")
    click.echo("# planned commands:")
    for cmd in plan:
        click.echo(f"  {cmd}")
    if mode == "apply":
        raise click.ClickException(
            "--apply is not implemented in Phase 1; the git substrate "
            "lands in Phase 2 (see GITIGNORED/ARCHITECTURE.md Req 2)."
        )


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #
def register(main: click.Group) -> None:
    """Attach the Phase-1 mutation/admin verbs to the root group."""
    main.add_command(add_cmd, name="add")
    main.add_command(update_cmd, name="update")
    main.add_command(done_cmd, name="done")
    main.add_command(list_cmd, name="list")
    main.add_command(summary_cmd, name="summary")
    main.add_command(where_cmd, name="where")
    main.add_command(init_cmd, name="init")
    main.add_command(sync_cmd, name="sync")


# EOF
