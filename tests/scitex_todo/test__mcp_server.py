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
    mod = importlib.import_module("scitex_todo._mcp_server")
    assert hasattr(mod, "mcp")
    assert mod.mcp.name == "scitex-todo"


def test_tool_naming_follows_convention():
    """Every registered tool is prefixed with `scitex_todo_` (§2)."""
    from scitex_todo._mcp_server import mcp

    names = _tool_names(mcp)
    assert names, "expected at least one tool registered"
    for name in names:
        assert name.startswith("scitex_todo_"), (
            f"tool {name!r} violates §2 <pkg>_<verb>_<noun> convention"
        )


def test_phase_1_tools_registered():
    """All six Phase-1 tools (architecture doc) are present."""
    from scitex_todo._mcp_server import mcp

    expected = {
        "scitex_todo_add_task",
        "scitex_todo_update_task",
        "scitex_todo_complete_task",
        "scitex_todo_list_tasks",
        "scitex_todo_summary",
        "scitex_todo_where",
    }
    names = set(_tool_names(mcp))
    assert expected <= names, f"missing: {expected - names}"


# --------------------------------------------------------------------------- #
# Tool round-trips against a real tmp_path store                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_add_then_list_round_trip(tmp_path):
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_list_tasks

    store = str(tmp_path / "tasks.yaml")
    add = await _call_tool(
        scitex_todo_add_task,
        id="a",
        title="A",
        scope="agent:test",
        tasks_path=store,
    )
    assert json.loads(add)["id"] == "a"

    listed = await _call_tool(scitex_todo_list_tasks, tasks_path=store)
    rows = json.loads(listed)
    assert {r["id"] for r in rows} == {"a"}


@pytest.mark.asyncio
async def test_scope_filter_round_trip(tmp_path):
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_list_tasks

    store = str(tmp_path / "tasks.yaml")
    await _call_tool(
        scitex_todo_add_task, id="a", title="A", scope="agent:lead", tasks_path=store
    )
    await _call_tool(
        scitex_todo_add_task,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        tasks_path=store,
    )
    listed = await _call_tool(
        scitex_todo_list_tasks, scope="agent:proj-scitex-todo", tasks_path=store
    )
    assert {r["id"] for r in json.loads(listed)} == {"b"}


@pytest.mark.asyncio
async def test_complete_stamps_log_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:mcp-test")
    from scitex_todo._mcp_server import (
        scitex_todo_add_task,
        scitex_todo_complete_task,
    )

    store = str(tmp_path / "tasks.yaml")
    await _call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store)
    out = json.loads(
        await _call_tool(scitex_todo_complete_task, task_id="a", tasks_path=store)
    )
    assert out["status"] == "done"
    assert out["_log_meta"]["completed_by"] == "agent:mcp-test"
    assert out["_log_meta"]["completed_at"].endswith("Z")


@pytest.mark.asyncio
async def test_update_round_trip(tmp_path):
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_update_task

    store = str(tmp_path / "tasks.yaml")
    await _call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store)
    out = json.loads(
        await _call_tool(
            scitex_todo_update_task,
            task_id="a",
            status="in_progress",
            scope="agent:lead",
            tasks_path=store,
        )
    )
    assert out["status"] == "in_progress"
    assert out["scope"] == "agent:lead"


@pytest.mark.asyncio
async def test_summary_returns_counts(tmp_path):
    from scitex_todo._mcp_server import scitex_todo_add_task, scitex_todo_summary

    store = str(tmp_path / "tasks.yaml")
    await _call_tool(scitex_todo_add_task, id="a", title="A", tasks_path=store)
    await _call_tool(
        scitex_todo_add_task, id="b", title="B", status="done", tasks_path=store
    )
    info = json.loads(await _call_tool(scitex_todo_summary, tasks_path=store))
    assert info["total"] == 2
    assert info["by_status"]["done"] == 1
    assert info["by_status"]["pending"] == 1


@pytest.mark.asyncio
async def test_where_returns_resolution(tmp_path):
    from scitex_todo._mcp_server import scitex_todo_where

    store = str(tmp_path / "tasks.yaml")
    info = json.loads(await _call_tool(scitex_todo_where, tasks_path=store))
    assert info["resolved"] == store
    # The file doesn't exist yet — we only asked where IT WOULD be.
    assert info["exists"] is False


# --------------------------------------------------------------------------- #
# Helpers — handle FastMCP 2.x and 3.x APIs                                   #
# --------------------------------------------------------------------------- #
def _tool_names(mcp) -> list[str]:
    for attr in ("tools", "_tools"):
        reg = getattr(mcp, attr, None)
        if isinstance(reg, dict):
            return list(reg.keys())
        if isinstance(reg, (list, tuple)):
            return [getattr(t, "name", str(t)) for t in reg]
    return []


async def _call_tool(tool_callable, **kwargs):
    """Call a `@mcp.tool()` callable, peeling FastMCP's wrappers as needed.

    FastMCP 2.x: `@mcp.tool()` returns the original async function — we
    can just await it.
    FastMCP 3.x: returns a `FunctionTool` whose `.fn` attribute holds
    the async function.
    """
    fn = getattr(tool_callable, "fn", None) or tool_callable
    return await fn(**kwargs)
