#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_todo._stale_active_nudge`` (delivery sweep).

No mocks (STX-NM / PA-306): the sweep delivers on the REAL pull-inbox rail
(:func:`scitex_todo._inbox.enqueue`) against a ``tmp_path``-scoped store, and
every delivery assertion reads the OWNER'S REAL INBOX back with
:func:`scitex_todo._inbox.poll_inbox` — the same call an agent's drain makes.
The fail-soft tests inject a REAL failing enqueue function (the documented seam,
like ``sweep_reminders``'s), never a mock of the sweep itself.

This is the 2026-07-12 fix: the nudge used to push on the turn-url wire, which
is not provisioned for almost any agent — the scheduled sweep delivered to
NOBODY (every owner ``ERR ... reason=no-turn-url-configured``). It now rides the
SAME rail the owner digest does.

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

from scitex_todo._inbox import enqueue as real_enqueue
from scitex_todo._inbox import poll_inbox
from scitex_todo._paths import ENV_TASKS
from scitex_todo._push import ENV_DRY_RUN
from scitex_todo._stale_active import (
    ENV_PENDING_NUDGE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
)
from scitex_todo._stale_active_nudge import (
    ENV_NUDGE_FLOOR_HOURS,
    ENV_NUDGE_PUSH,
    KIND_PENDING_BACKLOG,
    KIND_STALE_ACTIVE,
    NUDGE_CARD_ID,
    load_nudge_state,
    sweep_and_nudge,
)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the store (and therefore the inbox + nudge sidecar) at a tmp dir."""
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


#: A fixed sweep instant. The deliver-on-change tests run TWO sweeps and the
#: inbox dedups on ``(event_type, card_id, ts, actor)`` at second resolution, so
#: back-to-back wall-clock sweeps inside one second would dedup and look like a
#: failed re-delivery. Real sweeps are ~30 min apart; the tests pin the clock.
T0 = _dt.datetime(2026, 7, 11, 9, 0, tzinfo=_dt.timezone.utc)
T1 = T0 + _dt.timedelta(minutes=30)


def _at(base, hours):
    return (base - _dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale_at(cid, agent, *, base=T0, hours=10):
    return {
        "id": cid, "status": "in_progress", "title": cid, "agent": agent,
        "last_activity": _at(base, hours),
    }


def _pending_at(cid, agent, *, base=T0, hours=48):
    return {
        "id": cid, "status": "deferred", "title": cid, "agent": agent,
        "last_activity": _at(base, hours),
    }


def _fresh_at(cid, agent, *, base=T0):
    return _stale_at(cid, agent, base=base, hours=0.1)


def _drain(owner):
    """Read + ACK an owner's unseen inbox, exactly like an agent's drain."""
    return poll_inbox(owner, unseen_only=True, mark_seen=True)


def _failing_enqueue(*owners):
    """A REAL enqueue that RAISES for ``owners`` and delivers for everyone else.

    The injectable ``enqueue`` seam (same one ``sweep_reminders`` exposes), not
    a mock of the sweep: healthy owners still land in the REAL inbox, so the
    fail-soft assertions read the real store.
    """
    bad = set(owners)

    def _enqueue(recipient_id, **kwargs):
        if recipient_id in bad:
            raise RuntimeError(f"inbox write refused for {recipient_id}")
        return real_enqueue(recipient_id, **kwargs)

    return _enqueue


class TestNudgeLandsInTheOwnerInbox:
    """The whole point: the nudge must REACH the owner, on the rail they drain."""

    def test_stale_nudge_lands_in_that_owners_inbox(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale("a1", "alpha"), _stale("b1", "beta")])

        alpha = poll_inbox("alpha", unseen_only=True)
        assert len(alpha) == 1
        rec = alpha[0]
        assert rec["event_type"] == KIND_STALE_ACTIVE
        assert rec["card_id"] == NUDGE_CARD_ID[KIND_STALE_ACTIVE]
        assert "a1" in rec["body"]
        # …and it went to ALPHA, not to everybody.
        assert "b1" not in rec["body"]
        beta = poll_inbox("beta", unseen_only=True)
        assert len(beta) == 1 and "b1" in beta[0]["body"]

    def test_pending_backlog_nudge_lands_in_the_owner_inbox(self, monkeypatch):
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        sweep_and_nudge([_pending("p1", "alpha")])

        recs = poll_inbox("alpha", unseen_only=True)
        assert [r["event_type"] for r in recs] == [KIND_PENDING_BACKLOG]
        assert recs[0]["card_id"] == NUDGE_CARD_ID[KIND_PENDING_BACKLOG]
        assert "p1" in recs[0]["body"]

    def test_both_kinds_are_distinct_records(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        sweep_and_nudge([_stale("s1", "alpha"), _pending("p1", "alpha")])

        kinds = sorted(r["event_type"] for r in poll_inbox("alpha"))
        assert kinds == [KIND_PENDING_BACKLOG, KIND_STALE_ACTIVE]

    def test_fresh_owner_gets_nothing(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        lines = sweep_and_nudge([_fresh("a1", "alpha")])
        assert poll_inbox("alpha") == []
        assert any("0 delivered" in ln for ln in lines)

    def test_unassigned_surfaced_not_delivered(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [{
            "id": "x", "status": "blocked", "title": "x",
            "last_activity": _iso_hours_ago(10),
        }]
        lines = sweep_and_nudge(tasks)
        joined = "\n".join(lines)
        assert "(unassigned)" in joined and "no owner" in joined
        assert poll_inbox("(unassigned)") == []

    def test_summary_counts_the_inbox_rail(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        lines = sweep_and_nudge([_stale("a1", "alpha"), _stale("b1", "beta")])
        assert (
            "# stale-active: 2 owner(s) detected, 2 delivered (inbox), "
            "0 suppressed, 0 failed" in lines
        )


class TestDeliverOnChange:
    """The sweep is SCHEDULED, so an unchanged stale set must not re-deliver."""

    def test_unchanged_set_is_suppressed_on_second_sweep(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale_at("a1", "alpha")]
        sweep_and_nudge(tasks, now=T0)
        assert len(_drain("alpha")) == 1  # the agent read it

        lines = sweep_and_nudge(tasks, now=T1)
        assert poll_inbox("alpha", unseen_only=True) == []  # nothing new
        # Suppressed, but NOT silent — the owner is still surfaced with a reason.
        joined = "\n".join(lines)
        assert "alpha" in joined and "suppressed (unchanged since" in joined
        assert "0 delivered (inbox), 1 suppressed" in joined

    def test_added_card_redelivers(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        _drain("alpha")
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T1
        )

        fresh = poll_inbox("alpha", unseen_only=True)
        assert len(fresh) == 1 and "a2" in fresh[0]["body"]

    def test_removed_card_redelivers(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T0
        )
        _drain("alpha")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T1)
        assert len(poll_inbox("alpha", unseen_only=True)) == 1

    def test_touched_card_leaving_the_bucket_redelivers(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T0
        )
        _drain("alpha")
        # a2 is touched → it drops out of the stale bucket → the set CHANGED.
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _fresh_at("a2", "alpha", base=T1)], now=T1
        )
        assert len(poll_inbox("alpha", unseen_only=True)) == 1

    def test_owner_state_pruned_when_no_cards_left(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        sweep_and_nudge([_fresh_at("a1", "alpha", base=T1)], now=T1)
        assert "alpha" not in load_nudge_state()[KIND_STALE_ACTIVE]

    def test_floor_elapsed_redelivers_the_unchanged_set(self, monkeypatch):
        # A genuinely stuck owner must still be nudged once the floor elapses,
        # even though nothing about their card set changed.
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_NUDGE_FLOOR_HOURS, "24")
        tasks = [_stale_at("a1", "alpha")]

        sweep_and_nudge(tasks, now=T0)
        assert len(_drain("alpha")) == 1

        mid = T0 + _dt.timedelta(hours=6)
        sweep_and_nudge(tasks, now=mid)
        assert poll_inbox("alpha", unseen_only=True) == []  # still suppressed

        past = T0 + _dt.timedelta(hours=25)
        sweep_and_nudge(tasks, now=past)
        assert len(poll_inbox("alpha", unseen_only=True)) == 1  # floor elapsed

    def test_unseen_nudge_is_superseded_not_stacked(self, monkeypatch):
        # A drain-down owner must not accumulate a replay-storm: the newest
        # snapshot replaces the unseen predecessor (same rule as the digest).
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T1
        )

        pending = poll_inbox("alpha", unseen_only=True)
        assert len(pending) == 1 and "a2" in pending[0]["body"]

    def test_pending_backlog_is_suppressed_independently(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        tasks = [_stale_at("s1", "alpha"), _pending_at("p1", "alpha")]
        sweep_and_nudge(tasks, now=T0)
        _drain("alpha")
        # Only the stale-active set changes; the backlog set does not.
        sweep_and_nudge([*tasks, _stale_at("s2", "alpha")], now=T1)

        kinds = [r["event_type"] for r in poll_inbox("alpha", unseen_only=True)]
        assert kinds == [KIND_STALE_ACTIVE]


class TestFailSoftAndFailLoud:
    """A bad owner never aborts the batch — and never passes for delivered."""

    def test_one_owner_raising_does_not_starve_the_other(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        lines = sweep_and_nudge(
            [_stale("s1", "badowner"), _stale("s2", "goodowner")],
            enqueue=_failing_enqueue("badowner"),
        )
        joined = "\n".join(lines)

        # The healthy owner is DELIVERED (read back off the real inbox)…
        good = poll_inbox("goodowner", unseen_only=True)
        assert len(good) == 1 and "s2" in good[0]["body"]
        # …the bad owner got nothing, and it is LOUD, not a quiet 0.
        assert poll_inbox("badowner") == []
        assert "ERR" in joined and "badowner" in joined
        assert "1 delivered (inbox)" in joined and "1 failed" in joined

    def test_failed_enqueue_does_not_arm_suppression(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale_at("a1", "alpha")]
        sweep_and_nudge(tasks, now=T0, enqueue=_failing_enqueue("alpha"))
        # Nothing delivered → no suppression state → the next sweep RETRIES.
        assert "alpha" not in load_nudge_state()[KIND_STALE_ACTIVE]

        lines = sweep_and_nudge(tasks, now=T1)  # real enqueue this time
        joined = "\n".join(lines)
        assert "1 delivered (inbox), 0 suppressed" in joined
        assert len(poll_inbox("alpha", unseen_only=True)) == 1

    def test_every_owner_failing_screams(self, monkeypatch):
        # The exact shape of the shipped bug: detected > 0, delivered == 0.
        # It must be UNMISTAKABLE in the log, not a bland "0 sent".
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        lines = sweep_and_nudge(
            [_stale("a1", "alpha"), _stale("b1", "beta")],
            enqueue=_failing_enqueue("alpha", "beta"),
        )
        joined = "\n".join(lines)
        assert "!! ALERT stale-active" in joined
        assert "reached NOBODY" in joined
        assert "0 of 2 attempted" in joined

    def test_all_suppressed_is_not_an_alert(self, monkeypatch):
        # 0 delivered because everyone is SUPPRESSED is the healthy steady
        # state — it must NOT cry wolf.
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        tasks = [_stale("a1", "alpha")]
        sweep_and_nudge(tasks)
        joined = "\n".join(sweep_and_nudge(tasks))
        assert "ALERT" not in joined
        assert "1 suppressed" in joined

    def test_sweep_never_raises_on_a_broken_enqueue(self, monkeypatch):
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        lines = sweep_and_nudge(
            [_stale("s1", "alpha"), _pending("p1", "alpha")],
            enqueue=_failing_enqueue("alpha"),
        )
        # Both kinds still summarised — no kind aborted the other.
        assert any(ln.startswith("# stale-active:") for ln in lines)
        assert any(ln.startswith("# pending-backlog:") for ln in lines)


class TestOptionalPushEcho:
    """``_push`` survives ONLY as an opt-in, strictly-secondary echo."""

    def test_no_push_by_default(self, monkeypatch):
        # The default rail is the inbox. With no turn URL configured and no
        # dry-run, the sweep must still DELIVER (the old code reported
        # `reason=no-turn-url-configured` and delivered nothing).
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        lines = sweep_and_nudge([_stale("a1", "nourlowner")])
        joined = "\n".join(lines)

        assert len(poll_inbox("nourlowner", unseen_only=True)) == 1
        assert "1 delivered (inbox)" in joined
        assert "push echo" not in joined
        assert "no-turn-url-configured" not in joined

    def test_opt_in_echo_reaches_a_real_receiver_and_the_inbox(self, monkeypatch):
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_NUDGE_PUSH, "1")
        with _local_receiver() as (url, received):
            monkeypatch.setenv("SCITEX_TODO_TURN_URL_ECHOOWNER", url)
            lines = sweep_and_nudge([_stale("s1", "echoowner")])

        assert len(received) == 1                       # the echo went out…
        assert len(poll_inbox("echoowner")) == 1        # …AND the inbox landed
        assert "push echo ok=True" in "\n".join(lines)

    def test_a_broken_echo_never_fails_the_inbox_delivery(self, monkeypatch):
        # No silent fallback BETWEEN rails: a dead push receiver must not make
        # a landed inbox nudge look failed (nor arm/disarm the suppression).
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_NUDGE_PUSH, "1")
        # ``deliver`` slugs the agent name as UPPER, '-'→'_'; no scheme → raise.
        monkeypatch.setenv("SCITEX_TODO_TURN_URL_BADECHO", "noscheme-url")
        lines = sweep_and_nudge([_stale("s1", "badecho")])
        joined = "\n".join(lines)

        assert len(poll_inbox("badecho", unseen_only=True)) == 1
        assert "1 delivered (inbox)" in joined and "0 failed" in joined
        assert "push echo raised" in joined
        assert "badecho" in load_nudge_state()[KIND_STALE_ACTIVE]

# EOF
