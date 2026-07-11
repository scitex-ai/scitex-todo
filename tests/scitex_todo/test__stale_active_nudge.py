#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_todo._stale_active_nudge`` (delivery sweep).

No mocks (STX-NM / PA-306): we drive the REAL push wire in dry-run mode
(``SCITEX_TODO_PUSH_DRY_RUN=1`` → ok=True, no network) and a real
no-turn-url path (fail-soft). Threshold is pinned via the env so the
test is independent of wall-clock-relative defaults.

The sweep persists its deliver-on-change state in a sidecar under the store's
``runtime/`` dir, so every test runs against a REAL but ``tmp_path``-scoped
store (``_isolated_store``) — never the user's canonical one.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scitex_todo._paths import ENV_TASKS
from scitex_todo._push import ENV_DRY_RUN
from scitex_todo._stale_active import (
    ENV_PENDING_NUDGE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
)
from scitex_todo._stale_active_nudge import (
    ENV_NUDGE_FLOOR_HOURS,
    KIND_STALE_ACTIVE,
    load_nudge_state,
    sweep_and_nudge,
)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the store (and therefore the nudge sidecar) at a tmp dir."""
    store = tmp_path / "tasks.yaml"
    monkeypatch.setenv(ENV_TASKS, str(store))
    return store


@contextlib.contextmanager
def _local_receiver():
    """A REAL local turn-url receiver (no mocks): answers 200, records bodies."""
    received: list[bytes] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's contract.
            length = int(self.headers.get("Content-Length") or 0)
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/turn", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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

    def test_raising_owner_does_not_starve_the_other_owner(self, monkeypatch):
        # The batch guarantee, on the REAL wire (no mocks): one owner's turn URL
        # raises (no scheme → urlopen ValueError), the other's points at a real
        # local receiver that answers 200. The raiser must NOT abort the batch:
        # the healthy owner is still DELIVERED.
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        with _local_receiver() as (url, received):
            monkeypatch.setenv("SCITEX_TODO_TURN_URL_BADOWNER", "noscheme-url")
            monkeypatch.setenv("SCITEX_TODO_TURN_URL_GOODOWNER", url)
            tasks = [_stale("s1", "badowner"), _stale("s2", "goodowner")]
            lines = sweep_and_nudge(tasks)
        joined = "\n".join(lines)
        assert "push raised" in joined
        assert "# 1 stale-active push(es) sent" in lines
        assert len(received) == 1


class TestDeliverOnChange:
    """The sweep is SCHEDULED, so an unchanged stale set must not re-push."""

    def test_unchanged_set_is_suppressed_on_second_sweep(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale("a1", "alpha")]
        assert "# 1 stale-active push(es) sent" in sweep_and_nudge(tasks)

        lines = sweep_and_nudge(tasks)
        assert "# 0 stale-active push(es) sent" in lines
        # Suppressed, but NOT silent — the owner is still surfaced with a reason.
        joined = "\n".join(lines)
        assert "alpha" in joined and "suppressed" in joined

    def test_added_card_repushes(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale("a1", "alpha")])
        lines = sweep_and_nudge([_stale("a1", "alpha"), _stale("a2", "alpha")])
        assert "# 1 stale-active push(es) sent" in lines

    def test_removed_card_repushes(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale("a1", "alpha"), _stale("a2", "alpha")])
        lines = sweep_and_nudge([_stale("a1", "alpha")])
        assert "# 1 stale-active push(es) sent" in lines

    def test_touched_card_leaving_the_bucket_repushes(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale("a1", "alpha"), _stale("a2", "alpha")])
        # a2 is touched → it drops out of the stale bucket → the set CHANGED.
        lines = sweep_and_nudge([_stale("a1", "alpha"), _fresh("a2", "alpha")])
        assert "# 1 stale-active push(es) sent" in lines

    def test_owner_state_pruned_when_no_cards_left(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale("a1", "alpha")])
        sweep_and_nudge([_fresh("a1", "alpha")])
        assert "alpha" not in load_nudge_state()[KIND_STALE_ACTIVE]

    def test_floor_elapsed_repushes_the_unchanged_set(self, monkeypatch):
        # A genuinely stuck owner must still be nudged once the floor elapses,
        # even though nothing about their card set changed.
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_NUDGE_FLOOR_HOURS, "24")
        t0 = _dt.datetime(2026, 7, 11, 9, 0, tzinfo=_dt.timezone.utc)
        # Timestamp relative to the INJECTED clock, not wall-clock.
        tasks = [{
            "id": "a1", "status": "in_progress", "title": "a1", "agent": "alpha",
            "last_activity": (t0 - _dt.timedelta(hours=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }]

        assert "# 1 stale-active push(es) sent" in sweep_and_nudge(tasks, now=t0)
        mid = t0 + _dt.timedelta(hours=6)
        assert "# 0 stale-active push(es) sent" in sweep_and_nudge(tasks, now=mid)
        past = t0 + _dt.timedelta(hours=25)
        assert "# 1 stale-active push(es) sent" in sweep_and_nudge(tasks, now=past)

    def test_failed_push_is_not_suppressed_next_sweep(self, monkeypatch):
        # A push that did NOT land must be retried, not treated as delivered.
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale("a1", "nourlowner")]
        sweep_and_nudge(tasks)  # ok=False (no-turn-url-configured)
        assert "nourlowner" not in load_nudge_state()[KIND_STALE_ACTIVE]
        joined = "\n".join(sweep_and_nudge(tasks))
        assert "suppressed" not in joined

    def test_pending_backlog_is_suppressed_independently(self, monkeypatch):
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        tasks = [_stale("s1", "alpha"), _pending("p1", "alpha")]
        sweep_and_nudge(tasks)
        # Only the stale-active set changes; the backlog set does not.
        lines = sweep_and_nudge([*tasks, _stale("s2", "alpha")])
        assert "# 1 stale-active push(es) sent" in lines
        assert "# 0 pending-backlog push(es) sent" in lines
