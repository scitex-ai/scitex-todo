#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``should_deliver_now`` channel-push policy gate.

Real config round-trips (STX-NM / PA-306: no mocks) — a real ``config.yaml``
written to a ``tmp_path`` user-scope dir, resolved through the actual
``_config`` layering, no monkeypatch of the resolver itself.
"""

from __future__ import annotations

from scitex_todo._config import DEFAULT_QUIET_EVENT_TYPES, resolve_quiet_event_types
from scitex_todo._delivery._recipients import should_deliver_now


def test_default_quiet_event_types_is_just_reminder():
    assert DEFAULT_QUIET_EVENT_TYPES == frozenset({"reminder"})


def test_reminder_digest_does_not_push():
    note = {"event_type": "reminder", "card_id": "(digest)", "body": "..."}
    assert should_deliver_now("u_alice", note) is False


def test_escalations_still_push():
    for event_type in ("escalation", "creator_escalation"):
        note = {"event_type": event_type, "card_id": "c1", "body": "..."}
        assert should_deliver_now("u_alice", note) is True


def test_ordinary_card_events_still_push():
    # These were never part of the flooding complaint — must not regress.
    for event_type in ("reassigned", "commented", "completed", "created"):
        note = {"event_type": event_type, "card_id": "c1", "body": "..."}
        assert should_deliver_now("u_alice", note) is True


def test_missing_or_unknown_event_type_fails_open():
    assert should_deliver_now("u_alice", {"card_id": "c1", "body": "x"}) is True
    assert should_deliver_now("u_alice", {"event_type": "some_future_type"}) is True


def test_resolve_quiet_event_types_config_override(monkeypatch, tmp_path):
    """A ``delivery.quiet_event_types`` config override wins over the default."""
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))
    todo_dir = tmp_path / "todo"
    todo_dir.mkdir(parents=True, exist_ok=True)
    (todo_dir / "config.yaml").write_text(
        "delivery:\n  quiet_event_types:\n    - reminder\n    - commented\n",
        encoding="utf-8",
    )
    assert resolve_quiet_event_types() == frozenset({"reminder", "commented"})


def test_resolve_quiet_event_types_empty_override_falls_back_to_default():
    """An empty/malformed override must not silently allow everything through."""
    assert resolve_quiet_event_types({"quiet_event_types": []}) == DEFAULT_QUIET_EVENT_TYPES
    assert resolve_quiet_event_types({"quiet_event_types": "not-a-list"}) == (
        DEFAULT_QUIET_EVENT_TYPES
    )
    assert resolve_quiet_event_types({}) == DEFAULT_QUIET_EVENT_TYPES

# EOF
