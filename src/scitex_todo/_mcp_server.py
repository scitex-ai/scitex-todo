#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP server — one FastMCP instance per the SciTeX convention.

Tools (audit §6 Convention A — ``tool_name == python_api_name``):

    add_task          — add a new task                  (scitex_todo.add_task)
    update_task       — mutate fields of an existing task (scitex_todo.update_task)
    complete_task     — mark done + stamp _log_meta     (scitex_todo.complete_task)
    list_tasks        — filter the store                (scitex_todo.list_tasks)
    summarize_tasks   — counts by status/scope/assignee (scitex_todo.summarize_tasks)
    resolve_store     — resolved store path + chain     (scitex_todo.resolve_store)
    todo_skills_list  — list bundled agent skills       (audit §5 required pair)
    todo_skills_get   — get one bundled skill's content (audit §5 required pair)

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

import json

try:
    from fastmcp import FastMCP
except ImportError as _exc:  # pragma: no cover — exercised in the doctor test
    raise ImportError(
        "scitex-todo MCP tools require the [mcp] extra. Install with:\n"
        "  pip install 'scitex-todo[mcp]'"
    ) from _exc

from . import _store

mcp = FastMCP(
    name="scitex-todo",
    instructions=(
        "scitex-todo: shared YAML task store across agents and hosts. "
        "Use list_tasks with a `scope` arg (e.g. "
        "'agent:proj-scitex-todo') to see only your slice. The canonical "
        "store lives at ~/.scitex/todo/tasks.yaml; precedence is "
        "explicit > $SCITEX_TODO_TASKS > project (<git-root>/.scitex/todo) > "
        "user (~/.scitex/todo) > bundled example."
    ),
)


# --------------------------------------------------------------------------- #
# Task-store tools — Convention A (tool name == Python API name).             #
# --------------------------------------------------------------------------- #
@mcp.tool()
async def add_task(
    id: str,
    title: str,
    status: str = "pending",
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
    tasks_path: str | None = None,
) -> str:
    """Append a new task to the store. Returns the inserted task as JSON.

    ``tasks_path`` overrides the default resolution chain; pass ``None`` to
    use the resolved default (project → user → bundled).

    Closed-enum fields (``status`` / ``kind`` / ``blocker``) are gated by
    the writer's validator — typos raise ``TaskValidationError`` with the
    bad value and the valid set.
    """
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
    )
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
    tasks_path: str | None = None,
) -> str:
    """Mutate fields of an existing task. Returns the merged task as JSON.

    Pass an empty string (e.g. ``scope=""``) to CLEAR a string field.
    Pass an empty list to CLEAR a list field. Omit a field to leave it
    untouched. Closed-enum values (``status`` / ``kind`` / ``blocker``)
    are gated by the writer's validator.
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
    merged = _store.update_task(tasks_path, task_id, **fields)
    return json.dumps(merged)


@mcp.tool()
async def complete_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mark a task done and stamp `_log_meta.completed_{at,by}`.

    Idempotent: re-completing a `done` task keeps the original stamp.
    `by` overrides the $SCITEX_TODO_AGENT → $USER precedence.
    """
    done = _store.complete_task(tasks_path, task_id, by=by)
    return json.dumps(done)


@mcp.tool()
async def list_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """List tasks, filtered by scope/assignee/status. Returns a JSON array.

    `scope=None` (the default) uses $SCITEX_TODO_SCOPE if set;
    `scope=""` (empty string) opts out of that env default.
    """
    rows = _store.list_tasks(
        tasks_path, scope=scope, assignee=assignee, status=status
    )
    return json.dumps(rows)


