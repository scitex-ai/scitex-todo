#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the MCP server's agent-facing instructions (`_mcp_instructions`).

The bug these lock down: the instructions hard-coded ``'agent:proj-scitex-cards'``
as the example scope. That identity does not exist — ``proj-`` is a dead legacy
prefix (the one :data:`scitex_cards._users.IDENTITY_PREFIXES` exists to STRIP).
Measured against the live store on 2026-07-11:

    cards under the TAUGHT scope  agent:proj-scitex-cards :  2
    cards under the REAL   scope  agent:scitex-cards      : 63

So every agent that followed its own MCP instructions saw ~3% of its cards and
concluded the board had nothing for it. The instructions must therefore name the
agent's OWN resolved identity — and when that identity is unresolvable they must
name NO scope at all, because a silently-wrong example is worse than an honest
absence.

No mocks: the rendered string is asserted directly, and the live-server test
reads the REAL ``mcp.instructions`` off the constructed FastMCP instance.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_cards._mcp_instructions import build_instructions

#: The dead prefix. Anything an agent is TOLD to use must never carry it.
DEAD_PREFIX = "proj-"


# --------------------------------------------------------------------------- #
# The resolved identity is interpolated                                       #
# --------------------------------------------------------------------------- #
def test_instructions_name_the_resolved_identity():
    # Arrange
    agent_id = "scitex-cards"
    # Act
    text = build_instructions(agent_id)
    # Assert
    assert "agent:scitex-cards" in text


def test_instructions_name_whatever_identity_is_resolved():
    # Arrange — any agent, not just this repo's.
    agent_id = "ripple-wm"
    # Act
    text = build_instructions(agent_id)
    # Assert
    assert "agent:ripple-wm" in text and "scitex-cards`" not in text


def _server_instructions_under(env_agent_id: str | None) -> str:
    """Boot the REAL server in a FRESH interpreter and return `mcp.instructions`.

    A subprocess, not an in-process reload: the instructions are built ONCE at
    module import (exactly as they are when an agent starts its MCP server), and
    reloading the module in-process would rebuild the shared FastMCP instance
    without the tools its sibling modules register on it — corrupting the rest of
    the session. This is the honest end-to-end check, and it needs no mocks.
    """
    env = dict(os.environ)
    if env_agent_id is None:
        env.pop("SCITEX_TODO_AGENT_ID", None)
        env.pop("SCITEX_TODO_AGENT", None)  # deprecated name: fails loud if set
    else:
        env["SCITEX_TODO_AGENT_ID"] = env_agent_id
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scitex_cards._mcp_server import mcp; print(mcp.instructions)",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_live_server_instructions_carry_the_env_identity():
    """The REAL server string, built at import, names $SCITEX_TODO_AGENT_ID."""
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-cards[mcp] extra not installed")
    # Act
    text = _server_instructions_under("test-agent-xyz")
    # Assert
    assert "agent:test-agent-xyz" in text


def test_live_server_instructions_name_no_scope_without_an_identity():
    """With the identity unset the REAL server fabricates no `agent:` example."""
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-cards[mcp] extra not installed")
    # Act
    text = _server_instructions_under(None)
    # Assert
    fabricated = [
        m.group(0)
        for m in re.finditer(r"agent:[A-Za-z0-9_.-]+", text)
        if m.group(0) != "agent:<your-agent-id>"
    ]
    assert fabricated == [] and "UNRESOLVED" in text


# --------------------------------------------------------------------------- #
# An UNRESOLVED identity fabricates nothing                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_invents_no_scope_example(unresolved):
    # Arrange / Act
    text = build_instructions(unresolved)
    # Assert — no `agent:<something>` example is fabricated. The only permitted
    # mention is the explicit `<your-agent-id>` placeholder, which names nobody.
    fabricated = [
        m.group(0)
        for m in re.finditer(r"agent:[A-Za-z0-9_.-]+", text)
        if m.group(0) != "agent:<your-agent-id>"
    ]
    assert fabricated == []


@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_says_so_and_says_how_to_discover(unresolved):
    # Arrange / Act
    text = build_instructions(unresolved)
    # Assert — it admits the absence and points at the discovery path.
    assert "UNRESOLVED" in text
    assert "SCITEX_TODO_AGENT_ID" in text
    assert "list_tasks" in text and "resolve_store" in text


def test_unresolved_branch_never_mentions_the_dead_prefix():
    # Arrange / Act
    text = build_instructions(None)
    # Assert
    assert DEAD_PREFIX not in text


