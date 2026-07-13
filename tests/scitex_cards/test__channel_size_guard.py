#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Size / burst guards for the scitex-cards channel push path.

Regression coverage for the 2026-07-02 incident: 180 solver apptainer
containers died on boot with ``JSON message exceeded maximum buffer size of
1048576 bytes`` when an oversized scitex-cards channel push overflowed the SDK's
1 MB stdio reader. Two guards are pinned here:

* :func:`build_channel_params` truncates an oversized ``content`` body to
  ``MAX_CONTENT_BYTES`` (UTF-8, multibyte-safe) with a "see the card" pointer.
* :func:`drain_once` pushes at most ``MAX_PUSH_PER_DRAIN`` records per call, so
  a large unseen backlog can never burst all at once on first connect.

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` YAML store,
real :mod:`scitex_cards._inbox` enqueue/poll/ack, and a real in-process async
``send`` recorder. Async seams are driven with ``asyncio.run`` (the repo has no
pytest-asyncio).
"""

from __future__ import annotations

import asyncio

from scitex_cards import _inbox
from scitex_cards._channel_guard import MAX_CONTENT_BYTES, MAX_PUSH_PER_DRAIN
from scitex_cards._mcp_channel import build_channel_params, drain_once


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


# --------------------------------------------------------------------------- #
# build_channel_params — body size cap                                        #
# --------------------------------------------------------------------------- #
def test_oversized_body_truncated_within_cap_with_pointer():
    # An ASCII body well over the cap.
    body = "x" * (MAX_CONTENT_BYTES + 5000)
    params = build_channel_params({"body": body, "card_id": "c42"})
    content = params["content"]

    # The FINAL content (prefix + suffix) fits the cap.
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES
    # It ends with the truncation pointer AND names the card.
    assert content.endswith("on the board]")
    assert "truncated" in content
    assert "c42" in content


def test_oversized_multibyte_body_is_char_boundary_safe():
    # A body made entirely of 3-byte UTF-8 chars — a naive byte-slice would
    # split a multibyte char and raise/produce invalid UTF-8.
    body = "あ" * MAX_CONTENT_BYTES  # each char = 3 bytes, far over the cap
    params = build_channel_params({"body": body, "card_id": "c7"})
    content = params["content"]

    # Fits the cap and is valid UTF-8 (no split multibyte char).
    encoded = content.encode("utf-8")
    assert len(encoded) <= MAX_CONTENT_BYTES
    assert encoded.decode("utf-8") == content  # round-trips ⇒ no broken char
    assert content.endswith("on the board]")
    assert "c7" in content


def test_oversized_body_without_card_id_uses_generic_pointer():
    body = "y" * (MAX_CONTENT_BYTES + 100)
    params = build_channel_params({"body": body})  # no card_id
    content = params["content"]
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES
    assert content.endswith("[truncated — see the board]")


def test_normal_body_is_unchanged():
    body = "a short normal notification body"
    params = build_channel_params({"body": body, "card_id": "c1"})
    assert params["content"] == body
    assert "truncated" not in params["content"]


def test_truncated_params_meta_values_all_strings():
    body = "z" * (MAX_CONTENT_BYTES + 10)
    params = build_channel_params(
        {
            "body": body,
            "card_id": "c9",
            "event_type": "reassigned",
            "actor": "bob",
            "ts": "2026-07-02T00:00:00Z",
            "id": "n_1",
        }
    )
    for key, value in params["meta"].items():
        assert isinstance(value, str), f"meta[{key!r}] is {type(value)} not str"


# --------------------------------------------------------------------------- #
# drain_once — per-drain batch cap                                            #
# --------------------------------------------------------------------------- #
def _enqueue_n(agent, n, store, *, start=0):
    for i in range(start, start + n):
        rec = _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="bob",
            ts=f"2026-07-02T00:00:{i:02d}Z",
            store=store,
        )
        assert rec, f"enqueue {i} failed"


def test_drain_caps_batch_and_next_drain_delivers_the_rest(tmp_path):
    store = _store(tmp_path)
    agent = "agent-burst"
    extra = 7
    total = MAX_PUSH_PER_DRAIN + extra
    _enqueue_n(agent, total, store)

    # First drain pushes EXACTLY the cap and acks exactly those.
    recorder1 = _SendRecorder()
    pushed1 = asyncio.run(drain_once(agent, recorder1, store=store))
    assert pushed1 == MAX_PUSH_PER_DRAIN
    assert len(recorder1.calls) == MAX_PUSH_PER_DRAIN

    # Exactly the remainder is still unseen (the cap acked only what it pushed).
    pending = _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store)
    assert len(pending) == extra

    # Second drain delivers the rest.
    recorder2 = _SendRecorder()
    pushed2 = asyncio.run(drain_once(agent, recorder2, store=store))
    assert pushed2 == extra
    assert len(recorder2.calls) == extra

    # Every enqueued record is delivered exactly once across the two drains.
    all_bodies = [c["content"] for c in recorder1.calls + recorder2.calls]
    assert len(all_bodies) == total
    assert len(set(all_bodies)) == total

    # Nothing left unseen after the second drain.
    assert (
        _inbox.poll_inbox(agent, unseen_only=True, mark_seen=False, store=store) == []
    )


def test_drain_under_cap_pushes_all(tmp_path):
    store = _store(tmp_path)
    agent = "agent-small"
    _enqueue_n(agent, 3, store)
    recorder = _SendRecorder()
    pushed = asyncio.run(drain_once(agent, recorder, store=store))
    assert pushed == 3
    assert len(recorder.calls) == 3


# --------------------------------------------------------------------------- #
# DM wire shape — fleet convention (scitex-dev spec v1): a dm record must     #
# render a2a-compatible: source=<sender>, conversation_id=<thread key>.       #
# --------------------------------------------------------------------------- #
def test_dm_record_renders_a2a_wire_shape():
    rec = {
        "id": "m_abc123def456",
        "event_type": "dm",
        "card_id": "dm:neurovista::operator",
        "body": "please check the compute queue",
        "actor": "operator",
        "ts": "2026-07-07T12:00:00Z",
    }
    meta = build_channel_params(rec)["meta"]
    assert meta["source"] == "operator", "DM must render the SENDER as source"
    assert meta["conversation_id"] == "dm:neurovista::operator"
    assert meta["msg_id"] == "m_abc123def456"
    for value in meta.values():
        assert isinstance(value, str)


def test_non_dm_record_keeps_channel_source():
    rec = {
        "id": "n_1",
        "event_type": "reminder",
        "card_id": "some-card",
        "body": "digest",
        "actor": "notifyd",
        "ts": "2026-07-07T12:00:00Z",
    }
    meta = build_channel_params(rec)["meta"]
    assert meta["source"] == "stodo", "non-DM keeps the configured channel label"
    assert "conversation_id" not in meta


def test_dm_record_missing_actor_falls_back_to_channel_source():
    rec = {
        "id": "m_x",
        "event_type": "dm",
        "card_id": "dm:a::b",
        "body": "b",
        "actor": "",
        "ts": "2026-07-07T12:00:00Z",
    }
    meta = build_channel_params(rec)["meta"]
    assert meta["source"] == "stodo"
    assert meta["conversation_id"] == "dm:a::b"


# EOF
