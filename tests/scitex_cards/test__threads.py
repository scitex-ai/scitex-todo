#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the operator↔agent DM thread store (:mod:`scitex_cards._threads`).

Covers the scitex-dev DM convention v1 contract:

  - append → get_thread round-trip with the canonical record shape
    ``{id, thread, from, to, body, ts, read}``.
  - sorted thread key: ``thread_key(a, b) == thread_key(b, a)``.
  - STORE ISOLATION: threads live in the ``threads.yaml`` SIDECAR next to
    the tasks store, never inside ``tasks.yaml``, with their own lockfile.
  - crash-safe write: the sidecar reparses cleanly after writes.
  - mark_read (all-for-reader and id-scoped) + list_threads unread counts.
  - dm-dispatch: append_message enqueues an ``event_type="dm"`` record into
    the recipient's EXISTING pull-inbox in the real tasks.yaml store.

Real tmp_path stores throughout; no mocks (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards import _threads
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import (
    append_message,
    get_thread,
    list_threads,
    mark_read,
    thread_key,
    threads_path,
)
from scitex_cards._yaml import safe_load


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml store (threads sidecar lands next to it).

    Git autocommit is disabled so the inbox write path stays fast and
    deterministic under test.
    """
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


# === thread key ============================================================


def test_thread_key_sorts_peers_lexicographically():
    # Arrange / Act
    key = thread_key("zeta", "alpha")
    # Assert
    assert key == "dm:alpha::zeta"


def test_thread_key_is_direction_agnostic():
    # Arrange / Act / Assert
    assert thread_key("operator", "agent-x") == thread_key("agent-x", "operator")


def test_peers_of_inverts_thread_key():
    # Arrange
    key = thread_key("operator", "agent-x")
    # Act
    peers = _threads.peers_of(key)
    # Assert
    assert peers == ("agent-x", "operator")


# === append / get round-trip ===============================================


def test_append_then_get_thread_round_trip(store):
    # Arrange / Act
    sent = append_message("operator", "agent-x", "hello there", store=store)
    got = get_thread("agent-x", "operator", store=store)
    # Assert — canonical record shape, both directions resolve the thread.
    assert got == [sent]
    assert set(sent) == {"id", "thread", "from", "to", "body", "ts", "read"}
    assert sent["from"] == "operator"
    assert sent["to"] == "agent-x"
    assert sent["body"] == "hello there"
    assert sent["thread"] == "dm:agent-x::operator"
    assert sent["read"] is False
    assert sent["id"].startswith("m_")
    assert sent["ts"].endswith("Z")


def test_append_preserves_chronological_order(store):
    # Arrange
    append_message("operator", "agent-x", "first", store=store)
    append_message("agent-x", "operator", "second", store=store)
    # Act
    msgs = get_thread("operator", "agent-x", store=store)
    # Assert
    assert [m["body"] for m in msgs] == ["first", "second"]


def test_append_rejects_empty_body(store):
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        append_message("operator", "agent-x", "   ", store=store)


def test_get_thread_on_missing_sidecar_returns_empty(store):
    # Arrange — no message ever appended, sidecar absent.
    assert not threads_path(store).exists()
    # Act / Assert — never raises.
    assert get_thread("operator", "ghost", store=store) == []
    assert list_threads(store=store) == {}


# === store isolation (sidecar, own lock) ===================================


def test_threads_live_in_sidecar_not_tasks_yaml(store):
    # Arrange / Act
    append_message("operator", "agent-x", "hi", store=store)
    # Assert — sidecar exists next to tasks.yaml and holds the thread...
    sidecar = threads_path(store)
    assert sidecar == store.parent / "threads.yaml"
    assert sidecar.exists()
    doc = safe_load(sidecar.read_text(encoding="utf-8"))
    assert "dm:agent-x::operator" in doc["threads"]
    # ...while tasks.yaml carries NO threads section (isolation).
    tasks_doc = safe_load(store.read_text(encoding="utf-8"))
    assert "threads" not in tasks_doc


def test_threads_use_their_own_lockfile(store):
    # Arrange / Act
    append_message("operator", "agent-x", "hi", store=store)
    # Assert — a separate lock sentinel, not the tasks.yaml one.
    assert (store.parent / ".threads.yaml.lock").exists()


# === crash-safe write ======================================================


def test_sidecar_reparses_cleanly_after_writes(store):
    # Arrange — several writes across two threads.
    for i in range(3):
        append_message("operator", "agent-x", f"msg {i}", store=store)
    append_message("agent-y", "operator", "other thread", store=store)
    # Act — reparse the raw bytes exactly like the verify step does.
    doc = safe_load(threads_path(store).read_text(encoding="utf-8"))
    # Assert — full structure survives, no tmp sidecar left behind.
    assert len(doc["threads"]) == 2
    assert len(doc["threads"]["dm:agent-x::operator"]) == 3
    assert not (store.parent / ".threads.yaml.tmp").exists()


# === mark_read / list_threads ==============================================


def test_mark_read_all_for_reader(store):
    # Arrange — two inbound to operator, one outbound.
    append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    append_message("operator", "agent-x", "reply", store=store)
    key = thread_key("operator", "agent-x")
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert — only the reader's inbound messages flip.
    assert flipped == 2
    msgs = get_thread("operator", "agent-x", store=store)
    assert [m["read"] for m in msgs] == [True, True, False]
    # Idempotent.
    assert mark_read(key, "operator", store=store) == 0


def test_mark_read_scoped_to_ids(store):
    # Arrange
    first = append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    key = first["thread"]
    # Act
    flipped = mark_read(key, "operator", ids=[first["id"]], store=store)
    # Assert
    assert flipped == 1
    msgs = get_thread("operator", "agent-x", store=store)
    assert [m["read"] for m in msgs] == [True, False]


def test_list_threads_summarizes_unread_and_last(store):
    # Arrange
    append_message("agent-x", "operator", "ping", store=store)
    last = append_message("agent-x", "operator", "ping again", store=store)
    # Act
    summary = list_threads(store=store)
    # Assert
    row = summary["dm:agent-x::operator"]
    assert row["peers"] == ("agent-x", "operator")
    assert row["count"] == 2
    assert row["unread"] == {"agent-x": 0, "operator": 2}
    assert row["last"]["id"] == last["id"]


# === dm-dispatch → recipient inbox =========================================


def test_append_message_enqueues_dm_into_recipient_inbox(store):
    # Arrange / Act
    sent = append_message("operator", "agent-x", "please check PR", store=store)
    # Assert — the recipient's pull-inbox in the REAL tasks store carries the
    # dm record (raw-name key: agent-x is not in the users registry).
    inbox = poll_inbox("agent-x", store=store)
    assert len(inbox) == 1
    rec = inbox[0]
    assert rec["event_type"] == "dm"
    assert rec["card_id"] == sent["thread"]
    assert rec["body"] == "please check PR"
    assert rec["actor"] == "operator"
    assert rec["ts"] == sent["ts"]


def test_agent_reply_enqueues_into_operator_inbox_for_symmetry(store):
    # Arrange / Act — documented decision: the operator inbox is enqueued
    # too (cheap + keeps a future operator-side drain surface working).
    append_message("agent-x", "operator", "done, PR is green", store=store)
    # Assert
    inbox = poll_inbox("operator", store=store)
    assert len(inbox) == 1
    assert inbox[0]["event_type"] == "dm"
    assert inbox[0]["actor"] == "agent-x"


def test_dispatch_resolves_registered_recipient_to_user_id(store):
    # Arrange — register the recipient so dispatch must key by u_* id
    # (exactly how poll_notifications resolves the drain key).
    from scitex_cards._users import register_user

    user = register_user(kind="agent", names=["agent-reg"], store=store)
    # Act
    append_message("operator", "agent-reg", "hello registered", store=store)
    # Assert — enqueued under the stable id, not the raw name.
    assert len(poll_inbox(user.id, store=store)) == 1
    assert poll_inbox("agent-reg", store=store) == []


# EOF
