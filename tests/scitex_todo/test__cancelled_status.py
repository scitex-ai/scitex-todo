#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``cancelled`` status — GitHub "closed as not planned" terminal state.

A ``cancelled`` card is CLOSED: distinct from ``done`` (completed
successfully) and ``failed`` (attempted, did not succeed), but treated as
terminal everywhere a closed status is. It must drop out of every
open / actionable / stale / backlog view exactly like ``done`` does, must
not be nagged by the reminder sweep, must not be overdue, and must not
count toward runnable liveness.

Real fakes, NO mocks (STX-NM / PA-306): real ``tmp_path`` YAML stores,
real list-of-dicts inputs, a plain list-recorder ``enqueue``, a real
``resolve_key`` dict, and an injected ``now``. AAA structure.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_todo import TaskValidationError
from scitex_todo._model import VALID_STATUSES, is_overdue, load_tasks
from scitex_todo._reconcile_prs import (
    ACTION_SKIP_NOT_OPEN,
    MERGED,
    OPEN_STATUSES,
    decide_reconcile_action,
)
from scitex_todo._reminders import EVENT_DIGEST, sweep_reminders
from scitex_todo._stale_active import (
    detect_pending_backlog,
    detect_stale_active,
    is_stale_active,
)
from scitex_todo._throughput import TERMINAL_STATUSES, aggregate


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _utc(2026, 6, 30, 12, 0, 0)


# --------------------------------------------------------------------------- #
# enum membership + validation                                               #
# --------------------------------------------------------------------------- #


def test_cancelled_is_in_valid_statuses():
    # "cancelled" is a recognized lifecycle status.
    assert "cancelled" in VALID_STATUSES


def test_cancelled_is_distinct_from_done_and_failed():
    # It is its OWN status, not an alias of done/failed.
    assert {"done", "failed", "cancelled"} <= set(VALID_STATUSES)


def test_load_tasks_accepts_cancelled_status(tmp_path):
    # Arrange — a store with a single cancelled card.
    store = tmp_path / "tasks.yaml"
    store.write_text(
        "tasks:\n  - {id: x, title: Abandoned, status: cancelled}\n",
        encoding="utf-8",
    )
    # Act
    tasks = load_tasks(store)
    # Assert — validates OK and round-trips the status.
    assert tasks[0]["status"] == "cancelled"


def test_load_tasks_warns_on_unknown_status(tmp_path):
    # Guard, updated for the tolerant reader (2026-07-10 outage fix): an
    # unknown status VALUE no longer takes the whole store down — it may have
    # been written by a newer agent, and raising here is exactly how adding
    # `cancelled` bricked every older reader. It still warns loudly, naming
    # the card, and the closed enum stays enforced at the SOURCES (the CLI
    # --status Choice, the board handlers' 400).
    store = tmp_path / "tasks.yaml"
    store.write_text(
        "tasks:\n  - {id: x, title: X, status: wibble}\n", encoding="utf-8"
    )
    with pytest.warns(UserWarning, match="wibble"):
        tasks = load_tasks(store)
    assert tasks[0]["status"] == "wibble"


# --------------------------------------------------------------------------- #
# terminal / closed predicate                                                #
# --------------------------------------------------------------------------- #


def test_cancelled_is_terminal():
    # The central closed-status SSOT includes cancelled.
    assert "cancelled" in TERMINAL_STATUSES
    # Sanity: open statuses are not terminal.
    assert "in_progress" not in TERMINAL_STATUSES
    assert "pending" not in TERMINAL_STATUSES


def test_cancelled_not_in_reconcile_open_statuses():
    # The PR-reconciler never treats cancelled as open work to auto-close.
    assert "cancelled" not in OPEN_STATUSES


def test_reconcile_skips_cancelled_even_when_pr_merged():
    # A cancelled card with a merged PR is NOT auto-flipped — it is closed.
    task = {
        "status": "cancelled",
        "pr_url": "https://github.com/o/r/pull/1",
    }
    assert decide_reconcile_action(task, MERGED) == ACTION_SKIP_NOT_OPEN


# --------------------------------------------------------------------------- #
# throughput — cancelled is NOT open backlog                                 #
# --------------------------------------------------------------------------- #


