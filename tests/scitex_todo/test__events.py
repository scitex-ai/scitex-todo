#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical card-event model + emit seam (foundation C1).

C1 of the card-event / notification foundation epic: a TYPED canonical
:class:`Event` + a single :func:`emit` wrapping the existing hook bus.
Pure model + thin emit, NO delivery here.

No mocks (STX-NM / PA-306) — the bus is exercised via the real
``entry_points=`` injection seam (a concrete fake handler), exactly like
the ``_hooks`` / ``card-message`` tests. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_todo._events import (
    CARD_EVENT_KIND,
    EVENT_TYPES,
    Event,
    EventType,
    EventValidationError,
    emit,
)


# === Construction + defaults ===============================================


def test_event_type_constants_match_event_types_set():
    # Arrange / Act
    declared = {
        EventType.CREATED,
        EventType.REASSIGNED,
        EventType.REASSIGNED_BATCH,
        EventType.STATUS_CHANGED,
        EventType.COMMENTED,
        EventType.COMPLETED,
        EventType.COMMITTED,
        EventType.PUSHED,
        EventType.MERGED,
        EventType.RELEASED,
        EventType.PULLED,
        EventType.DEPLOYED,
    }
    # Assert — the closed set is exactly the twelve declared constants.
    assert declared == set(EVENT_TYPES)
    assert len(EVENT_TYPES) == 12


def test_event_minimal_construction_defaults():
    # Arrange / Act
    ev = Event(type=EventType.CREATED)
    # Assert — required type set; nullable fields default None; extra {}.
    assert ev.type == "created"
    assert ev.card_id is None
    assert ev.actor is None
    assert ev.repo is None
    assert ev.extra == {}


def test_event_explicit_ts_is_preserved():
    # Arrange / Act
    ev = Event(type=EventType.CREATED, ts="2026-01-01T00:00:00Z")
    # Assert
    assert ev.ts == "2026-01-01T00:00:00Z"


# === ts auto-stamp is valid ISO ============================================


def test_event_ts_autostamped_when_absent():
    # Arrange / Act
    ev = Event(type=EventType.COMPLETED, card_id="c-1")
    # Assert — non-empty string that parses as an ISO-8601 datetime.
    assert isinstance(ev.ts, str) and ev.ts
    parsed = _dt.datetime.fromisoformat(ev.ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None  # UTC-aware


# === Bad type raises EventValidationError ==================================


def test_event_bad_type_raises_validation_error():
    # Arrange / Act / Assert
    with pytest.raises(EventValidationError) as exc:
        Event(type="not-a-real-type")
    # The bad value is echoed (fail-loud).
    assert "not-a-real-type" in str(exc.value)


def test_event_validation_error_is_value_error():
    # Arrange / Act / Assert — subclass contract for broad except sites.
    with pytest.raises(ValueError):
        Event(type="bogus")


# === to_dict() shape =======================================================


def test_to_dict_has_kind_discriminator_and_type():
    # Arrange
    ev = Event(type=EventType.PUSHED, repo="o/r", branch="develop", sha="abc")
    # Act
    d = ev.to_dict()
    # Assert — stable discriminator + canonical type + carried fields.
    assert d["kind"] == CARD_EVENT_KIND == "card-event"
    assert d["type"] == "pushed"
    assert d["repo"] == "o/r"
    assert d["branch"] == "develop"
    assert d["sha"] == "abc"
    assert d["ts"]


def test_to_dict_omits_none_fields():
    # Arrange — a card-level event with no git fields.
    ev = Event(type=EventType.CREATED, card_id="c-1")
    # Act
    d = ev.to_dict()
    # Assert — None-valued fields are absent from the envelope.
    assert "repo" not in d
    assert "pr_url" not in d
    assert d["card_id"] == "c-1"


def test_to_dict_merges_extra_without_clobbering_known_keys():
    # Arrange
    ev = Event(
        type=EventType.DEPLOYED,
        repo="o/r",
        extra={"service": "web", "type": "should-not-win"},
    )
    # Act
    d = ev.to_dict()
    # Assert — extra appears, but cannot overwrite the canonical `type`.
    assert d["service"] == "web"
    assert d["type"] == "deployed"


# === Ergonomic constructors ================================================


def test_card_created_constructor():
    ev = Event.card_created("c-1", actor="op")
    assert ev.type == "created" and ev.card_id == "c-1" and ev.actor == "op"


def test_reassigned_constructor():
    ev = Event.reassigned("c-1", actor="op")
    assert ev.type == "reassigned" and ev.card_id == "c-1"


def test_status_changed_constructor():
    ev = Event.status_changed("c-1", actor="op")
    assert ev.type == "status_changed"


def test_commented_constructor():
    ev = Event.commented("c-1", actor="op")
    assert ev.type == "commented"


def test_completed_constructor():
    ev = Event.completed("c-1", actor="op")
    assert ev.type == "completed"


def test_committed_constructor_repo_level():
    ev = Event.committed(repo="o/r", sha="deadbeef")
    assert ev.type == "committed" and ev.card_id is None and ev.sha == "deadbeef"


def test_pushed_constructor():
    ev = Event.pushed(repo="o/r", branch="main", sha="abc")
    assert ev.type == "pushed" and ev.branch == "main"


def test_merged_constructor():
    ev = Event.merged(repo="o/r", pr_url="https://x/pull/1")
    assert ev.type == "merged" and ev.pr_url == "https://x/pull/1"


def test_released_constructor():
    ev = Event.released(repo="o/r", version="1.2.3")
    assert ev.type == "released" and ev.version == "1.2.3"


def test_pulled_constructor():
    ev = Event.pulled(repo="o/r")
    assert ev.type == "pulled" and ev.repo == "o/r"


def test_deployed_constructor_tucks_service_into_extra():
    ev = Event.deployed(repo="o/r", service="api")
    assert ev.type == "deployed"
    assert ev.to_dict()["service"] == "api"


# === emit() wraps dispatch_event with the right envelope ===================


class _Capturing:
    """Concrete fake entry-point handler that records every event."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))  # shallow copy, see card-message test


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def test_emit_dispatches_card_event_envelope():
    # Arrange — a real in-process handler via the injection seam.
    sink = _Capturing()
    eps = [_FakeEP("captor", sink)]
    ev = Event.pushed(repo="o/r", branch="develop", sha="abc123")
    # Act
    emit(ev, entry_points=eps)
    # Assert — exactly one event, carrying the canonical envelope.
    assert len(sink.events) == 1
    got = sink.events[0]
    assert got["kind"] == "card-event"
    assert got["type"] == "pushed"
    assert got["repo"] == "o/r"
    assert got["sha"] == "abc123"


def test_emit_does_not_use_legacy_card_message_kind():
    # Arrange — C1 must NOT collide with the existing card-message kind.
    sink = _Capturing()
    eps = [_FakeEP("captor", sink)]
    # Act
    emit(Event.commented("c-1", actor="op"), entry_points=eps)
    # Assert
    assert sink.events[0]["kind"] != "card-message"
    assert sink.events[0]["kind"] == "card-event"


def test_emit_never_raises_even_when_handler_explodes():
    # Arrange — a real fake handler that raises, injected via the seam.
    def _bad(_event):
        raise RuntimeError("bus exploded")

    eps = [_FakeEP("bad", _bad)]
    # Act / Assert — emit swallows; producer is never broken.
    emit(Event.completed("c-1"), entry_points=eps)  # must NOT raise
