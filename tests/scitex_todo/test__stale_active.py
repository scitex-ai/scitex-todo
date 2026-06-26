#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_todo._stale_active`` (pure detector).

Real list-of-dicts inputs (no mocks; STX-NM / PA-306). AAA structure,
one behavioural assertion per test where practical.

Covers:
  * :func:`is_stale_active` — status gate + threshold + fallback
  * :func:`detect_stale_active` — owner grouping, fresh/stale split,
    in_progress/blocked only, created_at fallback, oldest-first order
  * :func:`stale_active_nudge_line` — concise line + id cap
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo._stale_active import (
    DEFAULT_STALE_ACTIVE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
    NUDGE_ID_CAP,
    detect_stale_active,
    is_stale_active,
    stale_active_nudge_line,
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
# is_stale_active                                                             #
# --------------------------------------------------------------------------- #


class TestIsStaleActive:
    def test_in_progress_old_is_stale(self):
        t = _card("x", "in_progress", hours_ago=5)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is True

    def test_in_progress_recent_is_fresh(self):
        t = _card("x", "in_progress", hours_ago=1)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is False

    def test_blocked_old_is_stale(self):
        t = _card("x", "blocked", hours_ago=5)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is True

    def test_pending_never_stale_active(self):
        # pending is open but NOT active — out of scope.
        t = _card("x", "pending", hours_ago=99)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is False

    def test_done_never_stale_active(self):
        t = _card("x", "done", hours_ago=99)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is False

    def test_fallback_to_created_at_when_no_last_activity(self):
        t = _card("x", "in_progress", created_hours_ago=10)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is True

    def test_fresh_created_at_fallback_is_not_stale(self):
        t = _card("x", "in_progress", created_hours_ago=1)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is False

    def test_no_timestamp_treated_as_stale(self):
        t = {"id": "x", "status": "in_progress", "agent": "a"}
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is True

    def test_boundary_exactly_threshold_is_fresh(self):
        # age == threshold is NOT > threshold → fresh.
        t = _card("x", "in_progress", hours_ago=2)
        assert is_stale_active(t, now=NOW, stale_hours=2.0) is False


# --------------------------------------------------------------------------- #
# detect_stale_active                                                         #
# --------------------------------------------------------------------------- #


class TestDetectStaleActive:
    def test_groups_by_owner(self):
        tasks = [
            _card("a1", "in_progress", hours_ago=5, agent="a"),
            _card("b1", "blocked", hours_ago=5, agent="b"),
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        assert set(out.keys()) == {"a", "b"}

    def test_fresh_card_excluded(self):
        tasks = [
            _card("a1", "in_progress", hours_ago=5, agent="a"),
            _card("a2", "in_progress", hours_ago=1, agent="a"),
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        ids = [c.id for c in out["a"]]
        assert ids == ["a1"]

    def test_only_in_progress_and_blocked(self):
        tasks = [
            _card("p", "pending", hours_ago=99, agent="a"),
            _card("d", "done", hours_ago=99, agent="a"),
            _card("ip", "in_progress", hours_ago=99, agent="a"),
            _card("bl", "blocked", hours_ago=99, agent="a"),
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        assert sorted(c.id for c in out["a"]) == ["bl", "ip"]

    def test_owner_with_no_stale_card_absent(self):
        tasks = [
            _card("a1", "in_progress", hours_ago=1, agent="a"),
            _card("b1", "in_progress", hours_ago=5, agent="b"),
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        assert "a" not in out

    def test_oldest_first_within_owner(self):
        tasks = [
            _card("young", "in_progress", hours_ago=3, agent="a"),
            _card("old", "in_progress", hours_ago=20, agent="a"),
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        assert [c.id for c in out["a"]] == ["old", "young"]

    def test_no_timestamp_sorts_first(self):
        tasks = [
            _card("timed", "in_progress", hours_ago=20, agent="a"),
            {"id": "untimed", "status": "in_progress", "agent": "a"},
        ]
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        assert out["a"][0].id == "untimed"

    def test_assignee_fallback_owner(self):
        t = {
            "id": "x", "status": "in_progress", "assignee": "z",
            "last_activity": _iso(NOW - _dt.timedelta(hours=9)),
        }
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        assert "z" in out

    def test_unassigned_bucket(self):
        t = {
            "id": "x", "status": "in_progress",
            "last_activity": _iso(NOW - _dt.timedelta(hours=9)),
        }
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        assert "(unassigned)" in out

    def test_created_at_fallback_counts(self):
        t = _card("x", "blocked", created_hours_ago=10, agent="a")
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        assert out["a"][0].id == "x"

    def test_default_threshold_via_env(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "1")
        t = _card("x", "in_progress", hours_ago=1.5, agent="a")
        out = detect_stale_active([t], now=NOW)
        assert out["a"][0].id == "x"

    def test_default_threshold_constant(self):
        assert DEFAULT_STALE_ACTIVE_HOURS == 2.0


# --------------------------------------------------------------------------- #
# stale_active_nudge_line                                                     #
# --------------------------------------------------------------------------- #


class TestNudgeLine:
    def test_line_contains_count_and_ids(self):
        out = detect_stale_active(
            [_card("c1", "in_progress", hours_ago=5, agent="a")],
            now=NOW, stale_hours=2.0,
        )
        line = stale_active_nudge_line("a", out["a"], stale_hours=2.0)
        assert "1 stale card(s)" in line and "c1" in line

    def test_line_mentions_threshold(self):
        out = detect_stale_active(
            [_card("c1", "in_progress", hours_ago=5, agent="a")],
            now=NOW, stale_hours=2.0,
        )
        line = stale_active_nudge_line("a", out["a"], stale_hours=2.0)
        assert ">2h" in line

    def test_id_cap_collapses_remainder(self):
        cards = detect_stale_active(
            [
                _card(f"c{i}", "in_progress", hours_ago=5, agent="a")
                for i in range(NUDGE_ID_CAP + 4)
            ],
            now=NOW, stale_hours=2.0,
        )["a"]
        line = stale_active_nudge_line("a", cards, stale_hours=2.0)
        assert "+4 more" in line
