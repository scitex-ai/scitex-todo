#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP server — one FastMCP instance per the SciTeX convention.

Tools (§2 ``<pkg>_<verb>_<noun>`` naming):

    scitex_todo_add_task          — add a new task
    scitex_todo_update_task       — mutate fields of an existing task
    scitex_todo_complete_task     — mark done + stamp _log_meta
    scitex_todo_list_tasks        — filter the store
    scitex_todo_summary           — counts by status/scope/assignee
    scitex_todo_where             — print resolved store path + chain

The tool surface is a thin wrapper around :mod:`scitex_todo._store` (the
Python API) so MCP / CLI / GUI all share one logic path — §6 Python-API
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
        "Use scitex_todo_list_tasks with a `scope` arg (e.g. "
        "'agent:proj-scitex-todo') to see only your slice. The canonical "
        "store lives at ~/.scitex/todo/tasks.yaml; precedence is "
        "explicit > $SCITEX_TODO_TASKS > project (<git-root>/.scitex/todo) > "
        "user (~/.scitex/todo) > bundled example."
    ),
)


# --------------------------------------------------------------------------- #
# Tools                                                                       #
# --------------------------------------------------------------------------- #
@mcp.tool()
async def scitex_todo_add_task(
    id: str,
    title: str,
    status: str = "pending",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Append a new task to the store. Returns the inserted task as JSON.

    `tasks_path` overrides the default resolution chain; pass `None` to
    use the resolved default (project → user → bundled).
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
    )
    return json.dumps(inserted)


@mcp.tool()
async def scitex_todo_update_task(
    task_id: str,
    title: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Mutate fields of an existing task. Returns the merged task as JSON.

    Pass an empty string (e.g. `scope=""`) to CLEAR a field. Omit a field
    to leave it untouched.
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
    ):
        if value is None:
            continue
        fields[key] = None if value == "" else value
    merged = _store.update_task(tasks_path, task_id, **fields)
    return json.dumps(merged)


@mcp.tool()
async def scitex_todo_complete_task(
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
async def scitex_todo_list_tasks(
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
async def scitex_todo_summary(
    scope: str | None = None,
    assignee: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Numeric progress: counts by status / scope / assignee."""
    return json.dumps(_store.summary(tasks_path, scope=scope, assignee=assignee))


@mcp.tool()
async def scitex_todo_where(tasks_path: str | None = None) -> str:
    """Show the resolved store path and the precedence chain.

    Useful for an agent to confirm "yes, I am writing to the shared
    user-scope store, not to a project shadow."
    """
    import os
    from pathlib import Path

    from ._paths import (
        ENV_TASKS,
        PKG_SHORT,
        _user_root,
        bundled_example,
        resolve_tasks_path,
    )

    resolved = resolve_tasks_path(tasks_path)
    return json.dumps(
        {
            "resolved": str(resolved),
            "explicit": tasks_path,
            "env_tasks": os.environ.get(ENV_TASKS),
            "user_store": str(_user_root() / "tasks.yaml"),
            "bundled_example": str(bundled_example()),
            "pkg_short": PKG_SHORT,
            "exists": Path(resolved).exists(),
        }
    )


#: Canonical list of registered tool names — kept here as a constant so the
#: `mcp doctor` / `mcp list-tools` CLI verbs don't have to introspect
#: FastMCP's internal registry (which drifts between 2.x and 3.x). Update
#: this tuple whenever a `@mcp.tool()` is added or removed above.
TOOL_NAMES: tuple[str, ...] = (
    "scitex_todo_add_task",
    "scitex_todo_update_task",
    "scitex_todo_complete_task",
    "scitex_todo_list_tasks",
    "scitex_todo_summary",
    "scitex_todo_where",
)


__all__ = ["TOOL_NAMES", "mcp"]

# EOF
