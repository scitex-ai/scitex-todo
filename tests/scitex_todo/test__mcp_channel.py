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
    resolve_agent_id,
)


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
    # source drives the `<- scitex-todo` render.
    assert meta["source"] == "scitex-todo"
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
        assert call["meta"]["source"] == "scitex-todo"
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
# resolve_agent_id — fail loud on unresolved                                  #
# --------------------------------------------------------------------------- #
def test_resolve_agent_id_explicit_arg():
    assert resolve_agent_id("my-agent") == "my-agent"


def test_resolve_agent_id_from_env(monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT", "env-agent")
    assert resolve_agent_id() == "env-agent"


def test_resolve_agent_id_unresolved_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT", raising=False)
    with pytest.raises(RuntimeError) as exc:
        resolve_agent_id()
    assert "SCITEX_TODO_AGENT" in str(exc.value)


def test_resolve_agent_id_unknown_sentinel_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        resolve_agent_id("unknown")


def test_resolve_agent_id_blank_raises(monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        resolve_agent_id("   ")


# EOF