# --------------------------------------------------------------------------- #
# No dead `proj-*` identity examples on any agent-facing surface              #
# --------------------------------------------------------------------------- #
def test_no_dead_prefix_anywhere_in_the_mcp_surface():
    """Neither the instructions nor ANY tool description may teach `proj-*`.

    The MCP surface is what an agent reads without being asked to — the
    instructions at session start and every tool's description in its tool
    list. One dead identity in there mis-teaches the whole fleet.
    """
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-cards[mcp] extra not installed")
    from scitex_cards._mcp_server import mcp  # noqa: PLC0415

    # Act
    surfaces = [mcp.instructions or ""]
    for tool in _registered_tools(mcp):
        surfaces.append(getattr(tool, "description", "") or "")
    offenders = [s for s in surfaces if DEAD_PREFIX in s]

    # Assert
    assert offenders == []


#: Identity-TEACHING forms of the dead prefix: a `proj-*` name sitting where an
#: agent id belongs (a scope literal, an identity flag, an identity field, or
#: the identity env var). Deliberately narrow so it does NOT fire on the places
#: `proj-` legitimately survives: the prefix-STRIPPING table in
#: `_users/_identity.py`, historical card ids / task-dir paths, prose about the
#: naming drift itself, and the unrelated `.proj-hide` CSS class.
DEAD_IDENTITY_EXAMPLE = re.compile(
    r"""(?ix)
    (?:
        agent:proj-                                             # scope literal
      | --(?:agent|assignee|author|by|scope|id-prefix)\s+
            ["']?(?:agent:)?proj-                               # CLI flag
      | \b(?:agent|assignee|author|scope|owner)\s*[:=]\s*
            ["']?(?:agent:)?proj-                               # field / kwarg
      | SCITEX_TODO_AGENT_ID\s*=\s*["']?proj-                   # the env var
    )
    """
)

#: Scanned files that keep a `proj-*` identity ON PURPOSE, each a deliberate
#: carve-out rather than a blanket exemption:
#:
#: - ``_cli/_sync_github.py`` — WRITES ``agent="proj-scitex-dev"`` onto the
#:   bundle card it imports. That is a data/behaviour bug of the same family
#:   (the card lands owned by a dead identity), but fixing it changes what the
#:   verb WRITES, not what an agent is TOLD, so it is out of scope for the
#:   instructions fix and is left for a follow-up.
_ALLOWED = {"_cli/_sync_github.py"}


def _agent_facing_files(root: Path) -> list[Path]:
    """Every surface that TEACHES an agent how to address itself.

    Shipped package source + the shipped skills + the two entry-point docs.
    Deliberately EXCLUDES the historical record (``docs/adr/``, ``docs/audit/``,
    ``CHANGELOG.md``) and the bundled example store, where a `proj-*` name is a
    faithful record of what was true then, not an instruction for now.
    """
    pkg = root / "src" / "scitex_cards"
    files = [p for p in pkg.rglob("*.py")]
    files += list((pkg / "_skills").rglob("*.md"))
    files += [p for p in (root / "README.md", root / "docs" / "CHEATSHEET-fleet-todo.md") if p.exists()]
    return files


def test_no_dead_proj_identity_examples_in_shipped_surfaces():
    # Arrange
    root = Path(__file__).resolve().parents[2]
    if not (root / "src" / "scitex_cards").is_dir():  # installed, not a checkout
        pytest.skip("not running from a source checkout")

    # Act
    offenders: list[str] = []
    for path in _agent_facing_files(root):
        rel = path.relative_to(root).as_posix()
        if any(rel.endswith(a) for a in _ALLOWED):
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DEAD_IDENTITY_EXAMPLE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    # Assert
    assert offenders == [], (
        "dead `proj-*` identity examples still shipped — an agent that copies "
        "one filters on a scope holding nothing:\n" + "\n".join(offenders)
    )


def _registered_tools(mcp):
    """Return the registered tool objects across fastmcp's drifting registry API.

    Mirrors the strategy in ``tests/scitex_cards/test__mcp_server.py`` and
    ``_cli/_mcp.py``: prefer the tool manager's private map, fall back to the
    async listing.
    """
    import asyncio  # noqa: PLC0415

    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None) if tm is not None else None
    if isinstance(tools, dict):
        return list(tools.values())

    async def _collect():
        if tm is not None and hasattr(tm, "get_tools"):
            got = await tm.get_tools()
        else:
            got = await mcp.list_tools()
        return list(got.values()) if isinstance(got, dict) else list(got)

    return asyncio.run(_collect())


# EOF
