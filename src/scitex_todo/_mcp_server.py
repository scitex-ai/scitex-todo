#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP server — one FastMCP instance per the SciTeX convention.

Tools follow audit §6 Convention A (``tool_name == python_api_name``); see
``TOOL_NAMES`` for the full registered set (task CRUD + edges + roles, the
``help_wait`` / ``help_clear`` cards, ``poll_notifications`` — the standalone
PULL card-message inbox — and the ``todo_skills_*`` §5 pair).

Two cohesive tool clusters live in sibling modules for this module's line
budget and register on the SAME ``mcp`` instance (imported at the tail for the
side effect): :mod:`scitex_todo._mcp_relations` (edges + roles) and
:mod:`scitex_todo._mcp_skills` (skills, help-wait, DMs, inbox, health). The
agent-facing instructions text lives in :mod:`scitex_todo._mcp_instructions`.

The task-store tool surface is a thin wrapper around :mod:`scitex_todo._store`
(the Python API) so MCP / CLI / GUI all share one logic path — §6 Python-API
parity. JSON-shape parity: every tool returns a JSON-string of the dict /
list the Python API returns.

Import semantics
----------------
``fastmcp`` is an OPTIONAL dependency (``pip install scitex-todo[mcp]``).
Importing this module without fastmcp installed raises :class:`ImportError`
with a clear install hint — it does NOT raise at ``import scitex_todo``
time (the CLI guards the import; the MCP `start` verb surfaces the same
hint as a click error).
"""

from __future__ import annotations

import functools
import json

import anyio

try:
    from fastmcp import FastMCP
except ImportError as _exc:  # pragma: no cover — exercised in the doctor test
    raise ImportError(
        "scitex-todo MCP tools require the [mcp] extra. Install with:\n"
        "  pip install 'scitex-todo[mcp]'"
    ) from _exc

from . import _store
from ._channel_identity import resolve_agent_id_optional
from ._mcp_instructions import build_instructions

# The instructions name THIS agent's OWN scope, interpolated from its resolved
# identity ($SCITEX_TODO_AGENT_ID) — never a hard-coded example, which is how
# every agent came to be taught the scope of the long-dead `proj-scitex-todo`.
# An UNRESOLVED identity names no scope at all; see `_mcp_instructions`.
mcp = FastMCP(
    name="scitex-todo",
    instructions=build_instructions(resolve_agent_id_optional()),
)


# --------------------------------------------------------------------------- #
# Task-store tools — Convention A (tool name == Python API name).             #
# --------------------------------------------------------------------------- #
@mcp.tool()
async def add_task(
    id: str,
    title: str,
    status: str = "deferred",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension; closes the gap
    # noted in PR #127: callers couldn't SET deadlines via MCP).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
    created_by: str | None = None,  # creating USER; hook-bypass: line-limit
    tasks_path: str | None = None,
) -> str:
    """Append a new task to the store. Returns the inserted task as JSON.

    ``tasks_path`` overrides the default resolution chain; pass ``None`` to
    use the resolved default (project → user → bundled).

    Closed-enum fields (``status`` / ``kind`` / ``blocker``) are gated by
    the writer's validator — typos raise ``TaskValidationError`` with the
    bad value and the valid set.

    ``deadline`` accepts the P4 schema: a bare ISO date / ISO datetime,
    optionally followed by a recurring repeater suffix
    (``+1d``/``+1w``/``+1m``/``+1y``). ``deadlines`` is the multi form (a
    list of the same shape) — mutually exclusive with ``deadline``.
    ``scheduled`` is the corresponding "start work on" stamp (validator
    rejects ``deadline < scheduled``). See ``scitex_todo._model`` +
    ``next_deadline_for_task`` for parse rules.
    """
    _call = functools.partial(
        _store.add_task,
        tasks_path,
        id=id,
        title=title,
        status=status,
        scope=scope,
        assignee=assignee,
        priority=priority,
        parent=parent,
        note=note,
        repo=repo,
        depends_on=depends_on,
        blocks=blocks,
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
        deadline=deadline,
        deadlines=deadlines,
        scheduled=scheduled,
        created_by=created_by,  # hook-bypass: line-limit
    )
    inserted = await anyio.to_thread.run_sync(_call)
    return json.dumps(inserted)


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension) — mirror of the
    # add_task surface so callers can SET deadlines via MCP, not just
    # READ them via list_tasks (PR #127 gap).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mutate fields of an existing task. Returns the merged task as JSON.

    Pass an empty string (e.g. ``scope=""``) to CLEAR a string field.
    Pass an empty list to CLEAR a list field. Omit a field to leave it
    untouched. Closed-enum values (``status`` / ``kind`` / ``blocker``)
    are gated by the writer's validator.

    ``deadline`` / ``deadlines`` / ``scheduled`` follow the same P4
    schema as ``add_task``. Pass an empty string to CLEAR ``deadline`` /
    ``scheduled``; pass an empty list to CLEAR ``deadlines``. The pair
    ``deadline`` + ``deadlines`` is mutually exclusive; the validator
    will raise if both are set on the resulting task.
    """
    fields: dict = {}
    for key, value in (
        ("title", title),
        ("status", status),
        ("scope", scope),
        ("assignee", assignee),
        ("priority", priority),
        ("parent", parent),
        ("note", note),
        ("repo", repo),
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
        ("deadline", deadline),
        ("scheduled", scheduled),
    ):
        if value is None:
            continue
        fields[key] = None if value == "" else value
    # List fields: ``None`` = leave untouched (filtered above);
    # empty list = clear; non-empty list = replace.
    if depends_on is not None:
        fields["depends_on"] = list(depends_on) if depends_on else None
    if blocks is not None:
        fields["blocks"] = list(blocks) if blocks else None
    if deadlines is not None:
        fields["deadlines"] = list(deadlines) if deadlines else None
    merged = await anyio.to_thread.run_sync(
        functools.partial(_store.update_task, tasks_path, task_id, **fields)
    )
    return json.dumps(merged)


