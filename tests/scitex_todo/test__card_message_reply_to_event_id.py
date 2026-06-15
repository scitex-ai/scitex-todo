#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`reply_to_event_id` loop-prevention hint on the card-message wire.

Lead a2a `8f7687ae`, 2026-06-15. Adds an optional pass-through field
to the ``card-message`` event so SAC's plugin can suppress echoing a
reply back to its own author. todo CARRIES the field; enforcement
lives in the consumer plugin. Convention:
``event_id = "{card_id}#{created_at}"``.

Coverage:
  - validator accepts optional reply_to_event_id (None / non-empty str)
  - validator rejects empty string + non-string types (400)
  - comment_task kwarg threads into emitted event
  - comment_task rejects empty / non-string kwarg
  - /chat/<id> POST accepts reply_to_event_id JSON field
  - /chat/<id> POST 400 on empty reply_to_event_id JSON field
  - backwards compat: absent => emit dict contains reply_to_event_id=None
                      (existing 17 tests in test__card_message_event.py
                       stay green; this file pins the new field)

No mocks (STX-NM/PA-306). AAA pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_todo import _hooks as hooks_module
from scitex_todo._hooks import HookEventError, event_validate
from scitex_todo._store import add_task, comment_task


# === fixtures ==============================================================


@pytest.fixture()
def captured_events(monkeypatch) -> list[dict]:
    """Capture every event dispatch_event(...) is called with."""
    captured: list[dict] = []
    real = hooks_module.dispatch_event

    def spy(event, **kwargs):
        captured.append(dict(event))
        return real(event_validate(event), **kwargs)

    monkeypatch.setattr(hooks_module, "dispatch_event", spy)
    return captured


@pytest.fixture()
def store(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "tasks.yaml"
    add_task(store=p, id="card-1", title="x")
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(p))
    return p


# === validator =============================================================


def test_validator_accepts_optional_reply_to_event_id_string():
    # Arrange
    payload = {
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
        "reply_to_event_id": "c-1#2026-06-15T01:23:45Z",
    }
    # Act
    out = event_validate(payload)
    # Assert
    assert out["reply_to_event_id"] == "c-1#2026-06-15T01:23:45Z"


def test_validator_treats_missing_reply_to_event_id_as_none():
    out = event_validate({"kind": "card-message", "card_id": "c-1", "body": "hi"})
    assert out["reply_to_event_id"] is None


def test_validator_treats_explicit_null_reply_to_event_id_as_none():
    out = event_validate({
        "kind": "card-message",
        "card_id": "c-1",
        "body": "hi",
        "reply_to_event_id": None,
    })
    assert out["reply_to_event_id"] is None


def test_validator_rejects_empty_reply_to_event_id():
    with pytest.raises(HookEventError):
        event_validate({
            "kind": "card-message",
            "card_id": "c-1",
            "body": "hi",
            "reply_to_event_id": "",
        })


def test_validator_rejects_non_string_reply_to_event_id():
    with pytest.raises(HookEventError):
        event_validate({
            "kind": "card-message",
            "card_id": "c-1",
            "body": "hi",
            "reply_to_event_id": 42,
        })


# === comment_task ==========================================================


def test_comment_task_threads_reply_to_event_id_into_event(store, captured_events):
    comment_task(
        store=store,
        task_id="card-1",
        text="reply body",
        by="agent-a",
        reply_to_event_id="card-1#2026-06-15T01:23:45Z",
    )
    assert any(
        e.get("kind") == "card-message"
        and e.get("reply_to_event_id") == "card-1#2026-06-15T01:23:45Z"
        for e in captured_events
    )


def test_comment_task_absent_reply_to_event_id_emits_none(store, captured_events):
    comment_task(
        store=store, task_id="card-1", text="plain comment", by="agent-a",
    )
    cm = [e for e in captured_events if e.get("kind") == "card-message"]
    assert cm, "card-message event must be emitted"
    assert cm[-1].get("reply_to_event_id") is None


def test_comment_task_rejects_empty_reply_to_event_id(store):
    with pytest.raises(ValueError):
        comment_task(
            store=store,
            task_id="card-1",
            text="x",
            by="a",
            reply_to_event_id="",
        )


def test_comment_task_rejects_non_string_reply_to_event_id(store):
    with pytest.raises(ValueError):
        comment_task(
            store=store,
            task_id="card-1",
            text="x",
            by="a",
            reply_to_event_id=42,
        )


# === /chat/<id> POST =======================================================


def _post_json(rf: RequestFactory, path: str, payload: dict):
    return rf.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_chat_post_threads_reply_to_event_id(store, captured_events):
    from scitex_todo._django.handlers.chat import chat_view

    rf = RequestFactory()
    req = _post_json(
        rf,
        "/chat/card-1",
        {
            "text": "reply body",
            "author": "agent-a",
            "reply_to_event_id": "card-1#2026-06-15T01:23:45Z",
        },
    )
    response = chat_view(req, "card-1")
    assert response.status_code == 200

    cm = [e for e in captured_events if e.get("kind") == "card-message"]
    assert cm
    assert cm[-1].get("reply_to_event_id") == "card-1#2026-06-15T01:23:45Z"


def test_chat_post_400_on_empty_reply_to_event_id(store):
    from scitex_todo._django.handlers.chat import chat_view

    rf = RequestFactory()
    req = _post_json(
        rf,
        "/chat/card-1",
        {"text": "x", "author": "a", "reply_to_event_id": ""},
    )
    response = chat_view(req, "card-1")
    assert response.status_code == 400
    assert b"reply_to_event_id" in response.content


def test_chat_post_400_on_non_string_reply_to_event_id(store):
    from scitex_todo._django.handlers.chat import chat_view

    rf = RequestFactory()
    req = _post_json(
        rf,
        "/chat/card-1",
        {"text": "x", "author": "a", "reply_to_event_id": 42},
    )
    response = chat_view(req, "card-1")
    assert response.status_code == 400


def test_chat_post_absent_field_works_as_before(store, captured_events):
    """Backwards compat — operator can still POST plain {text, author}."""
    from scitex_todo._django.handlers.chat import chat_view

    rf = RequestFactory()
    req = _post_json(rf, "/chat/card-1", {"text": "hi", "author": "op"})
    response = chat_view(req, "card-1")
    assert response.status_code == 200

    cm = [e for e in captured_events if e.get("kind") == "card-message"]
    assert cm
    assert cm[-1].get("reply_to_event_id") is None
