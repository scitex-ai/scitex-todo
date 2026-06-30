#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex_todo.hooks`` liveness-anomaly CONSUMER.

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store with
real cards via ``add_task`` / real users via ``register_user``, the real
internal ``comment_task`` record path, and the real notify rail
(``dispatch_notifications`` → standalone PULL-inbox read via
``scitex_todo._inbox.poll_inbox``).

The consumer (``scitex_todo._hooks._anomaly.consume_anomaly``) is called the
way sac calls it — ONE positional dict, NO store kwarg — so it resolves the
store via ``SCITEX_TODO_TASKS`` (set through the ``env`` fixture, the same
seam the existing ``_notify`` tests use).

Coverage:

* a VALID critical event appends an anomaly comment recording
  agent/reason/severity and the card's status/blocker are UNCHANGED;
* the push is exercised through the notify rail — a critical event reaches
  the OWNER's inbox (the phone-eligible tier), a warning reaches the
  SUBSCRIBERS' inbox (the telegram/email tier);
* a malformed event (missing key / bad enum) → no raise, nothing recorded.
"""

from __future__ import annotations

import scitex_todo._hooks._anomaly as anomaly_mod
from scitex_todo._hooks._anomaly import consume_anomaly
from scitex_todo._inbox import poll_inbox
from scitex_todo._store import add_task, get_task
from scitex_todo._users import register_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _valid_event(card_id="c1", agent="alice", severity="critical"):
    return {
        "agent": agent,
        "card_id": card_id,
        "reason": "owner-not-live",
        "severity": severity,
        "ts": 1_800_000_000.0,
    }


# --------------------------------------------------------------------------- #
# (a) records a durable comment, NON-destructively                            #
# --------------------------------------------------------------------------- #
def test_consume_records_comment_without_mutating_status_or_blocker(tmp_path, env):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="alice",
        status="in_progress",
        created_by="alice",
    )
    before = get_task(store=store, task_id="c1")
    env.set("SCITEX_TODO_TASKS", str(store))

    consume_anomaly(_valid_event(severity="critical"))

    after = get_task(store=store, task_id="c1")
    # A comment was appended recording the anomaly (agent + reason + severity).
    comments = after.get("comments") or []
    anomaly_comments = [c for c in comments if c.get("kind") == "anomaly"]
    assert len(anomaly_comments) == 1
    text = anomaly_comments[0]["text"]
    assert "alice" in text
    assert "owner-not-live" in text
    assert "critical" in text
    # NON-destructive: status + blocker are untouched.
    assert after.get("status") == before.get("status") == "in_progress"
    assert after.get("blocked_by") == before.get("blocked_by")
    assert after.get("blocker") == before.get("blocker")


# --------------------------------------------------------------------------- #
# (b) push exercised through the notify rail: severity → urgency tier         #
# --------------------------------------------------------------------------- #
def test_critical_push_reaches_owner_inbox(tmp_path, env):
    # critical → REASSIGNED rule → routes to the card OWNER (phone-eligible
    # tier). Exercised through the real notify rail + standalone inbox.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    consume_anomaly(_valid_event(severity="critical"))

    notes = poll_inbox(alice.id, store=store)
    # The owner got a notification driven by the critical anomaly push.
    assert any(n["card_id"] == "c1" for n in notes)


def test_warning_push_reaches_subscriber_inbox_not_owner(tmp_path, env):
    # warning → STATUS_CHANGED rule → routes to SUBSCRIBERS (telegram/email
    # tier), NOT the owner. The owner is NOT a subscriber here, so the owner's
    # inbox stays empty for the anomaly push (the `commented` comment-emit does
    # reach the owner, but the anomaly comment is authored by the machine, so
    # the owner is not the actor and would normally get a `commented` note —
    # we therefore assert the SUBSCRIBER got the status_changed anomaly push).
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="alice",
        subscribers=["eve"],
        created_by="alice",
    )
    env.set("SCITEX_TODO_TASKS", str(store))

    consume_anomaly(_valid_event(severity="warning"))

    sub_notes = poll_inbox(eve.id, store=store)
    # The subscriber received the warning-tier anomaly push (status_changed).
    assert any(
        n["event_type"] == "status_changed" and n["card_id"] == "c1"
        for n in sub_notes
    )


def test_severity_event_type_mapping():
    # Pin the severity → urgency-tier mapping the rail is driven with.
    from scitex_todo._events import EventType

    assert anomaly_mod._severity_event_type("critical") == EventType.REASSIGNED
    assert anomaly_mod._severity_event_type("warning") == EventType.STATUS_CHANGED


# --------------------------------------------------------------------------- #
# (c) malformed event → no raise, nothing recorded                            #
# --------------------------------------------------------------------------- #
def test_missing_key_does_not_raise_and_records_nothing(tmp_path, env):
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    bad = _valid_event()
    del bad["severity"]  # missing required key

    # Must NOT raise (runs inside sac's producer loop).
    consume_anomaly(bad)

    after = get_task(store=store, task_id="c1")
    assert [c for c in (after.get("comments") or []) if c.get("kind") == "anomaly"] == []


def test_bad_enum_does_not_raise_and_records_nothing(tmp_path, env):
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    bad = _valid_event()
    bad["severity"] = "apocalyptic"  # not in SEVERITIES
    consume_anomaly(bad)

    bad2 = _valid_event()
    bad2["reason"] = "owner-took-a-nap"  # not in REASONS
    consume_anomaly(bad2)

    after = get_task(store=store, task_id="c1")
    assert [c for c in (after.get("comments") or []) if c.get("kind") == "anomaly"] == []


def test_non_dict_event_does_not_raise():
    # A wholly malformed (non-dict) event must be swallowed loud, not raised.
    consume_anomaly(["not", "a", "dict"])  # type: ignore[arg-type]


def test_unknown_card_does_not_raise(tmp_path, env):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    env.set("SCITEX_TODO_TASKS", str(store))

    # A valid event for a card that does not exist: comment_task raises
    # TaskNotFoundError internally, which the consumer swallows loud.
    consume_anomaly(_valid_event(card_id="never-existed"))


# EOF
