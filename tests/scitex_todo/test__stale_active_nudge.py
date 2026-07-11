#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_todo._stale_active_nudge`` (delivery sweep).

No mocks (STX-NM / PA-306): we drive the REAL push wire in dry-run mode
(``SCITEX_TODO_PUSH_DRY_RUN=1`` → ok=True, no network) and a real
no-turn-url path (fail-soft). Threshold is pinned via the env so the
test is independent of wall-clock-relative defaults.
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo._push import ENV_DRY_RUN
from scitex_todo._stale_active import (
    ENV_PENDING_NUDGE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
)
from scitex_todo._stale_active_nudge import sweep_and_nudge


def _iso_hours_ago(h):
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    return (now - _dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale(cid, agent, status="in_progress"):
    return {
        "id": cid, "status": status, "title": cid, "agent": agent,
        "last_activity": _iso_hours_ago(10),
    }


def _pending(cid, agent):
    return {
        "id": cid, "status": "deferred", "title": cid, "agent": agent,
        "last_activity": _iso_hours_ago(48),
    }


def _fresh(cid, agent):
    return {
        "id": cid, "status": "in_progress", "title": cid, "agent": agent,
        "last_activity": _iso_hours_ago(0.1),
    }


class TestSweepAndNudge:
    def test_pushes_per_owner_in_dry_run(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale("a1", "alpha"), _stale("b1", "beta")]
        lines = sweep_and_nudge(tasks)
        assert "# 2 stale-active push(es) sent" in lines

    def test_fresh_owner_not_pushed(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_fresh("a1", "alpha")]
        lines = sweep_and_nudge(tasks)
        assert "# 0 stale-active push(es) sent" in lines

    def test_unassigned_surfaced_not_pushed(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [{
            "id": "x", "status": "blocked", "title": "x",
            "last_activity": _iso_hours_ago(10),
        }]
        lines = sweep_and_nudge(tasks)
        joined = "\n".join(lines)
        assert "(unassigned)" in joined and "no owner" in joined

    def test_no_url_is_fail_soft(self, monkeypatch):
        # Real no-turn-url path: dry-run OFF, no env URL configured →
        # deliver returns ok=False, reason="no-turn-url-configured".
        # The sweep must NOT raise and must still emit a summary.
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale("a1", "nourlowner")]
        lines = sweep_and_nudge(tasks)
        assert "# 0 stale-active push(es) sent" in lines


class TestSweepBothKinds:
    def test_pending_summary_emitted(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        tasks = [_pending("p1", "alpha")]
        lines = sweep_and_nudge(tasks)
        assert "# 1 pending-backlog push(es) sent" in lines
        # No stale-active card present → 0 of that kind.
        assert "# 0 stale-active push(es) sent" in lines

    def test_both_lines_for_owner_with_both(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        # alpha has BOTH a stale in_progress card and an old pending card.
        tasks = [_stale("s1", "alpha"), _pending("p1", "alpha")]
        lines = sweep_and_nudge(tasks)
        assert "# 1 stale-active push(es) sent" in lines
        assert "# 1 pending-backlog push(es) sent" in lines

    def test_fresh_pending_not_pushed(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        # 0.1h-old pending is fresh under the 24h threshold.
        fresh_pending = {
            "id": "p1", "status": "deferred", "title": "p1", "agent": "alpha",
            "last_activity": _iso_hours_ago(0.1),
        }
        lines = sweep_and_nudge([fresh_pending])
        assert "# 0 pending-backlog push(es) sent" in lines

    def test_fail_soft_when_one_owner_delivery_raises(self, monkeypatch):
        # Real raise path (no mocks): point one owner's turn URL at an
        # unknown url type so urlopen raises ValueError (NOT caught inside
        # deliver) — the sweep's per-owner guard must absorb it and the
        # OTHER owner must still be delivered + the summary emitted.
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        # ``deliver`` slugs the agent name as UPPER, '-'→'_'.
        monkeypatch.setenv("SCITEX_TODO_TURN_URL_BADOWNER", "noscheme-url")
        tasks = [_stale("s1", "badowner"), _pending("p1", "badowner")]
        lines = sweep_and_nudge(tasks)
        joined = "\n".join(lines)
        # The sweep did not raise; both summaries are present and the
        # raising owner is surfaced as a guarded failure, not a crash.
        assert "# 0 stale-active push(es) sent" in lines
        assert "# 0 pending-backlog push(es) sent" in lines
        assert "push raised" in joined
