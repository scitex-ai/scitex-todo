#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rescore_task`` — the rank engine's contract (v5-lite, ADR-0011 §1/§8).

Pinned here, per the contract on card
``scitex-cards-schema-v5-axes-rank-rescore-verb-20260717``:

1. THE ORDER — quadrant II precedes III BY CONSTRUCTION (not by weight
   luck); score orders within a band; ties break by first-scored time
   (waiting never costs position) then id; terminal cards hold axes but
   never a rank; the ranks are a contiguous 1..N total order.
2. THE VERB — axes land, ``scored_at`` stamps ONCE (a re-drag never
   resets seniority), the audit comment carries the machine-readable
   old→new payload occupancy replay needs, and ONLY the rescored card's
   ``last_activity`` moves — neighbours re-rank silently (the
   priority.py inactivity-clock lesson, enforced by test).
3. THE EVENT — one ``rank_changed`` per rescore, captured via the
   documented in-process ``entry_points=`` seam (real fake handler, no
   mocks), carrying the same transition payload.
4. THE SEAM — ``rescore_task`` is a BACKEND_VERBS member callable on
   BOTH backends (the HubBackend-completeness walk added here is the
   gap the seam test's Local-only walk left open).
5. THE THRESHOLD PARITY — the engine's HIGH_THRESHOLD equals the matrix
   view's QUADRANT_THRESHOLD, read from the SHIPPED JS, so the drawn
   quadrants and the computed order can never disagree silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scitex_cards import _store
from scitex_cards._store_rescore import (
    HIGH_THRESHOLD,
    UNRANKED_STATUSES,
    recompute_ranks,
    rescore_task,
)

# === In-process injection seam (real fake handler, no mocks) ===============


class _Capturing:
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _eps(sink: _Capturing) -> list[_FakeEP]:
    return [_FakeEP("captor", sink)]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "rank-tester")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _add(store, card_id, status="deferred"):
    _store.add_task(
        store,
        id=card_id,
        title=f"Card {card_id}",
        status=status,
        assignee="alice",
        created_by="rank-tester",
    )


# === 1. the order (pure engine, no I/O) ====================================


def _card(cid, u, i, status="deferred", scored_at=None):
    out = {"id": cid, "status": status, "urgency": u, "importance": i}
    if scored_at:
        out["scored_at"] = scored_at
    return out


def test_quadrant_ii_precedes_iii_by_construction():
    """Worst-II (u=1,i=3, score 7) must outrank best-III (u=5,i=2, score 9):
    the guarantee lives in the order key, not the scalar weights."""
    tasks = [_card("iii-best", 5, 2), _card("ii-worst", 1, 3)]
    recompute_ranks(tasks)
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["ii-worst"] < ranks["iii-best"]


def test_score_orders_within_a_band():
    tasks = [_card("low", 1, 3), _card("high", 5, 5), _card("mid", 3, 4)]
    recompute_ranks(tasks)
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["high"] < ranks["mid"] < ranks["low"]


def test_aging_tie_break_older_scored_first():
    """Equal axes: the card scored EARLIER wins — waiting never costs."""
    tasks = [
        _card("young", 3, 3, scored_at="2026-07-17T10:00:00Z"),
        _card("old", 3, 3, scored_at="2026-07-01T10:00:00Z"),
    ]
    recompute_ranks(tasks)
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["old"] < ranks["young"]


def test_terminal_cards_keep_axes_but_hold_no_rank():
    tasks = [_card("live", 3, 3), _card("done", 5, 5, status="done")]
    n = recompute_ranks(tasks)
    assert n == 1
    assert tasks[0]["rank"] == 1
    assert "rank" not in tasks[1] and tasks[1]["urgency"] == 5


def test_ranks_are_a_contiguous_total_order():
    tasks = [_card(f"c{i}", (i % 5) + 1, ((i * 2) % 5) + 1) for i in range(9)]
    n = recompute_ranks(tasks)
    assert sorted(t["rank"] for t in tasks) == list(range(1, n + 1))


def test_unscored_cards_are_never_ranked():
    tasks = [_card("scored", 3, 3), {"id": "bare", "status": "deferred"}]
    recompute_ranks(tasks)
    assert "rank" not in tasks[1]


def test_stale_rank_on_a_now_terminal_card_is_stripped():
    tasks = [_card("was-ranked", 3, 3, status="done")]
    tasks[0]["rank"] = 7
    recompute_ranks(tasks)
    assert "rank" not in tasks[0]


# === 2. the verb ===========================================================


def test_rescore_sets_axes_rank_and_returns_the_triple(store):
    _add(store, "r-a")
    result = rescore_task(store, "r-a", urgency=4, importance=5)
    assert (result["rank"], result["of"]) == (1, 1)
    on_disk = _store.get_task(store, "r-a")
    assert (on_disk["urgency"], on_disk["importance"], on_disk["rank"]) == (4, 5, 1)


