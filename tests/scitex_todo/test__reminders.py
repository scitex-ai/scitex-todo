#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag engine — per-owner digest, flat configurable cadence, per-card override.

Real fakes, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store for the
sidecar, a plain list-recorder ``enqueue``, a real ``resolve_key`` dict, and
an injected ``now`` so the cadence/escalation state machine is driven
deterministically without a clock or network. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_todo._reminders import (
    DIGEST_CARD_ID,
    EVENT_CREATOR_ESCALATION,
    EVENT_DIGEST,
    EVENT_ESCALATION,
    load_reminder_state,
    sweep_reminders,
)


# === hermetic env + config =================================================


@pytest.fixture(autouse=True)
def _isolate_engine(monkeypatch):
    """Strip env knobs AND detach config resolution so a deployed container's
    settings (``SCITEX_TODO_REMINDER_OWNERS`` from the spec, a real
    ``~/.scitex/todo/config.yaml``) can never leak into these unit tests.
    Each test sets only what it needs via args."""
    for var in (
        "SCITEX_TODO_REMINDER_OWNERS",
        "SCITEX_TODO_REMINDER_ESCALATE_AFTER",
        "SCITEX_TODO_REMINDER_ESCALATE_PRIORITY",
        "SCITEX_TODO_OPERATOR",
        "SCITEX_TODO_STALE_ACTIVE_HOURS",
        "SCITEX_TODO_PENDING_NUDGE_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)
    # No config files contribute anything unless a test opts in.
    monkeypatch.setattr("scitex_todo._config.config_paths", lambda: [])


# === helpers ===============================================================


class _EnqueueRecorder:
    """A real ``enqueue`` callable — records each call, returns a record."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(
        self, recipient_key, *, event_type, card_id, body, actor, ts, store,
        supersede=False,
    ):
        rec = {
            "recipient": recipient_key,
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
            "actor": actor,
            "ts": ts,
            "supersede": supersede,
        }
        self.calls.append(rec)
        return rec


def _resolver(mapping):
    return lambda name: mapping.get(name, name)


def _t(*, id, owner, status="in_progress", hours_ago=10.0, priority=None,
       interval_minutes=None, now=None):
    now = now or _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
    last = (now - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = {"id": id, "title": f"card {id}", "status": status, "agent": owner,
            "last_activity": last}
    if priority is not None:
        card["priority"] = priority
    if interval_minutes is not None:
        card["reminder_interval_minutes"] = interval_minutes
    return card


_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _digests(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_DIGEST]


def _escalations(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION]


def _creator_escs(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_CREATOR_ESCALATION]


def _user_resolver(mapping):
    """Fake ``resolve_user``: owner name → a user DICT (or None = not a user).

    A user dict carries a ``last_seen`` stamp so ``is_alive`` can classify it;
    ``None`` models a free-form (non-registered) owner. Mirrors the real
    ``resolve_user`` seam without touching the registry (real fake, no mock).
    """
    return lambda name: mapping.get(name)


def _seen(now, *, seconds_ago):
    ts = now - _dt.timedelta(seconds=seconds_ago)
    return {"last_seen": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}


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
    # The cumulative digest supersedes any unseen predecessor (replay-storm fix).
    assert call["supersede"] is True


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


# === flat cadence (default 5 min) ==========================================


def test_sweep_does_not_renag_before_interval(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)

    sweep_reminders(tasks, now=_NOW, **kw)
    # 4 min later → under the 5 min gap → NOT due.
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=4), **kw)

    assert out2["digested"] == []
    assert out2["skipped"] == ["alice"]
    assert len(rec.calls) == 1


def test_sweep_renags_after_interval_elapses(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)

    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)

    assert out2["digested"] == ["alice"]
    assert len(rec.calls) == 2


def test_default_interval_is_five_minutes(tmp_path):
    """With no config and no arg, the cadence falls back to the 5 min default."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))

    sweep_reminders(tasks, now=_NOW, **kw)
    early = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=4), **kw)
    late = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)

    assert early["digested"] == []   # 4 min < 5 min default
    assert late["digested"] == ["alice"]  # 6 min ≥ 5 min default


