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
- Tool naming follows §2 `<pkg>_<verb>_<noun>` — six tools.
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
    """Every registered tool is prefixed with `scitex_todo_` (§2)."""
    # Arrange
    from scitex_todo._mcp_server import mcp

    # Act
    names = _tool_names(mcp)
    # Assert
    assert names, "expected at least one tool registered"


def test_tool_names_all_start_with_prefix():
    """Every registered tool name starts with `scitex_todo_`."""
    # Arrange
    from scitex_todo._mcp_server import mcp
    names = _tool_names(mcp)
    # Act
    bad = [n for n in names if not n.startswith("scitex_todo_")]
    # Assert
    assert not bad, (
        f"tools {bad!r} violate §2 <pkg>_<verb>_<noun> convention"
    )


def test_phase_1_tools_registered():
    """All six Phase-1 tools (architecture doc) are present."""
    # Arrange
    from scitex_todo._mcp_server import mcp
    expected = {
        "scitex_todo_add_task",
        "scitex_todo_update_task",
        "scitex_todo_complete_task",
        "scitex_todo_list_tasks",
        "scitex_todo_summary",
        "scitex_todo_where",
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
    from scitex_todo._mcp_server import scitex_todo_add_task
    store = str(tmp_path / "tasks.yaml")
    # Act
    add = asyncio.run(_call_tool(
        scitex_todo_add_task,
        id="a",
        title="A",
        scope="agent:test",
        tasks_path=store,
    ))
    # Assert
    assert json.loads(add)["id"] == "a"


def test_add_then_list_round_trip(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_list_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(
        scitex_todo_add_task,
        id="a",
        title="A",
        scope="agent:test",
        tasks_path=store,
    ))
    # Act
    listed = asyncio.run(_call_tool(scitex_todo_list_tasks, tasks_path=store))
    rows = json.loads(listed)
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_scope_filter_excludes_other_scope(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_list_tasks
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(
        scitex_todo_add_task, id="a", title="A", scope="agent:lead", tasks_path=store
    ))
    asyncio.run(_call_tool(
        scitex_todo_add_task,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        tasks_path=store,
    ))
    # Act
    listed = asyncio.run(_call_tool(
        scitex_todo_list_tasks, scope="agent:proj-scitex-todo", tasks_path=store
    ))
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"b"}


def test_complete_sets_status_done(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        scitex_todo_add_task,
        scitex_todo_complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(scitex_todo_complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["status"] == "done"


def test_complete_stamps_completed_by(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        scitex_todo_add_task,
        scitex_todo_complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(scitex_todo_complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_by"] == "agent:mcp-test"


def test_complete_stamps_completed_at_z_suffix(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        scitex_todo_add_task,
        scitex_todo_complete_task,
    )
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(scitex_todo_complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_at"].endswith("Z")


def test_update_sets_status(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_update_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            scitex_todo_update_task,
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
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_update_task
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(
            scitex_todo_update_task,
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
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_summary
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        scitex_todo_add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(scitex_todo_summary, tasks_path=store)))
    # Assert
    assert info["total"] == 2


def test_summary_returns_done_count(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_summary
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        scitex_todo_add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(scitex_todo_summary, tasks_path=store)))
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_returns_pending_count(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_summary
    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store))
    asyncio.run(_call_tool(
        scitex_todo_add_task, id="b", title="B", status="done", tasks_path=store
    ))
    # Act
    info = json.loads(asyncio.run(_call_tool(scitex_todo_summary, tasks_path=store)))
    # Assert
    assert info["by_status"]["pending"] == 1


def test_where_returns_resolved_path(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_where
    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(scitex_todo_where, tasks_path=store)))
    # Assert
    assert info["resolved"] == store


def test_where_returns_exists_false_when_absent(tmp_path):
    # Arrange
    from scitex_todo._mcp_server import scitex_todo_where
    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(scitex_todo_where, tasks_path=store)))
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