def test_scored_at_stamps_once_never_resets(store):
    _add(store, "r-age")
    rescore_task(store, "r-age", urgency=2, importance=2)
    first = _store.get_task(store, "r-age")["scored_at"]
    rescore_task(store, "r-age", urgency=5, importance=5)
    assert _store.get_task(store, "r-age")["scored_at"] == first


def test_audit_comment_carries_the_machine_payload(store):
    _add(store, "r-audit")
    rescore_task(store, "r-audit", urgency=2, importance=4, by="dragger")
    comments = _store.get_task(store, "r-audit")["comments"]
    entry = comments[-1]
    assert entry["kind"] == "rescore" and entry["author"] == "dragger"
    assert entry["rescore"]["urgency"] == [None, 2]
    assert entry["rescore"]["importance"] == [None, 4]
    assert entry["rescore"]["rank"] == [None, 1]
    assert entry["rescore"]["of"] == 1


def test_neighbours_rerank_silently_without_activity_restamp(store):
    """The binding priority.py lesson: a re-rank must not reset every
    inactivity-nudge clock on the board."""
    _add(store, "r-n1")
    rescore_task(store, "r-n1", urgency=1, importance=1)
    before = _store.get_task(store, "r-n1")["last_activity"]
    _add(store, "r-n2")
    rescore_task(store, "r-n2", urgency=5, importance=5)  # displaces r-n1 to rank 2
    after = _store.get_task(store, "r-n1")
    assert after["rank"] == 2
    assert after["last_activity"] == before


def test_rescoring_a_terminal_card_returns_null_rank(store):
    _add(store, "r-done", status="done")
    result = rescore_task(store, "r-done", urgency=3, importance=3)
    assert result["rank"] is None
    assert "rank" not in _store.get_task(store, "r-done")


def test_axis_validation_fails_loud(store):
    _add(store, "r-bad")
    with pytest.raises(ValueError):
        rescore_task(store, "r-bad", urgency=0, importance=3)
    with pytest.raises(ValueError):
        rescore_task(store, "r-bad", urgency=3, importance=6)
    with pytest.raises(ValueError):
        rescore_task(store, "r-bad", urgency=True, importance=3)


def test_unknown_id_raises_tasknotfound(store):
    with pytest.raises(_store.TaskNotFoundError):
        rescore_task(store, "ghost", urgency=3, importance=3)


# === 3. the event ==========================================================


def test_one_rank_changed_event_with_the_transition_payload(store):
    _add(store, "r-ev1")
    _add(store, "r-ev2")
    sink = _Capturing()
    rescore_task(
        store, "r-ev1", urgency=2, importance=5, by="dragger", entry_points=_eps(sink)
    )
    events = [
        e
        for e in sink.events
        if e.get("kind") == "card-event" and e.get("type") == "rank_changed"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["card_id"] == "r-ev1" and event["actor"] == "dragger"
    assert event["importance"] == [None, 5]
    assert event["of"] == 1
    # Neighbour r-ev2 (unscored) emitted nothing — and even a scored
    # neighbour would not: one event per rescore, for the rescored card.
    assert all(e.get("card_id") != "r-ev2" for e in sink.events)


# === 4. the seam ===========================================================


def test_rescore_is_a_backend_verb_on_both_backends():
    from scitex_cards._backend import BACKEND_VERBS, LocalBackend
    from scitex_cards._backend_http import HubBackend

    assert "rescore_task" in BACKEND_VERBS
    # Completeness on BOTH implementations — the Local-only walk in the
    # seam tests left the Hub side uncovered; this closes it for every
    # verb, not just rescore.
    hub = HubBackend("http://127.0.0.1:1")
    local = LocalBackend()
    missing = [
        v
        for v in BACKEND_VERBS
        if not callable(getattr(local, v, None)) or not callable(getattr(hub, v, None))
    ]
    assert missing == []


# === 5. threshold parity with the shipped matrix view ======================


def test_engine_threshold_equals_the_matrix_views():
    js = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "scitex_cards"
        / "_django"
        / "static"
        / "scitex_cards"
        / "board_v3"
        / "14-matrix.js"
    ).read_text(encoding="utf-8")
    m = re.search(r"QUADRANT_THRESHOLD\s*=\s*(\d+)", js)
    assert m, "QUADRANT_THRESHOLD not found in the shipped matrix module"
    assert int(m.group(1)) == HIGH_THRESHOLD


def test_unranked_statuses_are_the_terminal_set():
    assert UNRANKED_STATUSES == {"done", "cancelled", "failed"}


# EOF