def test_card_level_override_tightens_owner_cadence(tmp_path):
    """A per-card reminder_interval_minutes pulls the owner's digest faster."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # One card asks for a 1-min nudge; default would be 5 min.
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0, interval_minutes=1)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))

    sweep_reminders(tasks, now=_NOW, **kw)
    # 90 s later: under 5 min default, but over the card's 1 min override → due.
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(seconds=90), **kw)

    assert out2["digested"] == ["alice"]
    assert len(rec.calls) == 2


def test_config_interval_knob_is_honored(tmp_path, monkeypatch):
    """reminders.interval_minutes in config.yaml sets the cadence."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("reminders:\n  interval_minutes: 2\n", encoding="utf-8")
    monkeypatch.setattr("scitex_todo._config.config_paths", lambda: [cfg])

    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))

    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=3), **kw)

    assert out2["digested"] == ["alice"]  # 3 min ≥ configured 2 min


# === escalation — high-priority overdue → operator (per card) ==============


def test_high_priority_escalates_to_operator_after_threshold(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"operator": "u_op"}),
        operator="operator", escalate_after=2, escalate_priority=1,
        interval_minutes=5.0,
    )

    # 1st sweep: owner digest count→1, below threshold, no escalation.
    sweep_reminders(tasks, now=_NOW, **kw)
    # 2nd sweep after the interval: count→2 == threshold, high-prio → escalate.
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)

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
              escalate_after=1, escalate_priority=1, interval_minutes=5.0)

    sweep_reminders(tasks, now=_NOW, **kw)  # count1 >= 1 → escalate
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)  # already escalated

    assert len(_escalations(rec)) == 1


def test_low_priority_card_never_escalates(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0, priority=5)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}),
              escalate_after=1, escalate_priority=1, interval_minutes=5.0)

    sweep_reminders(tasks, now=_NOW, **kw)
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)

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
              escalate_after=1, escalate_priority=1, interval_minutes=5.0)

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


def test_parked_blocked_card_is_excluded_from_digest(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # alice's stale cards: one in_progress (actionable) + one blocked-with-blocker
    # (parked, waiting on a dep). The digest must list only the actionable one.
    actionable = _t(id="go", owner="alice", status="in_progress", hours_ago=10.0)
    parked = _t(id="wait", owner="alice", status="blocked", hours_ago=10.0)
    parked["blocker"] = "dependency"
    out = sweep_reminders([actionable, parked], store=store, now=_NOW,
                          enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == ["alice"]
    body = rec.calls[0]["body"]
    assert "go" in body and "wait" not in body


def test_owner_with_only_parked_blocked_is_not_nagged(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    parked = _t(id="c1", owner="alice", status="blocked", hours_ago=10.0)
    parked["blocker"] = "operator-decision"
    out = sweep_reminders([parked], store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == []
    assert rec.calls == []


def test_blocked_without_blocker_is_still_digested(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # Blocked but NO blocker = ambiguous (not clearly parked) → still surfaced.
    amb = _t(id="c1", owner="alice", status="blocked", hours_ago=10.0)
    out = sweep_reminders([amb], store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == ["alice"]


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


def test_owner_allowlist_config_scopes_the_sweep(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("reminders:\n  owners: [alice]\n", encoding="utf-8")
    monkeypatch.setattr("scitex_todo._config.config_paths", lambda: [cfg])

    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    out = sweep_reminders(tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({}))
    assert out["digested"] == ["alice"]  # config-scoped to alice


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
    sidecar = tmp_path / "runtime" / "reminders.yaml"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
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


# === liveness → creator escalation (Slice 3) ===============================
#
# Real fakes: ``resolve_user`` maps an owner name → a user DICT carrying a
# ``last_seen`` stamp (or None for a non-registered owner); ``is_alive``
# classifies it against the injected ``now``/ttl. No mocks, no clock.


def _task_with_creator(id, owner, creator, *, hours_ago=50.0, priority=None):
    card = _t(id=id, owner=owner, hours_ago=hours_ago, priority=priority)
    if creator is not None:
        card["created_by"] = creator
    return card


def test_creator_escalation_fires_when_owner_is_stale(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        # alice was last seen 2h ago (> 600s ttl) → "stale".
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )

    assert out["creator_escalated"] == ["c1"]
    esc = _creator_escs(rec)
    assert len(esc) == 1
    assert esc[0]["recipient"] == "u_bob"       # addressed to the CREATOR
    assert esc[0]["card_id"] == "c1"
    assert esc[0]["actor"] == "notifyd"
    assert "alice" in esc[0]["body"]            # names the dead owner
    # A per-card creator escalation is a DISTINCT event — it must NOT supersede
    # (only the cumulative digest collapses).
    assert esc[0]["supersede"] is False


def test_creator_escalation_fires_when_owner_is_unknown(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # alice IS a registered user but has never been seen (no last_seen) →
    # is_alive → "unknown" → still escalate (she isn't running).
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": {"id": "u_alice"}}),  # no last_seen
        liveness_ttl=600,
    )

    assert out["creator_escalated"] == ["c1"]
    assert _creator_escs(rec)[0]["recipient"] == "u_bob"


def test_creator_escalation_does_not_fire_when_owner_is_alive(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        # last seen 60s ago (< 600s ttl) → "alive" → assignee will act.
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=60)}),
        liveness_ttl=600,
    )

    assert out["creator_escalated"] == []
    assert _creator_escs(rec) == []
    # The ordinary owner digest still fires (this is only the liveness path).
    assert out["digested"] == ["alice"]


def test_creator_escalation_does_not_fire_for_non_registered_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({}),
        resolve_user=_user_resolver({}),  # alice resolves to None (free-form)
        liveness_ttl=600,
    )

    # No liveness signal for a non-user owner → no creator escalation.
    assert out["creator_escalated"] == []
    assert _creator_escs(rec) == []


def test_creator_escalation_latches_once_per_streak(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )

    sweep_reminders(tasks, now=_NOW, **kw)
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=30), **kw)

    # Latched once per stale streak — a nudge, not a per-sweep spam stream.
    assert len(_creator_escs(rec)) == 1
    assert load_reminder_state(store)["cards"]["c1"]["creator_escalated"] is True


def test_creator_escalation_falls_back_to_operator_when_creator_is_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    # Creator == the (dead) owner → escalating to a dead self is pointless;
    # fall back to the operator.
    tasks = [_task_with_creator("c1", owner="alice", creator="alice")]

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}), operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )

    assert out["creator_escalated"] == ["c1"]
    assert _creator_escs(rec)[0]["recipient"] == "u_op"


