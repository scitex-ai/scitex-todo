#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag engine — per-owner digest cadence + per-card operator escalation.

Real fakes, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store for the
sidecar, a plain list-recorder ``enqueue``, a real ``resolve_key`` dict, and
an injected ``now`` so the cadence/escalation state machine is driven
deterministically without a clock or network. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_todo._reminders import (
    DEFAULT_REMINDER_BASE_HOURS,
    DIGEST_CARD_ID,
    EVENT_DIGEST,
    EVENT_ESCALATION,
    load_reminder_state,
    reminder_interval_hours,
    sweep_reminders,
)


# === hermetic env ==========================================================


@pytest.fixture(autouse=True)
def _clear_engine_env(monkeypatch):
    """Strip the engine's env knobs so a deployed container's settings (e.g.
    ``SCITEX_TODO_REMINDER_OWNERS=scitex-todo`` from the agent spec) can never
    leak into these unit tests. Each test sets only what it needs via args."""
    for var in (
        "SCITEX_TODO_REMINDER_OWNERS",
        "SCITEX_TODO_REMINDER_BASE_HOURS",
        "SCITEX_TODO_REMINDER_MAX_HOURS",
        "SCITEX_TODO_REMINDER_ESCALATE_AFTER",
        "SCITEX_TODO_REMINDER_ESCALATE_PRIORITY",
        "SCITEX_TODO_OPERATOR",
        "SCITEX_TODO_STALE_ACTIVE_HOURS",
        "SCITEX_TODO_PENDING_NUDGE_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)


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


def _digests(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_DIGEST]


def _escalations(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION]


# === reminder_interval_hours — escalating backoff ==========================


def test_interval_zero_count_is_immediate():
    assert reminder_interval_hours(0, base=2.0, cap=24.0) == 0.0


def test_interval_escalates_then_caps():
    assert reminder_interval_hours(1, base=2.0, cap=24.0) == 2.0
    assert reminder_interval_hours(2, base=2.0, cap=24.0) == 4.0
    assert reminder_interval_hours(3, base=2.0, cap=24.0) == 8.0
    # caps
    assert reminder_interval_hours(10, base=2.0, cap=24.0) == 24.0


# === sweep_reminders — one digest per owner ================================


def test_sweep_enqueues_one_digest_for_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"alice": "u_alice"}),
    )

    assert out["digested"] == ["alice"]
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["recipient"] == "u_alice"  # producer-matching key
    assert call["event_type"] == EVENT_DIGEST
    assert call["card_id"] == DIGEST_CARD_ID
    assert call["actor"] == "notifyd"
    assert "c1" in call["body"]


def test_digest_collapses_many_cards_into_one_note(tmp_path):
    """The whole point of the refactor: N stale cards → ONE digest, not N nags."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="alice", hours_ago=20.0),
        _t(id="c3", owner="alice", status="pending", hours_ago=99.0),
    ]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}),
    )

    assert out["digested"] == ["alice"]
    assert len(rec.calls) == 1  # ONE note, not three
    body = rec.calls[0]["body"]
    assert "c1" in body and "c2" in body and "c3" in body


def test_sweep_does_not_renag_before_interval(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    # Same instant → count=1, interval=base(2h), 0h elapsed → NOT due.
    out2 = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))

    assert out2["digested"] == []
    assert out2["skipped"] == ["alice"]
    assert len(rec.calls) == 1  # still just the first digest


def test_sweep_renags_after_interval_elapses(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    later = _NOW + _dt.timedelta(hours=DEFAULT_REMINDER_BASE_HOURS + 0.1)
    out2 = sweep_reminders(tasks, store=store, now=later, enqueue=rec, resolve_key=_resolver({}))

    assert out2["digested"] == ["alice"]
    assert len(rec.calls) == 2


# === escalation — high-priority overdue → operator (per card) ==============


def test_high_priority_escalates_to_operator_after_threshold(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"operator": "u_op"}),
        operator="operator", escalate_after=2, escalate_priority=1,
    )

    # 1st sweep: owner digest count→1, below threshold, no escalation.
    sweep_reminders(tasks, now=_NOW, **kw)
    # 2nd sweep after the interval: count→2 == threshold, high-prio → escalate.
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=3), **kw)

    assert out2["escalated"] == ["c1"]
    esc = _escalations(rec)
    assert len(esc) == 1
    assert esc[0]["recipient"] == "u_op"
    assert esc[0]["card_id"] == "c1"  # escalation names the specific stuck card


def test_escalation_fires_once_per_streak(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)

    sweep_reminders(tasks, now=_NOW, **kw)  # count1 >= 1 → escalate
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=30), **kw)  # already escalated

    assert len(_escalations(rec)) == 1


def test_low_priority_card_never_escalates(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0, priority=5)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)

    sweep_reminders(tasks, now=_NOW, **kw)
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=30), **kw)

    assert _escalations(rec) == []


def test_card_without_priority_never_escalates(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0)]  # no priority
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), escalate_after=1)

    sweep_reminders(tasks, now=_NOW, **kw)
    assert _escalations(rec) == []


def test_only_high_priority_card_in_a_mixed_digest_escalates(tmp_path):
    """An owner's digest covers all their cards, but ONLY the high-prio one escalates."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="hot", owner="alice", hours_ago=50.0, priority=0),
        _t(id="cold", owner="alice", hours_ago=50.0, priority=5),
    ]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)

    out = sweep_reminders(tasks, now=_NOW, **kw)

    assert out["digested"] == ["alice"]
    assert out["escalated"] == ["hot"]
    assert [c["card_id"] for c in _escalations(rec)] == ["hot"]


