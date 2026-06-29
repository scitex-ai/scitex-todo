#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo's OWN standalone channel-notification MCP server (no sac).

A long-running MCP **stdio** server that pushes unsolicited
``notifications/claude/channel`` messages into the Claude session, draining
THIS agent's scitex-todo inbox (:mod:`scitex_todo._inbox`). Claude renders
each push as ``<- scitex-todo`` in the agent's terminal — driven by
``meta.source = "scitex-todo"``.

Why a hand-rolled low-level server (NOT FastMCP)
------------------------------------------------
FastMCP constructs its ``ServerSession`` internally and never exposes it, so
a side channel (the inbox poll loop) would have no session handle to push
server-initiated ``notifications/claude/channel`` through. We therefore use
the LOW-LEVEL :class:`mcp.server.lowlevel.Server` + own the ``ServerSession``
ourselves so the poll loop can push. The initialization options MUST declare
the ``claude/channel`` experimental capability or Claude Code drops every
push ("server did not declare claude/channel capability").

The shape of this server (own-the-session + manual incoming-message drive)
is modelled on sac's ``_mcp/channel.py``, but this module has ZERO sac
dependency: it drains the standalone :mod:`scitex_todo._inbox` pull-inbox
instead of subscribing to sac's bus. NO ``import scitex_agent_container``,
no ``sac`` shell-out — scitex-todo, sac, and claude-code-telegrammer are
three independent standalone mechanisms.

Wire format (exact)
-------------------
JSON-RPC notification, method ``notifications/claude/channel``, params
``{"content": <str>, "meta": {<all-string-values>}}``. EVERY meta value MUST
be a string or Claude's Zod validator silently drops the pushed turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from . import _inbox

logger = logging.getLogger(__name__)

#: Env var carrying the agent identity — same key the rest of the package
#: uses (``scitex_todo._store.ENV_AGENT``). Imported lazily in
#: :func:`resolve_agent_id` to avoid a heavy import at module load.
_ENV_AGENT = "SCITEX_TODO_AGENT"

#: Default poll interval (seconds) between inbox drains.
_DEFAULT_INTERVAL = 5.0

#: Default ``meta.source`` value — drives the ``<- scitex-todo`` render.
_DEFAULT_SOURCE = "scitex-todo"


# --------------------------------------------------------------------------- #
# Pure logic (tested directly — no live MCP session needed)                   #
# --------------------------------------------------------------------------- #
def resolve_agent_id(arg: str | None = None) -> str:
    """Resolve the agent id; FAIL LOUD when unresolved.

    Precedence: explicit ``arg`` → ``$SCITEX_TODO_AGENT``. Deliberately does
    NOT fall back to ``getpass.getuser()`` / ``"unknown"`` — a channel server
    that drains "unknown"'s inbox would silently deliver the wrong agent's
    notifications. The operator mandate (constitution rule 2 "fail fast and
    fail loud, NO silent fallbacks") requires a real identity here.

    Raises
    ------
    RuntimeError
        When the id resolves to empty, the ``"unknown"`` sentinel, or an
        unexpanded ``$``-placeholder, with an ACTIONABLE hint.
    """
    resolved = (arg or os.environ.get(_ENV_AGENT) or "").strip()
    if not resolved or resolved == "unknown":
        raise RuntimeError(
            "scitex-todo mcp channel: agent id unresolved — set "
            "SCITEX_TODO_AGENT=<your-agent> or pass --agent <id>. The channel "
            "server must drain a REAL agent's inbox; no silent fallback to a "
            "blank/'unknown' id."
        )
    # An id that still looks like an env placeholder (e.g. "$SCITEX_TODO_AGENT"
    # or "${SCITEX_TODO_AGENT}") means the launcher passed the literal text
    # instead of expanding it — Claude Code's .mcp.json only expands the
    # ``${VAR}`` (braces) form, never bare ``$VAR``. Draining an inbox keyed by
    # that literal silently delivers nothing; fail loud instead of polling a
    # dead key.
    if resolved.startswith("$"):
        raise RuntimeError(
            f"scitex-todo mcp channel: agent id is an unexpanded placeholder "
            f"({resolved!r}) — the launcher passed the literal text instead of "
            "the value. In .mcp.json use the brace form "
            '"SCITEX_TODO_AGENT": "${SCITEX_TODO_AGENT}" (Claude Code does not '
            'expand bare "$VAR"), or pass a literal --agent <id>.'
        )
    return resolved


def build_channel_params(rec: dict[str, Any], *, source: str = _DEFAULT_SOURCE) -> dict[str, Any]:
    """Project an inbox record onto the Claude channel notification shape.

    Returns ``{"content": <body str>, "meta": {<all-string-values>}}``. EVERY
    meta value is stringified — the Claude Code client schema types every
    ``meta`` value as a string and a non-string trips its Zod validator,
    silently dropping the pushed turn. ``meta.source`` drives the
    ``<- scitex-todo`` render.
    """
    return {
        "content": str(rec.get("body") or ""),
        "meta": {
            "source": str(source),
            "ts": str(rec.get("ts") or ""),
            "event_type": str(rec.get("event_type") or ""),
            "card_id": str(rec.get("card_id") or ""),
            "actor": str(rec.get("actor") or ""),
            "msg_id": str(rec.get("id") or ""),
        },
    }