@mcp.tool()
async def summarize_tasks(
    scope: str | None = None,
    assignee: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Numeric progress: counts by status / scope / assignee."""
    return json.dumps(_store.summarize_tasks(tasks_path, scope=scope, assignee=assignee))


@mcp.tool()
async def resolve_store(tasks_path: str | None = None) -> str:
    """Show the resolved store path and the precedence chain.

    Useful for an agent to confirm "yes, I am writing to the shared
    user-scope store, not to a project shadow."
    """
    return json.dumps(_store.resolve_store(tasks_path))


# --------------------------------------------------------------------------- #
# Skills tools — audit §5 required pair.                                      #
# Convention B (`todo_<verb>_<noun>`) because skills aren't a Python API      #
# surface; they're file-system introspection on the bundled `_skills/` dir.   #
# --------------------------------------------------------------------------- #
def _skills_dir():
    """Return the path to the bundled scitex-todo skill files."""
    from pathlib import Path

    return Path(__file__).parent / "_skills" / "scitex-todo"


@mcp.tool()
async def todo_skills_list() -> str:
    """List bundled scitex-todo skill files. Returns a JSON array of names."""
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return json.dumps([])
    names = sorted(p.name for p in skills_dir.iterdir() if p.is_file())
    return json.dumps(names)


@mcp.tool()
async def todo_skills_get(name: str) -> str:
    """Return the content of one bundled scitex-todo skill file.

    `name` must match a file in the bundled skills dir (e.g.
    `"01_installation.md"`). Returns a JSON object
    ``{"name": str, "content": str}`` or
    ``{"name": str, "error": "not found"}`` if the name doesn't resolve.
    """
    skills_dir = _skills_dir()
    target = skills_dir / name
    # Guard path traversal — only allow direct children of skills_dir.
    if target.parent.resolve() != skills_dir.resolve() or not target.is_file():
        return json.dumps({"name": name, "error": "not found"})
    return json.dumps({"name": name, "content": target.read_text(encoding="utf-8")})


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
    return json.dumps(_store.get_task(tasks_path, task_id))


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
    return json.dumps(_store.delete_task(tasks_path, task_id))


@mcp.tool()
async def restore_task(
    task: dict,
    refs: list[str] | None = None,
    tasks_path: str | None = None,
) -> str:
    """Undo a ``delete_task`` — re-insert at the original id. ``task``
    must be the exact dict ``delete_task`` returned in ``"removed"``.
    """
    return json.dumps(_store.restore_task(tasks_path, task=task, refs=refs))


@mcp.tool()
async def comment_task(
    task_id: str,
    text: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Append an entry to a task's ``comments[]`` thread (the
    Gitea-compatible Issue-activity log). ``by`` overrides the default
    author resolution ($SCITEX_TODO_AGENT → $USER).
    """
    return json.dumps(_store.comment_task(tasks_path, task_id, text, by=by))


@mcp.tool()
async def set_edge(
    action: str,
    kind: str,
    source: str,
    target: str,
    tasks_path: str | None = None,
) -> str:
    """Add or remove a depends_on / blocks edge between two tasks.

    Args:
      action: ``"add"`` or ``"remove"``.
      kind: ``"depends_on"`` or ``"blocks"``.
      source / target: task ids on the edge.
    """
    return json.dumps(
        _store.set_edge(tasks_path, action=action, kind=kind, source=source, target=target)
    )


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
    return json.dumps(_store.resolve_task(tasks_path, task_id, actor=actor))


@mcp.tool()
async def reopen_task(
    task_id: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Un-resolve: flip ``status=done`` back to ``blocked`` /
    ``blocker=operator-decision``. The Resolve→Undo partner.
    """
    return json.dumps(_store.reopen_task(tasks_path, task_id, by=by))


#: Canonical list of registered tool names — kept here as a constant so the
#: `mcp doctor` / `mcp list-tools` CLI verbs don't have to introspect
#: FastMCP's internal registry (which drifts between 2.x and 3.x). Update
#: this tuple whenever a `@mcp.tool()` is added or removed above.
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
    "set_edge",
    "resolve_task",
    "reopen_task",
    "todo_skills_list",
    "todo_skills_get",
)


__all__ = ["TOOL_NAMES", "mcp"]

# EOF
