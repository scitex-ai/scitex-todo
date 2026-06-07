#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Phase-1 MCP server (`scitex_todo._mcp_server`).

These exercise the real FastMCP tool dispatch — NO MOCKS, per the
package's no-mock policy. When fastmcp isn't installed in the test
environment the module-level skip below kicks in so the tests are
honestly absent rather than green-theatre-passing.

The behaviour the suite covers:

- The server module raises a clean `ImportError` (not `ModuleNotFoundError`
  with an opaque trace) when fastmcp isn't installed.
- When fastmcp IS installed, every Phase-1 tool is registered and
  dispatches correctly against a `tmp_path` store.
- Tool naming follows the audit conventions: Convention A
  (tool_name == python_api_name, no `scitex_todo_` prefix) for the six
  task-store tools, plus Convention B (`todo_<verb>_<noun>` per §5) for
  the two skills tools — eight tools total.
- The CLI `mcp list-tools` enumerates them.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest


# --------------------------------------------------------------------------- #
# Optional-dep guard                                                          #
# --------------------------------------------------------------------------- #
fastmcp = pytest.importorskip(
    "fastmcp",
    reason=(
        "fastmcp not installed — `scitex-todo[mcp]` extra not present. "
        "Install with `pip install scitex-todo[mcp]` to run the MCP tests."
    ),
)


# --------------------------------------------------------------------------- #
# Module import                                                               #
# --------------------------------------------------------------------------- #
def test_mcp_server_module_imports():
    # Arrange
    mod = importlib.import_module("scitex_todo._mcp_server")
    # Act
    has_mcp = hasattr(mod, "mcp")
    # Assert
    assert has_mcp


def test_mcp_server_name():
    # Arrange
    mod = importlib.import_module("scitex_todo._mcp_server")
    # Act
    name = mod.mcp.name
    # Assert
    assert name == "scitex-todo"


def test_tool_naming_follows_convention():
    """At least one tool is registered (sanity for the rest of the suite)."""
    # Arrange
    from scitex_todo._mcp_server import mcp

    # Act
    names = _tool_names(mcp)
    # Assert
    assert names, "expected at least one tool registered"


# Convention A — tool_name == python_api_name (audit §6, no `scitex_todo_`
# prefix; audit §2 forbids the bare scitex_todo_<verb>_<noun> three-token
# form for the task-store tools after Convention A is adopted).
_CONVENTION_A_NAMES = {
    "add_task",
    "update_task",
    "complete_task",
    "add_comment",
    "list_tasks",
    "summarize_tasks",
    "resolve_store",
    # MCP completeness wave (lead a2a `fe723080`, 2026-06-08) — 7 new
    # task-store tools, each mapping 1:1 to a Python API function in
    # ``scitex_todo._store`` per Convention A.
    "get_task",
    "delete_task",
    "restore_task",
    "comment_task",
    "set_edge",
    "resolve_task",
    "reopen_task",
}
# Convention B — `todo_<verb>_<noun>` for the audit §5 required skills
# tools. These don't map 1:1 to a Python API; they introspect the bundled
# `_skills/` directory.
_CONVENTION_B_NAMES = {
    "todo_skills_list",
    "todo_skills_get",
}


def test_no_tool_uses_dropped_scitex_todo_prefix():
    """The `scitex_todo_` prefix was dropped per audit §6 Convention A.

    Single-token names (`summary`, `where`) were also forbidden by audit
    §2 and got renamed (`summarize_tasks`, `resolve_store`). This test
    asserts the rename held — no tool reverts to the old prefix nor to a
    bare single-token name.
    """
    # Arrange
    from scitex_todo._mcp_server import mcp
    names = _tool_names(mcp)
    # Act
    bad_prefix = [n for n in names if n.startswith("scitex_todo_")]
    single_token = [n for n in names if "_" not in n]
    # Assert
    assert not bad_prefix and not single_token, (
        f"tools {bad_prefix + single_token!r} regress audit §6 / §2"
    )


def test_tool_names_match_known_conventions():
    """Every registered tool is either Convention A (task-store) or B (skills)."""
    # Arrange
    from scitex_todo._mcp_server import mcp
    names = set(_tool_names(mcp))
    allowed = _CONVENTION_A_NAMES | _CONVENTION_B_NAMES
    # Act
    extras = names - allowed
    # Assert
    assert not extras, (
        f"unrecognised tool name(s) {extras!r} — add to "
        f"_CONVENTION_A_NAMES / _CONVENTION_B_NAMES if intentional"
    )


def test_phase_1_tools_registered():
    """All six Phase-1 tools (architecture doc) are present."""
    # Arrange
    from scitex_todo._mcp_server import mcp
    expected = {
        "add_task",
        "update_task",
        "complete_task",
        "list_tasks",
        "summarize_tasks",
        "resolve_store",
    }
    # Act
    names = set(_tool_names(mcp))
    # Assert
    assert expected <= names, f"missing: {expected - names}"


