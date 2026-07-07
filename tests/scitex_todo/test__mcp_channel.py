#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scitex-todo's OWN standalone channel-notification server.

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
:mod:`scitex_todo._inbox` enqueue/poll/ack, and a real (in-process) async
``send`` recorder — no ``unittest.mock``. The live MCP stdio session is not
exercised here (it is driven only at runtime); instead the pure logic seams
(``build_channel_params``, ``drain_once``, ``resolve_agent_id``) are tested
with real objects so the receive→push path is covered end to end.
"""

from __future__ import annotations

import asyncio

import pytest

from scitex_todo import _inbox
from scitex_todo._mcp_channel import (
    build_channel_params,
    drain_once,
    recipient_keys,
    resolve_agent_id,
    resolve_agent_id_optional,
)
from scitex_todo._users import register_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


class _SendRecorder:
    """A real async ``send`` callable — records every pushed params payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)


class _FailingSend:
    """A real async ``send`` that always raises — exercises the retry path."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, params: dict) -> None:
        self.calls.append(params)
        raise RuntimeError("simulated push failure")


# --------------------------------------------------------------------------- #
# build_channel_params — every meta value is a string                         #
# --------------------------------------------------------------------------- #
def test_build_channel_params_all_meta_strings():
    rec = {
        "id": "n_abc123",
        "event_type": "reassigned",
        "card_id": "c1",
        "body": "Card c1 reassigned to you (by bob)",
        "actor": "bob",
        "ts": "2026-06-28T10:00:00Z",
        "seen": False,
    }
    params = build_channel_params(rec)
    assert params["content"] == rec["body"]
    meta = params["meta"]
    # source drives the `<- stodo` render (the fleet's short sender-identity
    # label — distinct from the scitex-todo agent id so the TUI doesn't
    # confuse system pushes with the agent's own messages).
    assert meta["source"] == "stodo"
    # EVERY meta value MUST be a string (Claude's Zod validator).
    for key, value in meta.items():
        assert isinstance(value, str), f"meta[{key!r}] is {type(value)} not str"
    assert meta["card_id"] == "c1"
    assert meta["event_type"] == "reassigned"
    assert meta["actor"] == "bob"
    assert meta["msg_id"] == "n_abc123"
    assert meta["ts"] == "2026-06-28T10:00:00Z"


def test_build_channel_params_custom_source():
    params = build_channel_params({"body": "hi"}, source="my-board")
    assert params["meta"]["source"] == "my-board"


def test_build_channel_params_missing_fields_become_empty_strings():
    # A sparse record (only a body) must still produce all-string meta.
    params = build_channel_params({"body": "x"})
    meta = params["meta"]
    for key in ("ts", "event_type", "card_id", "actor", "msg_id"):
        assert meta[key] == ""
        assert isinstance(meta[key], str)


def test_build_channel_params_none_body_becomes_empty_string():
    params = build_channel_params({"body": None})
    assert params["content"] == ""


# --------------------------------------------------------------------------- #
# drain_once — real inbox, fake send recorder                                 #
# --------------------------------------------------------------------------- #
def test_drain_once_pushes_each_unseen_and_acks(tmp_path):
    store = _store(tmp_path)
    agent = "agent-x"
    r1 = _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c1",
        body="body one",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    r2 = _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c2",
        body="body two",
        actor="alice",
        ts="2026-06-28T10:01:00Z",
        store=store,
    )
    assert r1 and r2

    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once(agent, recorder, store=store))

    assert pushed == 2
    assert len(recorder.calls) == 2
    bodies = {c["content"] for c in recorder.calls}
    assert bodies == {"body one", "body two"}
    for call in recorder.calls:
        assert call["meta"]["source"] == "stodo"
        for value in call["meta"].values():
            assert isinstance(value, str)

    # After a successful push, the records are ack'd — a second drain pushes
    # nothing (unseen-only cursor advanced).
    recorder2 = _SendRecorder()
    pushed2 = asyncio.run(drain_once(agent, recorder2, store=store))
    assert pushed2 == 0
    assert recorder2.calls == []


def test_drain_once_failed_send_is_not_acked_and_retried(tmp_path):
    store = _store(tmp_path)
    agent = "agent-y"
    _inbox.enqueue(
        agent,
        event_type="reassigned",
        card_id="c1",
        body="retry me",
        actor="bob",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )

    failing = _FailingSend()
    pushed = asyncio.run(drain_once(agent, failing, store=store))
    # The send raised → nothing counted as pushed, nothing ack'd.
    assert pushed == 0
    assert len(failing.calls) == 1

    # Still unseen — the failed record is retried on the next drain.
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    assert len(pending) == 1
    assert pending[0]["body"] == "retry me"

    # Next drain with a working send delivers + acks it.
    recorder = _SendRecorder()
    pushed2 = asyncio.run(drain_once(agent, recorder, store=store))
    assert pushed2 == 1
    assert len(recorder.calls) == 1
    assert (
        _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store) == []
    )


def test_drain_once_empty_inbox_is_noop(tmp_path):
    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once("nobody", recorder, store=_store(tmp_path)))
    assert pushed == 0
    assert recorder.calls == []


def test_drain_once_custom_source_propagates(tmp_path):
    store = _store(tmp_path)
    agent = "agent-z"
    _inbox.enqueue(
        agent,
        event_type="completed",
        card_id="c9",
        body="hi",
        actor=None,
        ts="2026-06-28T10:00:00Z",
        store=store,
    )
    recorder = _SendRecorder()
    asyncio.run(drain_once(agent, recorder, source="custom-board", store=store))
    assert recorder.calls[0]["meta"]["source"] == "custom-board"


# --------------------------------------------------------------------------- #
# recipient_keys — consumer keys EXACTLY like the producer (registered→id)    #
# --------------------------------------------------------------------------- #
def test_recipient_keys_unregistered_is_raw_name_only(tmp_path):
    store = _store(tmp_path)
    assert recipient_keys("ghost", store=store) == ["ghost"]


def test_recipient_keys_registered_name_includes_resolved_user_id(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    keys = recipient_keys("alice", store=store)
    # Raw name first (back-compat), then the resolved user-id the producer
    # enqueues under for a REGISTERED name.
    assert keys == ["alice", alice.id]


def test_drain_once_delivers_records_enqueued_under_resolved_user_id(tmp_path):
    # The live silent-drop: the notify dispatcher enqueues to a registered
    # agent's USER-ID, but the channel was launched with the raw NAME. The
    # channel must still find + deliver it (it now drains both keys).
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    _inbox.enqueue(
        alice.id,  # producer key for a registered name
        event_type="reassigned",
        card_id="c1",
        body="assigned to you",
        actor="operator",
        ts="2026-06-28T10:00:00Z",
        store=store,
    )

    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once("alice", recorder, store=store))

    assert pushed == 1
    assert recorder.calls[0]["content"] == "assigned to you"
    # Ack'd on the user-id key → a second drain pushes nothing.
    assert asyncio.run(drain_once("alice", _SendRecorder(), store=store)) == 0


# --------------------------------------------------------------------------- #
# resolve_agent_id — fail loud on unresolved                                  #
# --------------------------------------------------------------------------- #
def test_resolve_agent_id_explicit_arg():
    assert resolve_agent_id("my-agent") == "my-agent"


def test_resolve_agent_id_from_env(monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    assert resolve_agent_id() == "env-agent"


def test_resolve_agent_id_unresolved_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    with pytest.raises(RuntimeError) as exc:
        resolve_agent_id()
    assert "SCITEX_TODO_AGENT_ID" in str(exc.value)


def test_resolve_agent_id_unknown_sentinel_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    with pytest.raises(RuntimeError):
        resolve_agent_id("unknown")


def test_resolve_agent_id_blank_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    with pytest.raises(RuntimeError):
        resolve_agent_id("   ")


def test_resolve_agent_id_unexpanded_placeholder_arg_raises(monkeypatch):
    # The launcher passed the literal text instead of expanding it — Claude
    # Code only expands the `${VAR}` brace form, never bare `$VAR`. Draining an
    # inbox keyed by that literal would silently deliver nothing, so fail loud.
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    with pytest.raises(RuntimeError) as exc:
        resolve_agent_id("$SCITEX_TODO_AGENT_ID")
    assert "placeholder" in str(exc.value)


def test_resolve_agent_id_unexpanded_placeholder_braces_arg_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    with pytest.raises(RuntimeError):
        resolve_agent_id("${SCITEX_TODO_AGENT_ID}")


def test_resolve_agent_id_unexpanded_placeholder_from_env_raises(monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "$SCITEX_TODO_AGENT_ID")
    with pytest.raises(RuntimeError):
        resolve_agent_id()


def test_resolve_agent_id_current_var_wins_over_stale_deprecated(monkeypatch):
    """The CURRENT var wins: a valid $SCITEX_TODO_AGENT_ID must NOT be disabled
    by a leftover stale $SCITEX_TODO_AGENT. This is the incident fix — fleet
    agents carry a stale ambient old-name export baked in by an old injector;
    a correctly configured AGENT_ID must still resolve (so the poll loop runs)."""
    # Arrange: a valid NEW-name id AND the stale old name both set.
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act / Assert: the current var wins, no raise.
    assert resolve_agent_id() == "env-agent"


def test_resolve_agent_id_only_deprecated_env_var_fails_loud(monkeypatch):
    """With NO current $SCITEX_TODO_AGENT_ID but the renamed-away
    $SCITEX_TODO_AGENT still exported, resolution fails LOUD pointing at the new
    name — a genuine reliance on the old var the operator must migrate."""
    # Arrange: only the deprecated old name is set.
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    # Act / Assert
    with pytest.raises(RuntimeError, match="SCITEX_TODO_AGENT_ID"):
        resolve_agent_id()


# === resolve_agent_id_optional — the unified server's tools-only fallback =====


def test_resolve_agent_id_optional_returns_id_when_set(monkeypatch):
    """With an identity, the unified server enables the digest push."""
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    assert resolve_agent_id_optional() == "env-agent"


def test_resolve_agent_id_optional_returns_none_when_unset(monkeypatch):
    """No identity ⇒ the unified server serves tools ONLY (push disabled).
    It must NOT raise — the tools surface has to work without an agent id."""
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    assert resolve_agent_id_optional() is None


def test_resolve_agent_id_optional_none_on_deprecated_env(monkeypatch):
    """ONLY the deprecated $SCITEX_TODO_AGENT set (no current AGENT_ID) makes
    resolve fail loud; the optional variant swallows it to None (tools-only)
    rather than crashing the server — the loud warning still surfaces it."""
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    assert resolve_agent_id_optional() is None


def test_resolve_agent_id_optional_returns_id_when_both_vars_set(monkeypatch):
    """THE key regression that re-enables the poll loop: a valid AGENT_ID plus a
    stale deprecated $SCITEX_TODO_AGENT must return the id (NOT None). Before the
    fix the mere presence of the old var made resolve fail loud → optional
    returned None → the digest poll loop never started (server connected, tools
    worked, but no channel notifications were ever pushed)."""
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "env-agent")
    monkeypatch.setenv("SCITEX_TODO_AGENT", "legacy-agent")
    assert resolve_agent_id_optional() == "env-agent"


# === unified server: one scitex-todo serves tools AND declares the channel ====


def test_unified_server_keeps_tools_and_adds_channel_capability():
    """The unified `mcp start` runs FastMCP's underlying low-level server (which
    has the card tools) with the `claude/channel` capability added — so ONE
    server both serves tools AND pushes the digest. Adding the channel capability
    must NOT drop the tools capability."""
    fastmcp = pytest.importorskip("fastmcp")  # noqa: F841
    from scitex_todo._mcp_server import mcp

    opts = mcp._mcp_server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    assert opts.capabilities.tools is not None, "tools capability was dropped"
    assert opts.capabilities.experimental == {"claude/channel": {}}


def test_unified_start_wiring_present():
    """`scitex-todo mcp start` is wired to the unified server (tools + push)."""
    from scitex_todo._cli import _mcp

    assert hasattr(_mcp, "_run_unified_server")
    assert hasattr(_mcp, "_attach_unified_start")


# EOF