async def drain_once(
    agent_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    source: str = _DEFAULT_SOURCE,
    store: str | None = None,
) -> int:
    """Drain one batch of unseen notifications, pushing each via ``send``.

    The seam that makes the receive→push path testable without a live MCP
    session. Reads UNSEEN records (``mark_seen=False`` — we ack ONLY after a
    successful push so a push failure is retried next drain), builds the
    channel params, awaits ``send(params)``, and on success ack's the record.

    Fail-soft per record: one bad push (``send`` raises) leaves THAT record
    un-ack'd (retried next drain) and does not abort the rest of the batch.

    Parameters
    ----------
    agent_id : str
        The inbox key to drain.
    send : Callable[[dict], Awaitable[None]]
        Async callable that delivers one channel-params payload (the real
        server passes a closure over the MCP session's ``send_message``).
    source : str
        ``meta.source`` value (default ``"scitex-todo"``).
    store : str | None
        Store path override (default: the resolved task store).

    Returns
    -------
    int
        The number of records successfully pushed AND ack'd this drain.
    """
    records = _inbox.poll_inbox(
        agent_id, unseen_only=True, mark_seen=False, store=store
    )
    pushed = 0
    for rec in records:
        params = build_channel_params(rec, source=source)
        try:
            await send(params)
        except Exception as exc:  # noqa: BLE001 — one bad push must not kill the loop
            logger.warning(
                "scitex-todo channel: pushing notification %s failed: %s",
                rec.get("id"),
                exc,
            )
            continue
        # Ack ONLY after a successful send — a push failure stays unseen and
        # is retried on the next drain.
        rec_id = rec.get("id")
        if rec_id:
            try:
                _inbox.ack(agent_id, [rec_id], store=store)
            except Exception as exc:  # noqa: BLE001 — ack failure shouldn't kill the loop
                logger.warning(
                    "scitex-todo channel: ack of %s failed: %s", rec_id, exc
                )
        pushed += 1
    return pushed


# --------------------------------------------------------------------------- #
# Live MCP stdio server (own the session so we can push)                      #
# --------------------------------------------------------------------------- #
async def _poll_loop(
    agent_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    interval: float,
    source: str,
) -> None:
    """Background task: drain the inbox every ``interval`` seconds, forever.

    Fail-soft: a drain that raises is logged and retried next tick — the loop
    is long-lived and must survive transient store/IO errors.
    """
    while True:
        try:
            await drain_once(agent_id, send, source=source)
        except Exception as exc:  # noqa: BLE001 — keep the long-lived loop alive
            logger.warning("scitex-todo channel: drain tick failed: %s", exc)
        await asyncio.sleep(interval)


async def _serve(
    read_stream: Any,
    write_stream: Any,
    *,
    agent_id: str,
    source: str,
    interval: float,
) -> None:
    """Drive the MCP session AND the inbox poll loop over the given streams.

    We deliberately do NOT call ``Server.run``: it constructs its
    ``ServerSession`` internally and never exposes it, so the poll loop would
    have no session handle to push ``notifications/claude/channel`` through.
    Owning the session here is the supported way to send server-initiated
    notifications with the low-level API.
    """
    from contextlib import AsyncExitStack

    import anyio
    from mcp.server.lowlevel import Server
    from mcp.server.session import ServerSession
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCNotification

    server: Server = Server(name=f"scitex-todo-channel-{agent_id}")

    async with AsyncExitStack() as stack:
        lifespan_context = await stack.enter_async_context(server.lifespan(server))
        session = await stack.enter_async_context(
            ServerSession(
                read_stream,
                write_stream,
                # Declare the `claude/channel` experimental capability in the
                # initialize response — without it Claude Code logs "Channel
                # notifications skipped: server did not declare claude/channel
                # capability" and drops every push.
                server.create_initialization_options(
                    experimental_capabilities={"claude/channel": {}},
                ),
            )
        )

        async def _send(params: dict[str, Any]) -> None:
            await session.send_message(
                SessionMessage(
                    JSONRPCMessage(
                        JSONRPCNotification(
                            jsonrpc="2.0",
                            method="notifications/claude/channel",
                            params=params,
                        )
                    )
                )
            )

        poll_task: asyncio.Task[None] = asyncio.create_task(
            _poll_loop(agent_id, _send, interval=interval, source=source)
        )

        try:
            async with anyio.create_task_group() as tg:
                async for message in session.incoming_messages:
                    tg.start_soon(
                        server._handle_message,
                        message,
                        session,
                        lifespan_context,
                        False,
                    )
        finally:
            poll_task.cancel()


async def _run(*, agent_id: str, source: str, interval: float) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await _serve(
            read_stream,
            write_stream,
            agent_id=agent_id,
            source=source,
            interval=interval,
        )


def main(
    name: str = _DEFAULT_SOURCE,
    interval: float = _DEFAULT_INTERVAL,
    agent: str | None = None,
) -> None:
    """CLI entry point — run the channel server in the foreground (stdio).

    ``name`` sets ``meta.source`` (default ``"scitex-todo"``). ``agent``
    overrides the agent id; otherwise it is resolved from
    ``$SCITEX_TODO_AGENT`` (fail-loud when unresolved).
    """
    agent_id = resolve_agent_id(agent)
    asyncio.run(_run(agent_id=agent_id, source=name, interval=float(interval)))


__all__ = [
    "build_channel_params",
    "drain_once",
    "main",
    "resolve_agent_id",
]

# EOF
