#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``/dm/*`` Django endpoints (operator↔agent direct messages).

Minimal-slice contract (card fleet-agent-direct-message-board-pane-20260707):

  - GET  /dm/threads      → registry agents ∪ thread peers, with unread + last.
  - GET  /dm/thread/<p>   → chronological messages; mark_read=1 acks.
  - POST /dm/thread/<p>   → appends from=operator, dm-dispatches to the
                            agent's pull-inbox; 400 on empty body.
  - 405 on other verbs.

Django RequestFactory against a real tmp store via ``?store=``; no mocks
(STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.dm import dm_thread_view, dm_threads_view
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import append_message, get_thread


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml (threads sidecar lands next to it)."""
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


def _get(url):
    return RequestFactory().get(url)


# === /dm/threads ===========================================================


def test_threads_view_lists_thread_peers_with_unread(store):
    # Arrange — one inbound thread, no registry.
    append_message("agent-x", "operator", "ping", store=store)
    # Act
    response = dm_threads_view(_get(f"/dm/threads?store={store}"))
    # Assert
    assert response.status_code == 200
    agents = json.loads(response.content)["agents"]
    assert [a["name"] for a in agents] == ["agent-x"]
    assert agents[0]["unread"] == 1
    assert agents[0]["last_body"] == "ping"


def test_threads_view_includes_registry_agents_without_threads(store):
    # Arrange
    from scitex_cards._users import register_user

    register_user(kind="agent", names=["agent-quiet"], store=store)
    # Act
    response = dm_threads_view(_get(f"/dm/threads?store={store}"))
    # Assert — registered but silent agents still appear (composable-to).
    agents = json.loads(response.content)["agents"]
    assert [a["name"] for a in agents] == ["agent-quiet"]
    assert agents[0]["unread"] == 0
    assert agents[0]["last_ts"] is None


def test_threads_view_rejects_post(store):
    # Arrange / Act
    response = dm_threads_view(RequestFactory().post(f"/dm/threads?store={store}"))
    # Assert
    assert response.status_code == 405


# === GET /dm/thread/<peer> =================================================


def test_thread_view_returns_messages_chronologically(store):
    # Arrange
    append_message("agent-x", "operator", "first", store=store)
    append_message("operator", "agent-x", "second", store=store)
    # Act
    response = dm_thread_view(
        _get(f"/dm/thread/agent-x?store={store}"), "agent-x"
    )
    # Assert
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["thread"] == "dm:agent-x::operator"
    assert [m["body"] for m in data["messages"]] == ["first", "second"]


def test_thread_view_mark_read_acks_operator_messages(store):
    # Arrange
    append_message("agent-x", "operator", "unread ping", store=store)
    # Act
    dm_thread_view(
        _get(f"/dm/thread/agent-x?store={store}&mark_read=1"), "agent-x"
    )
    # Assert — persisted in the sidecar, not just the response.
    msgs = get_thread("operator", "agent-x", store=store)
    assert msgs[0]["read"] is True


# === POST /dm/thread/<peer> ================================================


def test_post_appends_operator_message_and_dispatches_to_inbox(store):
    # Arrange
    request = RequestFactory().post(
        f"/dm/thread/agent-x?store={store}",
        data=json.dumps({"body": "check the deploy"}),
        content_type="application/json",
    )
    # Act
    response = dm_thread_view(request, "agent-x")
    # Assert — stored with from=operator...
    assert response.status_code == 200
    message = json.loads(response.content)["message"]
    assert message["from"] == "operator"
    assert message["to"] == "agent-x"
    assert get_thread("operator", "agent-x", store=store) == [message]
    # ...and dm-dispatched into the agent's pull-inbox.
    inbox = poll_inbox("agent-x", store=store)
    assert len(inbox) == 1
    assert inbox[0]["event_type"] == "dm"
    assert inbox[0]["body"] == "check the deploy"


def test_post_rejects_empty_body(store):
    # Arrange
    request = RequestFactory().post(
        f"/dm/thread/agent-x?store={store}",
        data=json.dumps({"body": "   "}),
        content_type="application/json",
    )
    # Act
    response = dm_thread_view(request, "agent-x")
    # Assert
    assert response.status_code == 400


def test_thread_view_rejects_delete(store):
    # Arrange / Act
    response = dm_thread_view(
        RequestFactory().delete(f"/dm/thread/agent-x?store={store}"), "agent-x"
    )
    # Assert
    assert response.status_code == 405


# EOF