def test_creator_escalation_falls_back_to_operator_when_creator_missing(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator=None)]  # no created_by

    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}), operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )

    assert out["creator_escalated"] == ["c1"]
    assert _creator_escs(rec)[0]["recipient"] == "u_op"


def test_creator_escalation_ignores_digest_cadence(tmp_path):
    """A dead owner fires the creator escalation even when the digest is NOT
    yet due — it does not wait for the escalate_after count."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600, interval_minutes=5.0,
    )

    # 1st sweep digests + creator-escalates. 2nd sweep 1min later: digest NOT
    # due (interval 5min) so alice drops into the early-continue path — but the
    # creator escalation already latched, so it stays a single nudge.
    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=1), **kw)

    assert out2["skipped"] == ["alice"]         # digest not yet due
    assert len(_creator_escs(rec)) == 1         # fired on sweep 1, latched


def test_creator_escalation_latch_pruned_when_card_no_longer_stale(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store, enqueue=rec, resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    assert "c1" in load_reminder_state(store)["cards"]

    # Card done → leaves the stale set → the creator latch resets so a future
    # stall re-escalates (same pruning contract as the operator latch).
    done = [_task_with_creator("c1", owner="alice", creator="bob", hours_ago=50.0)]
    done[0]["status"] = "done"
    sweep_reminders(done, now=_NOW + _dt.timedelta(hours=1), **kw)
    assert "c1" not in load_reminder_state(store)["cards"]


def test_liveness_path_does_not_disturb_operator_escalation(tmp_path):
    """When the owner is ALIVE, the existing high-priority→operator escalation
    still fires unchanged and no creator escalation appears."""
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob",
                                hours_ago=50.0, priority=0)]
    kw = dict(
        store=store, enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}), operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=60)}),
        escalate_after=1, escalate_priority=1, interval_minutes=5.0,
        liveness_ttl=600,
    )

    out = sweep_reminders(tasks, now=_NOW, **kw)

    assert out["escalated"] == ["c1"]           # operator path intact
    assert _escalations(rec)[0]["recipient"] == "u_op"
    assert out["creator_escalated"] == []       # owner alive → no creator path


# EOF
