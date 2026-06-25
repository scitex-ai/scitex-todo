#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools extracted from the budget-bound server module.

:mod:`scitex_todo._mcp_server` sat at its line budget, so two cohesive tool
clusters live here instead and register on the SAME shared ``mcp`` FastMCP
instance — ``_mcp_server`` imports this module at its tail for the
registration side effect, so ``from scitex_todo._mcp_server import mcp``
continues to expose every tool.

Clusters:

  - Skills (Convention B, ``todo_<verb>_<noun>``) — audit §5 required pair;
    file-system introspection on the bundled ``_skills/`` dir.
  - Help-wait (``help_wait`` / ``help_clear``) — the "agent is stuck waiting
    on the operator" card, lifted out of the dotfiles Notification hook so
    scitex-todo owns the semantics. 1:1 with :mod:`scitex_todo._help_wait`.
"""

from __future__ import annotations

import json

from . import _help_wait
from ._mcp_server import mcp


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
async def help_wait(
    agent: str,
    question: str | None = None,
    host: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """UPSERT the canonical "agent is waiting on the operator" card.

    Card contract (id ``help-<agent>-waiting``, title ``[help] <agent>
    waiting on operator decision``, status ``blocked``, blocker
    ``operator-decision``, assignee + ``scope=agent:<agent>``, ``host`` from
    the arg or best-effort hostname, ``note`` from ``question`` or a
    placeholder). Idempotent: a re-run refreshes note + last_activity in
    place and never duplicates. Returns the upserted card as JSON.
    """
    return json.dumps(
        _help_wait.help_wait(tasks_path, agent, question=question, host=host)
    )


@mcp.tool()
async def help_clear(
    agent: str,
    tasks_path: str | None = None,
) -> str:
    """Resolve the ``help-<agent>-waiting`` card (status=done, clear blocker).

    No-op (no error) when the card does not exist. Returns a JSON object
    ``{"task_id": <id>, "cleared": bool, ...}``.
    """
    return json.dumps(_help_wait.help_clear(tasks_path, agent))


__all__ = ["help_clear", "help_wait", "todo_skills_get", "todo_skills_list"]

# EOF