# === nag STOPS when work leaves the stale set (closed / touched) ===========


def test_state_pruned_when_owner_has_no_stale_cards(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    stale = [_t(id="c1", owner="alice", hours_ago=10.0)]
    sweep_reminders(stale, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert "alice" in load_reminder_state(store)["owners"]

    # Card is now done → owner has no stale cards → cadence pruned, nag stops.
    done = [_t(id="c1", owner="alice", status="done", hours_ago=10.0)]
    out = sweep_reminders(done, store=store, now=_NOW + _dt.timedelta(hours=10),
                          enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == []
    assert "alice" not in load_reminder_state(store)["owners"]


def test_escalation_latch_pruned_when_card_no_longer_stale(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    hot = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1)
    sweep_reminders(hot, now=_NOW, **kw)
    assert "c1" in load_reminder_state(store)["cards"]

    done = [_t(id="c1", owner="alice", status="done", hours_ago=50.0, priority=0)]
    sweep_reminders(done, now=_NOW + _dt.timedelta(hours=30), **kw)
    assert "c1" not in load_reminder_state(store)["cards"]


def test_unassigned_cards_are_not_nagged(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # No agent/assignee → owner resolves to "(unassigned)".
    tasks = [{"id": "c1", "title": "x", "status": "in_progress",
              "last_activity": "2026-06-01T00:00:00Z"}]
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == []
    assert rec.calls == []


# === owner allowlist — phased rollout (nag only listed owners) ============


def test_owner_allowlist_arg_nags_only_listed_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}),
        owners={"alice"},
    )
    assert out["digested"] == ["alice"]  # bob left untouched
    assert [c["recipient"] for c in rec.calls] == ["alice"]


def test_owner_allowlist_env_scopes_the_sweep(tmp_path, monkeypatch):
    from scitex_todo._reminders import ENV_REMINDER_OWNERS

    monkeypatch.setenv(ENV_REMINDER_OWNERS, "alice, carol")
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == ["alice"]


def test_empty_allowlist_nags_all_owners(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}), owners=set())
    assert sorted(out["digested"]) == ["alice", "bob"]


# === legacy sidecar tolerance (per-card schema from the prior engine) ======


def test_legacy_cards_only_sidecar_loads_and_digests(tmp_path):
    store = tmp_path / "tasks.yaml"
    sidecar = tmp_path / "reminders.yaml"
    # Old engine wrote a bare ``cards:`` mapping with per-card cadence fields.
    sidecar.write_text(
        "cards:\n  c1:\n    count: 2\n    last_at: 2026-06-01T00:00:00Z\n"
        "    escalated: true\n",
        encoding="utf-8",
    )
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]

    # Owners section starts empty → owner is due immediately, digest fires.
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == ["alice"]


# EOF