@mcp.tool()
async def complete_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mark a task done and stamp `_log_meta.completed_{at,by}`.

    Idempotent: re-completing a `done` task keeps the original stamp.
    `by` overrides the $SCITEX_TODO_AGENT_ID → $USER precedence.
    """
    done = await anyio.to_thread.run_sync(
        functools.partial(_store.complete_task, tasks_path, task_id, by=by)
    )
    return json.dumps(done)


@mcp.tool()
async def list_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
    tasks_path: str | None = None,
) -> str:
    """List tasks, filtered by any combination of fields. Returns a JSON array.

    ``scope=None`` (default) uses $SCITEX_TODO_SCOPE if set; ``scope=""``
    opts out of that env default. ``statuses`` (multi) OR-combines with
    ``status`` (single). ``blocker="__none"`` matches rows with no blocker.
    ``blocking_me=True`` matches the board's BLOCKING-YOU predicate
    (``status=blocked AND blocker=operator-decision``). ``overdue=True``
    matches tasks past their next deadline AND not in a terminal lifecycle
    state (mirrors the ``scitex-todo list-tasks --overdue`` CLI flag and
    the fleet payload's ``overdue_count``; see scitex_todo._model.is_overdue
    — todo-p6-overdue-ui, PR #125 / #126).
    """
    _call = functools.partial(
        _store.list_tasks,
        tasks_path,
        scope=scope,
        assignee=assignee,
        status=status,
        statuses=statuses,
        agent=agent,
        project=project,
        host=host,
        blocker=blocker,
        kind=kind,
        id_prefix=id_prefix,
        blocking_me=blocking_me,
        overdue=overdue,
    )
    rows = await anyio.to_thread.run_sync(_call)
    return json.dumps(rows)


@mcp.tool()
async def summarize_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Numeric progress: counts by status / scope / assignee."""
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.summarize_tasks, tasks_path, scope=scope, assignee=assignee
        )
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_store(tasks_path: str | None = None) -> str:
    """Show the resolved store path and the precedence chain.

    Useful for an agent to confirm "yes, I am writing to the shared
    user-scope store, not to a project shadow."
    """
    return json.dumps(_store.resolve_store(tasks_path))


