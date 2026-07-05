#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the listen-server HTTP door (STX-NM: real app, real inbox).

Uses Starlette's ``TestClient`` against a REAL :func:`create_app` (no mocks)
with the embedded delivery loop disabled (``run_delivery_loop=False``), and a
REAL ``tmp_path`` store so ``/v1/notify`` actually round-trips through
:func:`scitex_todo._inbox.enqueue` / ``poll_inbox``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from scitex_todo._inbox import poll_inbox
from scitex_todo._listen import create_app

_TOKEN = "test-secret-token"


def _client(tmp_path):
    store = tmp_path / "tasks.yaml"
    app = create_app(token=_TOKEN, store=store, run_delivery_loop=False)
    return TestClient(app), store


# --------------------------------------------------------------------------- #
# /v1/health — public, no auth                                                #
# --------------------------------------------------------------------------- #
def test_health_is_public_and_returns_banner(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.get("/v1/health")  # NO Authorization header
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "scitex-todo-listen"
    assert body["v"] == 1


# --------------------------------------------------------------------------- #
# /v1/notify — bearer-gated                                                   #
# --------------------------------------------------------------------------- #
def test_notify_without_token_is_401(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.post("/v1/notify", json={"agent": "u_x", "body": "hi"})
    assert resp.status_code == 401
    assert "missing bearer token" in resp.json()["error"]


def test_notify_with_wrong_token_is_403(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            json={"agent": "u_x", "body": "hi"},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 403
    assert "invalid bearer token" in resp.json()["error"]


def test_notify_with_valid_token_enqueues_into_inbox(tmp_path):
    client, store = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            json={
                "agent": "u_alice",
                "body": "Card c1 needs you",
                "card_id": "c1",
                "from_agent": "bob",
                "event_type": "reassigned",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 200
    out = resp.json()
    assert out["agent"] == "u_alice"
    assert out["enqueued"] is True
    assert out["msg_id"] and out["msg_id"].startswith("n_")

    # The push actually landed in alice's pull-inbox.
    notes = poll_inbox("u_alice", unseen_only=True, mark_seen=False, store=store)
    assert len(notes) == 1
    assert notes[0]["id"] == out["msg_id"]
    assert notes[0]["body"] == "Card c1 needs you"
    assert notes[0]["card_id"] == "c1"
    assert notes[0]["actor"] == "bob"
    assert notes[0]["event_type"] == "reassigned"


def test_notify_missing_recipient_is_400(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            json={"body": "orphan"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 400
    assert "agent" in resp.json()["error"]


def test_notify_missing_body_is_400(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            json={"agent": "u_x"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 400
    assert "body" in resp.json()["error"]


def test_notify_malformed_json_is_400(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            content=b"not-json",
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 400


def test_notify_defaults_event_type_and_card_id(tmp_path):
    """A bare {agent, body} push still enqueues with sensible defaults."""
    client, store = _client(tmp_path)
    with client:
        resp = client.post(
            "/v1/notify",
            json={"agent": "u_dave", "body": "ping"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 200
    notes = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    assert len(notes) == 1
    assert notes[0]["event_type"] == "notify"
    assert notes[0]["card_id"] == "(direct)"
    assert notes[0]["actor"] is None


def test_no_configured_token_fails_closed_503(tmp_path):
    """An empty configured token rejects gated routes (never allow-all)."""
    store = tmp_path / "tasks.yaml"
    app = create_app(token="", store=store, run_delivery_loop=False)
    client = TestClient(app)
    with client:
        # health still public
        assert client.get("/v1/health").status_code == 200
        resp = client.post(
            "/v1/notify",
            json={"agent": "u_x", "body": "hi"},
            headers={"Authorization": "Bearer anything"},
        )
    assert resp.status_code == 503


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))

# EOF
