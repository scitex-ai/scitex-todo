#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo mcp channel` verb — the standalone channel server entry.

Extracted from ``_cli/_mcp.py`` (which is at its size cap) so the verb can be
wired into BOTH the scitex-dev-helper path and the hand-rolled fallback
``mcp`` group. The actual server lives in :mod:`scitex_todo._mcp_channel`;
this is the thin click wrapper.
"""

from __future__ import annotations

import click


def attach_channel_verb(mcp_group: click.Group) -> None:
    """Attach the ``channel`` verb to an existing ``mcp`` click group.

    Runs scitex-todo's OWN long-running MCP stdio server (foreground) that
    pushes unsolicited ``notifications/claude/channel`` messages (rendered
    ``<- scitex-todo``) into the Claude session, draining this agent's
    standalone inbox. ZERO external-runtime dependency. This is what an
    ``.mcp.json`` entry execs.
    """

    @mcp_group.command(
        "channel",
        help=(
            "Run the standalone channel-notification server (stdio).\n\n"
            "Pushes `notifications/claude/channel` (rendered `<- scitex-todo`)\n"
            "into the Claude session, draining this agent's inbox. ZERO sac\n"
            "dependency. Agent id resolves from $SCITEX_TODO_AGENT (or --agent).\n\n"
            "Example:\n"
            "  scitex-todo mcp channel --name scitex-todo --interval 5"
        ),
    )
    @click.option(
        "--name",
        default="scitex-todo",
        show_default=True,
        help="Sets meta.source (drives the `<- scitex-todo` render).",
    )
    @click.option(
        "--interval",
        type=float,
        default=5.0,
        show_default=True,
        help="Seconds between inbox drains.",
    )
    @click.option(
        "--agent",
        default=None,
        help="Override the agent id (default: $SCITEX_TODO_AGENT, fail-loud).",
    )
    def channel(name, interval, agent) -> None:
        try:
            from .._mcp_channel import main as channel_main
        except ImportError as exc:  # pragma: no cover — mcp SDK missing
            raise click.ClickException(
                "scitex-todo mcp channel needs the MCP SDK: "
                f"pip install 'scitex-todo[mcp]' ({exc})"
            ) from None
        try:
            channel_main(name=name, interval=interval, agent=agent)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from None


__all__ = ["attach_channel_verb"]

# EOF