def test_cancelled_excluded_from_open_count_like_done():
    # Arrange — one open card + one done + one cancelled, same owner.
    tasks = [
        {"id": "open1", "title": "o", "status": "in_progress", "agent": "a"},
        {"id": "done1", "title": "d", "status": "done", "agent": "a"},
        {"id": "cxl1", "title": "c", "status": "cancelled", "agent": "a"},
    ]
    # Act
    rows = aggregate(tasks, by="agent")
    # Assert — only the in_progress card counts as open. cancelled drops
    # out exactly like done.
    assert len(rows) == 1
    assert rows[0].open_count == 1


# --------------------------------------------------------------------------- #
# stale-active + pending-backlog detection                                   #
# --------------------------------------------------------------------------- #


def _card(cid, status, *, hours_ago):
    return {
        "id": cid,
        "title": cid,
        "status": status,
        "agent": "a",
        "last_activity": _iso(NOW - _dt.timedelta(hours=hours_ago)),
    }


def test_cancelled_is_never_stale_active():
    # A long-untouched cancelled card is NOT stale-active (it is closed,
    # not live work the owner is claiming).
    t = _card("x", "cancelled", hours_ago=999)
    assert is_stale_active(t, now=NOW, stale_hours=2.0) is False


def test_cancelled_excluded_from_stale_active_detection():
    # Arrange — one genuinely stale in_progress + one stale cancelled.
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    groups = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
    # Assert — only the live card surfaces; cancelled never does.
    surfaced = {sc.id for cards in groups.values() for sc in cards}
    assert surfaced == {"live"}


def test_cancelled_excluded_from_pending_backlog():
    # Arrange — one old pending (backlog) + one old cancelled.
    tasks = [
        _card("waiting", "pending", hours_ago=99),
        _card("cxl", "cancelled", hours_ago=99),
    ]
    # Act
    groups = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
    # Assert — only the pending card is backlog; cancelled is closed.
    surfaced = {sc.id for cards in groups.values() for sc in cards}
    assert surfaced == {"waiting"}


# --------------------------------------------------------------------------- #
# reminder sweep — cancelled is never nagged                                 #
# --------------------------------------------------------------------------- #


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
        }
        self.calls.append(rec)
        return rec


@pytest.fixture(autouse=True)
def _isolate_engine(monkeypatch):
    for var in (
        "SCITEX_TODO_REMINDER_OWNERS",
        "SCITEX_TODO_STALE_ACTIVE_HOURS",
        "SCITEX_TODO_PENDING_NUDGE_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("scitex_todo._config.config_paths", lambda: [])


def test_reminder_sweep_does_not_nag_cancelled_card(tmp_path):
    # Arrange — a single, long-untouched cancelled card owned by alice.
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_card("cxl", "cancelled", hours_ago=999)]
    # Act
    out = sweep_reminders(
        tasks,
        store=store,
        now=NOW,
        enqueue=rec,
        resolve_key=lambda name: name,
    )
    # Assert — no digest enqueued for the cancelled card.
    assert out["digested"] == []
    assert rec.calls == []


def test_reminder_sweep_nags_live_card_but_not_its_cancelled_sibling(tmp_path):
    # Arrange — a stale in_progress card alongside a stale cancelled card,
    # same owner. Only the live one should appear in the digest.
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    sweep_reminders(
        tasks,
        store=store,
        now=NOW,
        enqueue=rec,
        resolve_key=lambda name: name,
    )
    # Assert — one digest, mentioning the live card but not the cancelled one.
    digests = [c for c in rec.calls if c["event_type"] == EVENT_DIGEST]
    assert len(digests) == 1
    body = digests[0]["body"]
    assert "live" in body
    assert "cxl" not in body


# --------------------------------------------------------------------------- #
# is_overdue — cancelled is closed, never overdue                            #
# --------------------------------------------------------------------------- #


def test_cancelled_with_past_deadline_is_not_overdue():
    # A cancelled card with a deadline in the past is NOT overdue — it is
    # closed, same as done/deferred/failed.
    task = {"id": "x", "title": "x", "status": "cancelled", "deadline": "2026-06-01"}
    assert is_overdue(task, now=_utc(2026, 6, 30, 12, 0, 0)) is False
