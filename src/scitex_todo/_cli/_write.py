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
from .._model import VALID_BLOCKERS, VALID_KINDS, VALID_STATUSES
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

# Closed-enum CLI validation (fail-fast at click-parse time) — mirrors
# the _model validators so typos raise BEFORE we touch the disk. Matches
# the operator's "fail loud, fail fast" rule (TG 9494) one layer earlier
# than save_tasks.
_STATUS_CHOICE = click.Choice(list(VALID_STATUSES), case_sensitive=True)
_KIND_CHOICE = click.Choice(list(VALID_KINDS), case_sensitive=True)
_BLOCKER_CHOICE = click.Choice(list(VALID_BLOCKERS), case_sensitive=True)


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
        "--agent proj-scitex-todo --project scitex-todo"
    ),
)
@click.argument("id")
@click.argument("title")
@click.option(
    "--status",
    type=_STATUS_CHOICE,
    default="pending",
    show_default=True,
    help="Initial status (closed enum — see VALID_STATUSES).",
)
# Operator-co-designed surface (TG 9667).
@click.option("--task", default=None, help="The BIG board-card text (distinct from --title).")
@click.option("--project", default=None, help="Project / repo basename (e.g. 'scitex-todo').")
@click.option("--host", default=None, help="Where the work happens (hostname).")
@click.option("--agent", default=None, help="Owning agent (forward-compat alias for --assignee).")
@click.option("--goal", default=None, help="WHY (parent-goal text); 🎯 line on the card.")
@click.option("--last-activity", "last_activity", default=None, help="ISO-8601 UTC; drives recency color.")
@click.option(
    "--blocker",
    type=_BLOCKER_CHOICE,
    default=None,
    help="Closed enum (only valid when --status blocked).",
)
@click.option("--pr-url", "pr_url", default=None, help="GH/Gitea PR link.")
@click.option("--issue-url", "issue_url", default=None, help="GH/Gitea issue link.")
@click.option(
    "--kind",
    type=_KIND_CHOICE,
    default=None,
    help="Closed enum (absent ⇒ 'task').",
)
# Compute-kind metadata (ADR-0002).
@click.option("--job-id", "job_id", default=None, help="kind=compute: scheduler job id.")
@click.option("--command", default=None, help="kind=compute: command line.")
@click.option("--started-at", "started_at", default=None, help="kind=compute: start ISO-8601 UTC.")
@click.option("--finished-at", "finished_at", default=None, help="kind=compute: finish ISO-8601 UTC.")
# Legacy fields (preserved — assignee stays primary today per ADR-0008 D2).
@click.option("--scope", default=None, help="Audience label (free-form string).")
@click.option(
    "--assignee", default=None, help="Who should act on this (PRIMARY linking field today)."
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
    task,
    project,
    host,
    agent,
    goal,
    last_activity,
    blocker,
    pr_url,
    issue_url,
    kind,
    job_id,
    command,
    started_at,
    finished_at,
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
            f"agent={agent!r} project={project!r} kind={kind!r} blocker={blocker!r}"
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
            # Operator-co-designed + compute fields forwarded via **extras.
            task=task,
            project=project,
            host=host,
            agent=agent,
            goal=goal,
            last_activity=last_activity,
            blocker=blocker,
            pr_url=pr_url,
            issue_url=issue_url,
            kind=kind,
            job_id=job_id,
            command=command,
            started_at=started_at,
            finished_at=finished_at,
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
        "Pass an empty string (e.g. --scope '') to CLEAR a field.\n"
        "--depends-on / --blocks REPLACE the list (repeat the flag per id; "
        "pass once with '' to clear; +/- delta semantics are a follow-up PR).\n\n"
        "Example:\n"
        "  scitex-todo update my-task --status in_progress --priority 1 "
        "--agent proj-scitex-todo"
    ),
)
@click.argument("task_id")
@click.option("--title", default=None)
@click.option("--status", type=_STATUS_CHOICE, default=None)
# Operator-co-designed surface (TG 9667).
@click.option("--task", default=None, help="The BIG board-card text.")
@click.option("--project", default=None)
@click.option("--host", default=None)
@click.option("--agent", default=None, help="Owning agent (forward-compat alias for --assignee).")
@click.option("--goal", default=None)
@click.option("--last-activity", "last_activity", default=None)
@click.option("--blocker", type=_BLOCKER_CHOICE, default=None)
@click.option("--pr-url", "pr_url", default=None)
@click.option("--issue-url", "issue_url", default=None)
@click.option("--kind", type=_KIND_CHOICE, default=None)
# Compute-kind metadata.
@click.option("--job-id", "job_id", default=None)
@click.option("--command", default=None)
@click.option("--started-at", "started_at", default=None)
@click.option("--finished-at", "finished_at", default=None)
# Graph wiring (now supported on update, not just add).
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="REPLACE depends_on list. Repeat the flag per id; pass once with '' to clear.",
)
@click.option(
    "--blocks",
    "blocks",
    multiple=True,
    help="REPLACE blocks list. Repeat the flag per id; pass once with '' to clear.",
)
# Legacy fields.
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
    task,
    project,
    host,
    agent,
    goal,
    last_activity,
    blocker,
    pr_url,
    issue_url,
    kind,
    job_id,
    command,
    started_at,
    finished_at,
    depends_on,
    blocks,
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
        ("task", task),
        ("project", project),
        ("host", host),
        ("agent", agent),
        ("goal", goal),
        ("last_activity", last_activity),
        ("blocker", blocker),
        ("pr_url", pr_url),
        ("issue_url", issue_url),
        ("kind", kind),
        ("job_id", job_id),
        ("command", command),
        ("started_at", started_at),
        ("finished_at", finished_at),
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

    # --depends-on / --blocks: click's `multiple=True` returns a tuple.
    # Empty tuple = flag not passed → don't touch. Tuple of one empty
    # string = explicit "clear list". Otherwise REPLACE the list.
    for key, multi in (("depends_on", depends_on), ("blocks", blocks)):
        if not multi:
            continue
        if len(multi) == 1 and multi[0] == "":
            fields[key] = None
        else:
            fields[key] = [v for v in multi if v != ""]

    if not fields:
        raise click.ClickException(
            "no fields to update; pass at least one field flag (see --help)"
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
# comment                                                                     #
# --------------------------------------------------------------------------- #
@click.command(
    "comment",
    help=(
        "Append a comment to a task's comments[] (append-only activity log).\n\n"
        "Defaults: --author falls through $SCITEX_TODO_AGENT -> $USER; --ts "
        "auto-stamps ISO-8601 UTC now() if omitted.\n\n"
        "Example:\n"
        "  scitex-todo comment my-task 'PR #123 landed; CI green'"
    ),
)
@click.argument("task_id")
@click.argument("text")
@click.option(
    "--author",
    default=None,
    help="Comment author (default: $SCITEX_TODO_AGENT, then $USER).",
)
@click.option(
    "--ts",
    default=None,
    help="Comment timestamp (ISO-8601; default: UTC now()).",
)
@click.option(
    "--in-reply-to",
    "in_reply_to",
    default=None,
    help="Earlier comment's ts to reply to (renders as a nested thread).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the inserted comment as JSON.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be appended and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — comment is non-interactive; reserved for §2).",
)
@_TASKS_OPTION
def comment_cmd(
    task_id,
    text,
    author,
    ts,
    in_reply_to,
    as_json,
    dry_run,
    yes,
    tasks_path,
) -> None:
    """Append ``text`` to ``task_id``'s ``comments[]``."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would append comment to task_id={task_id!r} "
            f"author={author!r} ts={ts!r} in_reply_to={in_reply_to!r} "
            f"text={text!r}"
        )
        return
    try:
        entry = _store.add_comment(
            tasks_path,
            task_id,
            text,
            author=author,
            ts=ts,
            in_reply_to=in_reply_to,
        )
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        entry,
        as_json=as_json,
        human=(
            f"commented {task_id}  "
            f"({entry['author']} at {entry['ts']}): {entry['text']}"
        ),
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
    main.add_command(comment_cmd, name="comment")
    main.add_command(summary_cmd, name="summary")
    _admin.register(main)


# EOF
