#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag-until-closed reminder engine — escalating cadence + operator escalation.

Real fakes, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store for the
sidecar, a plain list-recorder ``enqueue``, a real ``resolve_key`` dict, and
an injected ``now`` so the cadence/escalation state machine is driven
deterministically without a clock or network. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo._reminders import (
    DEFAULT_REMINDER_BASE_HOURS,
    EVENT_ESCALATION,
    EVENT_REMINDER,
    load_reminder_state,
    reminder_interval_hours,
    sweep_reminders,
)


# === helpers ===============================================================


class _EnqueueRecorder:
    """A real ``enqueue`` callable — records each call, returns a record."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, recipient_key, *, event_type, card_id, body, actor, ts, store):
        rec = {
            "recipient": recipient_key,
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
            "actor": actor,
            "ts": ts,
        }
        self.calls.append(rec)
        return rec


def _resolver(mapping):
    return lambda name: mapping.get(name, name)


def _t(*, id, owner, status="in_progress", hours_ago=10.0, priority=None, now=None):
    now = now or _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
    last = (now - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = {"id": id, "title": f"card {id}", "status": status, "agent": owner,
            "last_activity": last}
    if priority is not None:
        card["priority"] = priority
    return card


_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


# === reminder_interval_hours — escalating backoff ==========================


def test_interval_zero_count_is_immediate():
    assert reminder_interval_hours(0, base=2.0, cap=24.0) == 0.0


def test_interval_escalates_then_caps():
    assert reminder_interval_hours(1, base=2.0, cap=24.0) == 2.0
    assert reminder_interval_hours(2, base=2.0, cap=24.0) == 4.0
    assert reminder_interval_hours(3, base=2.0, cap=24.0) == 8.0
    # caps
    assert reminder_interval_hours(10, base=2.0, cap=24.0) == 24.0


# === sweep_reminders — enqueue a due reminder to the owner =================


def test_sweep_enqueues_reminder_for_stale_card(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"alice": "u_alice"}),
    )

    assert out["reminded"] == ["c1"]
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["recipient"] == "u_alice"  # producer-matching key
    assert call["event_type"] == EVENT_REMINDER
    assert call["card_id"] == "c1"
    assert call["actor"] == "notifyd"


def test_sweep_does_not_renag_before_interval(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    # Same instant → count=1, interval=base(2h), 0h elapsed → NOT due.
    out2 = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))

    assert out2["reminded"] == []
    assert out2["skipped"] == ["c1"]
    assert len(rec.calls) == 1  # still just the first reminder


def test_sweep_renags_after_interval_elapses(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    later = _NOW + _dt.timedelta(hours=DEFAULT_REMINDER_BASE_HOURS + 0.1)
    out2 = sweep_reminders(tasks, store=store, now=later, enqueue=rec, resolve_key=_resolver({}))

    assert out2["reminded"] == ["c1"]
    assert len(rec.calls) == 2


# === escalation — high-priority overdue → operator =========================


def test_high_priority_escalates_to_operator_after_threshold(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"operator": "u_op"}),
        operator="operator", escalate_after=2, escalate_priority=1,
    )

    # 1st sweep: count→1, below threshold, no escalation.
    sweep_reminders(tasks, now=_NOW, **kw)
    # 2nd sweep after the interval: count→2 == threshold, high-prio → escalate.
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=3), **kw)

    assert out2["escalated"] == ["c1"]
    esc = [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION]
    assert len(esc) == 1
    assert esc[0]["recipient"] == "u_op"
    assert esc[0]["card_id"] == "c1"


def test_escalation_fires_once_per_streak(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)

    sweep_reminders(tasks, now=_NOW, **kw)  # count1 >= 1 → escalate
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=30), **kw)  # already escalated

    esc = [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION]
    assert len(esc) == 1


def test_low_priority_card_never_escalates(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0, priority=5)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)

    sweep_reminders(tasks, now=_NOW, **kw)
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=30), **kw)

    assert [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION] == []


def test_card_without_priority_never_escalates(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0)]  # no priority
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), escalate_after=1)

    sweep_reminders(tasks, now=_NOW, **kw)
    assert [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION] == []


# === nag STOPS when the card leaves the stale set (closed / touched) =======


def test_state_pruned_when_card_no_longer_stale(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    stale = [_t(id="c1", owner="alice", hours_ago=10.0)]
    sweep_reminders(stale, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert "c1" in load_reminder_state(store)

    # Card is now done → not in the stale set → its state is pruned, nag stops.
    done = [_t(id="c1", owner="alice", status="done", hours_ago=10.0)]
    out = sweep_reminders(done, store=store, now=_NOW + _dt.timedelta(hours=10),
                          enqueue=rec, resolve_key=_resolver({}))
    assert out["reminded"] == []
    assert "c1" not in load_reminder_state(store)


def test_unassigned_cards_are_not_nagged(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # No agent/assignee → owner resolves to "(unassigned)".
    tasks = [{"id": "c1", "title": "x", "status": "in_progress",
              "last_activity": "2026-06-01T00:00:00Z"}]
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["reminded"] == []
    assert rec.calls == []


# EOF
