#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the PENDING-backlog detector.

Pure detector (:func:`detect_pending_backlog` + nudge line) in
:mod:`scitex_cards._stale_active`. Real list-of-dicts inputs (no mocks;
STX-NM / PA-306), AAA structure, one behavioural assertion per test
where practical.

Covers:
  * pending cards older than threshold surfaced, grouped by owner,
    oldest-first
  * fresh / recent pending NOT surfaced
  * non-pending statuses NOT surfaced by the pending detector
  * threshold env override respected; missing timestamp → stale
  * distinct nudge wording from stale-active
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards._stale_active import (
    DEFAULT_PENDING_NUDGE_HOURS,
    ENV_PENDING_NUDGE_HOURS,
    NUDGE_ID_CAP,
    detect_pending_backlog,
    pending_backlog_nudge_line,
)


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _utc(2026, 6, 25, 12, 0, 0)


def _card(cid, status, *, hours_ago=None, created_hours_ago=None, agent="a"):
    t = {"id": cid, "status": status, "title": cid, "agent": agent}
    if hours_ago is not None:
        t["last_activity"] = _iso(NOW - _dt.timedelta(hours=hours_ago))
    if created_hours_ago is not None:
        t["created_at"] = _iso(NOW - _dt.timedelta(hours=created_hours_ago))
    return t


# --------------------------------------------------------------------------- #
# detect_pending_backlog                                                      #
# --------------------------------------------------------------------------- #


class TestDetectPendingBacklog:
    def test_old_pending_surfaced(self):
        tasks = [_card("p1", "deferred", hours_ago=30, agent="a")]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert [c.id for c in out["a"]] == ["p1"]

    def test_fresh_pending_excluded(self):
        tasks = [_card("p1", "deferred", hours_ago=2, agent="a")]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert "a" not in out

    def test_groups_by_owner(self):
        tasks = [
            _card("p1", "deferred", hours_ago=30, agent="a"),
            _card("p2", "deferred", hours_ago=30, agent="b"),
        ]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert set(out.keys()) == {"a", "b"}

    def test_oldest_first_within_owner(self):
        tasks = [
            _card("young", "deferred", hours_ago=25, agent="a"),
            _card("old", "deferred", hours_ago=100, agent="a"),
        ]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert [c.id for c in out["a"]] == ["old", "young"]

    def test_non_pending_statuses_excluded(self):
        tasks = [
            _card("ip", "in_progress", hours_ago=99, agent="a"),
            _card("bl", "blocked", hours_ago=99, agent="a"),
            _card("dn", "done", hours_ago=99, agent="a"),
            _card("pd", "deferred", hours_ago=99, agent="a"),
        ]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert [c.id for c in out["a"]] == ["pd"]

    def test_created_at_fallback_counts(self):
        t = _card("x", "deferred", created_hours_ago=48, agent="a")
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        assert out["a"][0].id == "x"

    def test_no_timestamp_treated_as_stale(self):
        t = {"id": "x", "status": "deferred", "agent": "a", "title": "x"}
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        assert out["a"][0].id == "x"

    def test_no_timestamp_sorts_first(self):
        tasks = [
            _card("timed", "deferred", hours_ago=48, agent="a"),
            {"id": "untimed", "status": "deferred", "agent": "a", "title": "u"},
        ]
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        assert out["a"][0].id == "untimed"

    def test_assignee_fallback_owner(self):
        t = {
            "id": "x", "status": "deferred", "assignee": "z", "title": "x",
            "last_activity": _iso(NOW - _dt.timedelta(hours=48)),
        }
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        assert "z" in out

    def test_unassigned_bucket(self):
        t = {
            "id": "x", "status": "deferred", "title": "x",
            "last_activity": _iso(NOW - _dt.timedelta(hours=48)),
        }
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        assert "(unassigned)" in out

    def test_threshold_env_override(self, monkeypatch):
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "1")
        t = _card("x", "deferred", hours_ago=2, agent="a")
        out = detect_pending_backlog([t], now=NOW)
        assert out["a"][0].id == "x"

    def test_default_threshold_constant(self):
        assert DEFAULT_PENDING_NUDGE_HOURS == 24.0

    def test_default_threshold_more_lenient_than_stale_active(self):
        # A 5h-old pending card is FRESH under the 24h default (would be
        # stale under the 2h stale-active clock).
        t = _card("x", "deferred", hours_ago=5, agent="a")
        out = detect_pending_backlog([t], now=NOW)
        assert "a" not in out


# --------------------------------------------------------------------------- #
# pending_backlog_nudge_line                                                  #
# --------------------------------------------------------------------------- #


class TestPendingNudgeLine:
    def test_line_contains_count_and_ids(self):
        out = detect_pending_backlog(
            [_card("c1", "deferred", hours_ago=30, agent="a")],
            now=NOW, pending_hours=24.0,
        )
        line = pending_backlog_nudge_line("a", out["a"], pending_hours=24.0)
        assert "1 untouched deferred card(s)" in line and "c1" in line

    def test_line_mentions_threshold(self):
        out = detect_pending_backlog(
            [_card("c1", "deferred", hours_ago=30, agent="a")],
            now=NOW, pending_hours=24.0,
        )
        line = pending_backlog_nudge_line("a", out["a"], pending_hours=24.0)
        assert ">24h" in line

    def test_wording_distinct_from_stale_active(self):
        out = detect_pending_backlog(
            [_card("c1", "deferred", hours_ago=30, agent="a")],
            now=NOW, pending_hours=24.0,
        )
        line = pending_backlog_nudge_line("a", out["a"], pending_hours=24.0)
        # Pending = "start or triage", NOT the stale-active "reconcile".
        assert "BACKLOG" in line
        assert "start or triage" in line
        assert "STALE-ACTIVE" not in line

    def test_id_cap_collapses_remainder(self):
        cards = detect_pending_backlog(
            [
                _card(f"c{i}", "deferred", hours_ago=30, agent="a")
                for i in range(NUDGE_ID_CAP + 4)
            ],
            now=NOW, pending_hours=24.0,
        )["a"]
        line = pending_backlog_nudge_line("a", cards, pending_hours=24.0)
        assert "+4 more" in line
