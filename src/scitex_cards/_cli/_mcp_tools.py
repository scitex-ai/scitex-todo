#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastMCP version-agnostic tool-introspection helpers for the `mcp` group.

Extracted from ``_cli/_mcp.py`` (size cap). Used by the ``doctor`` and
``list-tools`` verbs to enumerate the registered FastMCP tools across
fastmcp 2.x / 3.x. Re-exported by ``_mcp.py`` for historical import paths.
"""

from __future__ import annotations


def _tools_dict(mcp_obj) -> dict:
    """Return ``{name: tool}`` for a FastMCP server, version-agnostic.

    Self-contained mirror of ``scitex_dev.get_tools_sync`` (this fallback runs
    only when scitex-dev is *not* installed). FastMCP 3.x removed the sync
    ``_tools``/``tools`` attributes and exposes an async ``list_tools()``
    returning a *list* of Tool objects; 2.x exposes ``_tool_manager._tools``
    (dict) / ``_tool_manager.get_tools()``. We try the cheap sync paths first,
    then fall back to running the async API (guarding against a live loop).
    """
    import asyncio

    tm = getattr(mcp_obj, "_tool_manager", None)
    if tm is not None and isinstance(getattr(tm, "_tools", None), dict):
        return dict(tm._tools)
    for attr in ("tools", "_tools"):
        registry = getattr(mcp_obj, attr, None)
        if isinstance(registry, dict):
            return dict(registry)
        if isinstance(registry, (list, tuple)):
            return {getattr(t, "name", str(t)): t for t in registry}

    async def _gather():
        if tm is not None and hasattr(tm, "get_tools"):
            return await tm.get_tools()
        tools = await mcp_obj.list_tools()
        return {getattr(t, "name", str(t)): t for t in tools}

    if getattr(mcp_obj, "list_tools", None) is None and tm is None:
        return {}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is not None and running.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _gather()).result()
        return asyncio.run(_gather())
    except Exception:
        return {}


def _list_tool_names(mcp_obj) -> list[str]:
    """Names of the tools registered on the FastMCP server (2.x / 3.x)."""
    return list(_tools_dict(mcp_obj).keys())


def _list_tool_records(mcp_obj, *, verbosity: int) -> list[dict]:
    """``{name, description, …}`` records, FastMCP version-agnostic."""
    return [
        _tool_record(name, tool, verbosity=verbosity)
        for name, tool in _tools_dict(mcp_obj).items()
    ]


def _tool_record(name: str, tool, *, verbosity: int) -> dict:
    rec: dict = {"name": name}
    desc = getattr(tool, "description", None) or getattr(tool, "__doc__", None) or ""
    if verbosity >= 1:
        rec["description"] = desc.strip()
    if verbosity >= 3:
        # The full tool object is not JSON-friendly; expose its repr only.
        rec["repr"] = repr(tool)
    return rec


__all__ = [
    "_tools_dict",
    "_list_tool_names",
    "_list_tool_records",
    "_tool_record",
]

# EOF