@mcp.tool()
async def get_task(
    task_id: str,
    tasks_path: str | None = None,
) -> str:
    """Return one task by id as JSON. Raises if the id is unknown.

    Companion read-one verb for the CRUD surface (lead a2a `fe723080`).
    Mirrors the equivalent ``handle_get`` shape on the Django board, so
    MCP agents can use it without going through HTTP.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.get_task, tasks_path, task_id)
    )
    return json.dumps(result)


@mcp.tool()
async def delete_task(
    task_id: str,
    tasks_path: str | None = None,
) -> str:
    """Delete a task + scrub references; returns the lossless payload
    a follow-up ``restore_task`` can consume to undo.

    Returns ``{"removed": <task>, "refs": [<scrubbed-ref-ids>]}``.
    Wraps the board v3 Delete-with-Undo flow for MCP agents.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.delete_task, tasks_path, task_id)
    )
    return json.dumps(result)


@mcp.tool()
async def restore_task(
    task: dict,
    refs: list[str] | None = None,
    tasks_path: str | None = None,
) -> str:
    """Undo a ``delete_task`` — re-insert at the original id. ``task``
    must be the exact dict ``delete_task`` returned in ``"removed"``.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.restore_task, tasks_path, task=task, refs=refs)
    )
    return json.dumps(result)


@mcp.tool()
async def comment_task(
    task_id: str,
    text: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Append an entry to a task's ``comments[]`` thread (the
    Gitea-compatible Issue-activity log). ``by`` overrides the default
    author resolution ($SCITEX_TODO_AGENT_ID → $USER).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.comment_task, tasks_path, task_id, text, by=by)
    )
    return json.dumps(result)


@mcp.tool()
async def resolve_task(
    task_id: str,
    actor: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Flip a blocked task to done + clear the blocker. Appends an audit
    comment naming the actor. Idempotent on already-resolved tasks.

    This is the MCP equivalent of the board v3 "Resolve → notify agent"
    button (ADR-0006/0007).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.resolve_task, tasks_path, task_id, actor=actor)
    )
    return json.dumps(result)


@mcp.tool()
async def reopen_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Un-resolve: flip ``status=done`` back to ``blocked`` /
    ``blocker=operator-decision``. The Resolve→Undo partner.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_store.reopen_task, tasks_path, task_id, by=by)
    )
    return json.dumps(result)


#: Canonical list of registered tool names — a constant so the `mcp doctor`
#: / `mcp list-tools` CLI verbs need not introspect FastMCP's drifting
#: internal registry. Update when a `@mcp.tool()` is added/removed.
TOOL_NAMES: tuple[str, ...] = (
    "add_task",
    "update_task",
    "complete_task",
    "list_tasks",
    "summarize_tasks",
    "resolve_store",
    # MCP completeness wave (lead a2a `fe723080`, 2026-06-08).
    "get_task",
    "delete_task",
    "restore_task",
    "comment_task",
    # The card-RELATIONSHIP cluster (edges + ADR-0009 roles) — registered in
    # `_mcp_relations` (the split this block used to have queued).
    "set_edge",
    "set_collaborator",
    "set_subscriber",
    "resolve_task",
    "reopen_task",
    # Registered in `_mcp_skills` (budget): reassign (1:1 `_store.reassign_task`)
    "reassign_task",
    # Help-wait SoC lift — semantics lifted out of the dotfiles hook.
    "help_wait",
    "help_clear",
    # Standalone pull-inbox read path (1:1 `_inbox.poll_inbox`; in _mcp_skills).
    "poll_notifications",
    # Package-level health doctor (1:1 `_health.health`; in _mcp_skills). Broad
    # store/notifyd/channel diagnosis — distinct from the narrow `mcp doctor`.
    "health",
    "todo_skills_list",
    "todo_skills_get",
    # Operator↔agent DMs (threads.yaml sidecar; registered in _mcp_skills).
    "dm_send",
    "dm_list",
)

# Imports for the registration side effect: these modules (kept separate for
# this module's line budget) decorate their tools onto the shared ``mcp``
# instance, so ``from scitex_todo._mcp_server import mcp`` exposes every tool.
from . import _mcp_relations  # noqa: E402,F401
from . import _mcp_skills  # noqa: E402,F401

__all__ = ["TOOL_NAMES", "mcp"]

# EOF