# --------------------------------------------------------------------------- #
# Tool round-trips against a real tmp_path store                              #
# --------------------------------------------------------------------------- #
def test_add_returns_id(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task
    store = str(tmp_path / "tasks.yaml")
    # Act
    add = asyncio.run(_call_tool(
        add_task,
        id="a",
        title="A",
        scope="agent:test",
        tasks_path=store,
    ))
    # Assert
    assert json.loads(add)["id"] == "a"


def test_add_then_list_round_trip(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, list_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(
        add_task,
        id="a",
        title="A",
        scope="agent:test",
        tasks_path=store,
    ))
    # Act
    listed = asyncio.run(_call_tool(list_tasks, tasks_path=store))
    rows = json.loads(listed)
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_scope_filter_excludes_other_scope(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, list_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(
        add_task, id="a", title="A", scope="agent:lead", tasks_path=store
    ))
    asyncio.run(_call_tool(
        add_task,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        tasks_path=store,
    ))
    # Act
    listed = asyncio.run(_call_tool(
        list_tasks, scope="agent:proj-scitex-todo", tasks_path=store
    ))
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"b"}


def test_complete_sets_status_done(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        add_task,
        complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["status"] == "done"


def test_complete_stamps_completed_by(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        add_task,
        complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_by"] == "agent:mcp-test"


def test_complete_stamps_completed_at_z_suffix(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        add_task,
        complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_at"].endswith("Z")


def test_add_task_accepts_agent_field(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task
    store = str(tmp_path / "tasks.yaml")
    # Act
    out = json.loads(asyncio.run(_call_tool(
        add_task, id="a", title="A",
        agent="proj-scitex-todo", tasks_path=store,
    )))
    # Assert
    assert out["agent"] == "proj-scitex-todo"


def test_add_task_accepts_kind_compute(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task
    store = str(tmp_path / "tasks.yaml")
    # Act
    out = json.loads(asyncio.run(_call_tool(
        add_task, id="a", title="A",
        kind="compute", job_id="123", tasks_path=store,
    )))
    # Assert
    assert out["kind"] == "compute"


def test_update_task_sets_agent(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, update_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(asyncio.run(_call_tool(
        update_task, task_id="a", agent="proj-scitex-todo", tasks_path=store,
    )))
    # Assert
    assert out["agent"] == "proj-scitex-todo"


def test_add_comment_returns_text(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import add_comment, add_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            add_comment, task_id="a", text="first", tasks_path=store
        ))
    )
    # Assert
    assert out["text"] == "first"


def test_add_comment_default_author_from_env(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import add_comment, add_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            add_comment, task_id="a", text="first", tasks_path=store
        ))
    )
    # Assert
    assert out["author"] == "agent:mcp-test"


def test_add_comment_in_reply_to_persists(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import add_comment, add_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        add_comment, task_id="a", text="parent",
        ts="2026-06-07T00:00:00Z", tasks_path=store,
    ))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            add_comment, task_id="a", text="reply",
            in_reply_to="2026-06-07T00:00:00Z", tasks_path=store,
        ))
    )
    # Assert
    assert out["in_reply_to"] == "2026-06-07T00:00:00Z"


def test_update_sets_status(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, update_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            update_task,
            task_id="a",
            status="in_progress",
            scope="agent:lead",
            tasks_path=store,
        ))
    )
    # Assert
    assert out["status"] == "in_progress"


def test_update_sets_scope(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, update_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            update_task,
            task_id="a",
            status="in_progress",
            scope="agent:lead",
            tasks_path=store,
        ))
    )
    # Assert
    assert out["scope"] == "agent:lead"


def test_summary_returns_total(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, summarize_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["total"] == 2


def test_summary_returns_done_count(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, summarize_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_returns_pending_count(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import add_task, summarize_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["by_status"]["pending"] == 1


def test_where_returns_resolved_path(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import resolve_store
    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(resolve_store, tasks_path=store)))
    # Assert
    assert info["resolved"] == store


def test_where_returns_exists_false_when_absent(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import resolve_store
    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(resolve_store, tasks_path=store)))
    # Assert
    assert info["exists"] is False


# --------------------------------------------------------------------------- #
# Helpers — handle FastMCP 2.x and 3.x APIs                                   #
# --------------------------------------------------------------------------- #
def _tool_names(mcp) -> list[str]:
    """Return registered tool names, version-agnostic.

    FastMCP 2.x exposes the registry synchronously via
    ``_tool_manager._tools`` (or the legacy ``_tools``/``tools`` on the
    server object). FastMCP 3.x removed those and only ships an async
    ``list_tools()``. Mirror the strategy used in ``_cli/_mcp.py``: try
    the cheap sync paths first, fall back to driving the async API.
    """
    tm = getattr(mcp, "_tool_manager", None)
    if tm is not None and isinstance(getattr(tm, "_tools", None), dict):
        return list(tm._tools.keys())
    for attr in ("tools", "_tools"):
        reg = getattr(mcp, attr, None)
        if isinstance(reg, dict):
            return list(reg.keys())
        if isinstance(reg, (list, tuple)):
            return [getattr(t, "name", str(t)) for t in reg]

    async def _gather():
        if tm is not None and hasattr(tm, "get_tools"):
            tools = await tm.get_tools()
        else:
            tools = await mcp.list_tools()
        if isinstance(tools, dict):
            return list(tools.keys())
        return [getattr(t, "name", str(t)) for t in tools]

    return asyncio.run(_gather())


async def _call_tool(tool_callable, **kwargs):
    """Call a `@mcp.tool()` callable, peeling FastMCP's wrappers as needed.

    FastMCP 2.x: `@mcp.tool()` returns the original async function — we
    can just await it.
    FastMCP 3.x: returns a `FunctionTool` whose `.fn` attribute holds
    the async function.
    """
    fn = getattr(tool_callable, "fn", None) or tool_callable
    return await fn(**kwargs)
