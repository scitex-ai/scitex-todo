#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the digest replay-storm fix (supersede-on-enqueue + collapse).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
``enqueue`` / ``poll_inbox`` / ``collapse_digests`` against it. Covers:

* ``enqueue(..., supersede=True)`` for the cumulative digest keeps at most ONE
  unseen digest — a fresh snapshot strictly replaces its unseen predecessors.
* supersede leaves SEEN digests + OTHER event_types untouched.
* the non-supersede path keeps the ``(type,card,ts,actor)`` dedup unchanged.
* ``collapse_digests`` clears an accumulated backlog to the single newest
  unseen digest in one locked pass, marking the rest seen, others untouched.
"""

from __future__ import annotations

from scitex_todo._inbox import enqueue, poll_inbox
from scitex_todo._inbox_maint import collapse_digests
from scitex_todo._reminders import DIGEST_CARD_ID, EVENT_DIGEST


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _enqueue_digest(store, recipient, ts, *, supersede=True):
    return enqueue(
        recipient,
        event_type=EVENT_DIGEST,
        card_id=DIGEST_CARD_ID,
        body=f"digest snapshot @ {ts}",
        actor="notifyd",
        ts=ts,
        supersede=supersede,
        store=store,
    )


# --------------------------------------------------------------------------- #
# Change 1 — supersede-on-enqueue                                             #
# --------------------------------------------------------------------------- #
def test_supersede_keeps_only_latest_unseen_digest(tmp_path):
    store = _store(tmp_path)
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z")
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")
    last = _enqueue_digest(store, "u_owner", "2026-07-08T00:00:00Z")

    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    assert len(unseen) == 1, "at most one pending digest must survive"
    assert unseen[0]["ts"] == "2026-07-08T00:00:00Z"
    assert unseen[0]["id"] == last["id"]
    # The two earlier snapshots are GONE (removed, not merely marked seen):
    # the full inbox history holds only the surviving digest.
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    assert [r["ts"] for r in everything] == ["2026-07-08T00:00:00Z"]


def test_supersede_preserves_seen_digest(tmp_path):
    store = _store(tmp_path)
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z")
    # Drain (mark seen) the first digest — it is now history.
    poll_inbox("u_owner", unseen_only=True, mark_seen=True, store=store)
    # A new supersede-enqueue must NOT touch the seen record.
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")

    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    assert len(everything) == 2
    seen = [r for r in everything if r.get("seen")]
    unseen = [r for r in everything if not r.get("seen")]
    assert len(seen) == 1 and seen[0]["ts"] == "2026-07-06T00:00:00Z"
    assert len(unseen) == 1 and unseen[0]["ts"] == "2026-07-07T00:00:00Z"


def test_supersede_does_not_touch_other_event_types(tmp_path):
    store = _store(tmp_path)
    enqueue(
        "u_owner",
        event_type="commented",
        card_id="c1",
        body="bob commented on c1",
        actor="bob",
        ts="2026-07-06T00:00:00Z",
        store=store,
    )
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z")

    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    types = sorted(r["event_type"] for r in unseen)
    assert types == ["commented", EVENT_DIGEST]
    # The commented per-card event survives the digest supersede untouched.
    commented = [r for r in unseen if r["event_type"] == "commented"]
    assert len(commented) == 1 and commented[0]["card_id"] == "c1"


def test_non_supersede_path_keeps_dedup(tmp_path):
    store = _store(tmp_path)
    first = enqueue(
        "u_owner",
        event_type="commented",
        card_id="c1",
        body="bob commented on c1",
        actor="bob",
        ts="2026-07-06T00:00:00Z",
        store=store,
    )
    # Same (type, card, ts, actor) re-emit → deduped (returns None, no dup).
    dup = enqueue(
        "u_owner",
        event_type="commented",
        card_id="c1",
        body="bob commented on c1",
        actor="bob",
        ts="2026-07-06T00:00:00Z",
        store=store,
    )
    assert first is not None
    assert dup is None
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    assert len(everything) == 1


def test_non_supersede_distinct_ts_kept(tmp_path):
    store = _store(tmp_path)
    # Two digests WITHOUT supersede: distinct ts → both kept (old behavior).
    _enqueue_digest(store, "u_owner", "2026-07-06T00:00:00Z", supersede=False)
    _enqueue_digest(store, "u_owner", "2026-07-07T00:00:00Z", supersede=False)
    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    assert len(unseen) == 2


# --------------------------------------------------------------------------- #
# Change 3 — collapse_digests backlog sweep                                   #
# --------------------------------------------------------------------------- #
def test_collapse_digests_clears_backlog(tmp_path):
    store = _store(tmp_path)
    # Seed an accumulated backlog (supersede=False mimics the old buggy path).
    for day in range(4, 9):
        _enqueue_digest(
            store, "u_owner", f"2026-07-0{day}T00:00:00Z", supersede=False
        )
    # A non-digest record + a genuine per-card event must survive untouched.
    enqueue(
        "u_owner",
        event_type="reassigned",
        card_id="c9",
        body="c9 reassigned to you",
        actor="alice",
        ts="2026-07-05T12:00:00Z",
        store=store,
    )

    summary = collapse_digests(store=store)
    assert summary["recipients_collapsed"] == 1
    assert summary["digests_marked_seen"] == 4  # 5 digests → keep 1, seen 4

    unseen = poll_inbox("u_owner", unseen_only=True, store=store)
    unseen_digests = [r for r in unseen if r["event_type"] == EVENT_DIGEST]
    assert len(unseen_digests) == 1
    assert unseen_digests[0]["ts"] == "2026-07-08T00:00:00Z"  # newest kept
    # The non-digest event is still unseen (untouched).
    assert any(r["event_type"] == "reassigned" for r in unseen)
    # Nothing deleted — the full history still holds all 6 records.
    everything = poll_inbox("u_owner", unseen_only=False, store=store)
    assert len(everything) == 6


def test_collapse_digests_idempotent(tmp_path):
    store = _store(tmp_path)
    for day in range(4, 7):
        _enqueue_digest(
            store, "u_owner", f"2026-07-0{day}T00:00:00Z", supersede=False
        )
    first = collapse_digests(store=store)
    assert first["recipients_collapsed"] == 1
    # A second pass has nothing left to collapse.
    second = collapse_digests(store=store)
    assert second == {"recipients_collapsed": 0, "digests_marked_seen": 0}


def test_collapse_digests_multi_recipient(tmp_path):
    store = _store(tmp_path)
    for day in range(4, 7):
        _enqueue_digest(store, "u_a", f"2026-07-0{day}T00:00:00Z", supersede=False)
    for day in range(4, 8):
        _enqueue_digest(store, "u_b", f"2026-07-0{day}T00:00:00Z", supersede=False)
    summary = collapse_digests(store=store)
    assert summary["recipients_collapsed"] == 2
    assert summary["digests_marked_seen"] == (3 - 1) + (4 - 1)
    assert len(poll_inbox("u_a", store=store)) == 1
    assert len(poll_inbox("u_b", store=store)) == 1
