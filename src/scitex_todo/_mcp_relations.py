#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools — the card-RELATIONSHIP cluster.

Extracted from the budget-bound :mod:`scitex_todo._mcp_server` (the split its
``TOOL_NAMES`` block had queued), following the same pattern as
:mod:`scitex_todo._mcp_skills`: the tools register on the SAME shared ``mcp``
FastMCP instance, which ``_mcp_server`` imports at its tail for the
registration side effect — so ``from scitex_todo._mcp_server import mcp``
continues to expose every tool and ``TOOL_NAMES`` is unchanged.

Cluster: what a card is CONNECTED to —

  - ``set_edge`` — depends_on / blocks edges between two cards.
  - ``set_collaborator`` / ``set_subscriber`` — the ADR-0009 role lists.

Each tool is a thin wrapper around :mod:`scitex_todo._store` (§6 Python-API
parity) returning a JSON-string of what the Python API returns.
"""

from __future__ import annotations

import functools
import json

import anyio

from . import _store
from ._mcp_server import mcp


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
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_edge,
            tasks_path,
            action=action,
            kind=kind,
            source=source,
            target=target,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def set_collaborator(
    task_id: str,
    who: str,
    action: str = "add",
    tasks_path: str | None = None,
) -> str:
    """Add or remove a collaborator on a card (ADR-0009 roles).

    Args:
      task_id: the card id.
      who: the agent/human to add or remove.
      action: ``"add"`` (default) or ``"remove"``.

    Adding a collaborator also subscribes them to the card's feedback
    (the default — subscribers include collaborators). Removing a
    collaborator leaves their subscription intact; use ``set_subscriber``
    with ``action="remove"`` to also stop their notices.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_collaborator, tasks_path, task_id=task_id, who=who, action=action
        )
    )
    return json.dumps(result)


@mcp.tool()
async def set_subscriber(
    task_id: str,
    who: str,
    action: str = "add",
    tasks_path: str | None = None,
) -> str:
    """Subscribe or unsubscribe an agent/human on a card's notify list
    (ADR-0009 roles).

    Args:
      task_id: the card id.
      who: the agent/human to subscribe or unsubscribe.
      action: ``"add"`` (subscribe, default) or ``"remove"`` (unsubscribe).

    Anyone may unsubscribe — even a collaborator (the "always
    unsubscribable" rule).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _store.set_subscriber, tasks_path, task_id=task_id, who=who, action=action
        )
    )
    return json.dumps(result)


# EOF
