#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo mcp channel` verb — the standalone channel server entry.

Extracted from ``_cli/_mcp.py`` (which is at its size cap) so the verb can be
wired into BOTH the scitex-dev-helper path and the hand-rolled fallback
``mcp`` group. The actual server lives in :mod:`scitex_cards._mcp_channel`;
this is the thin click wrapper.
"""

from __future__ import annotations

import click

from ._compat import spec_command_kwargs


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
        **spec_command_kwargs(
            summary="Run the standalone channel-notification server (stdio).",
            description=(
                "Pushes `notifications/claude/channel` (rendered `<- stodo`) "
                "into the Claude session, draining this agent's inbox. ZERO "
                "sac dependency. The agent id resolves from "
                "$SCITEX_CARDS_AGENT_ID (or --agent); --name / --interval "
                "fall back to $SCITEX_CARDS_CHANNEL_SOURCE / "
                "$SCITEX_CARDS_CHANNEL_INTERVAL then the defaults, so the "
                ".mcp.json entry can carry zero config args.",
            ),
            examples=(
                (
                    "{prog} mcp channel --name stodo --interval 5",
                    "What an .mcp.json entry execs.",
                ),
            ),
        ),
    )
    @click.option(
        "--name",
        default=None,
        help=(
            "Sets meta.source (drives the `<- stodo` render). "
            "Default: $SCITEX_TODO_CHANNEL_SOURCE, then 'stodo'."
        ),
    )
    @click.option(
        "--interval",
        type=float,
        default=None,
        help=(
            "Seconds between inbox drains. "
            "Default: $SCITEX_TODO_CHANNEL_INTERVAL, then 5.0."
        ),
    )
    @click.option(
        "--agent",
        default=None,
        help="Override the agent id (default: $SCITEX_TODO_AGENT_ID, fail-loud).",
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
