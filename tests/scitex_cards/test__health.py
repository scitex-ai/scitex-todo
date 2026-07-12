#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the package-level health doctor (:func:`scitex_cards._health.health`).

Real, hermetic round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path``
YAML store, real :mod:`scitex_cards._inbox` enqueues, and explicit ``store`` /
``agent_id`` params so nothing depends on the process environment. Sync tests
(the repo has no pytest-asyncio) — the health function is pure and synchronous.
"""

from __future__ import annotations

from scitex_cards import _inbox
from scitex_cards._health import UNSEEN_BACKLOG_THRESHOLD, health


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _healthy_store(tmp_path):
    """Write a real, minimal-but-valid task store and return its path."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    return store


def _check(report, name):
    """Return the check record with ``name`` (or raise KeyError)."""
    for c in report["checks"]:
        if c["name"] == name:
            return c
    raise KeyError(name)


# --------------------------------------------------------------------------- #
# output shape — the cross-package standard                                   #
# --------------------------------------------------------------------------- #
def test_output_shape_is_standard(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")
    # Top-level keys.
    assert set(report) == {"package", "ok", "checks", "summary"}
    assert report["package"] == "scitex-cards"
    assert isinstance(report["ok"], bool)
    assert isinstance(report["summary"], str) and report["summary"]
    assert isinstance(report["checks"], list) and report["checks"]
    # Every check record has the exact 4 fields with the right types.
    for c in report["checks"]:
        assert set(c) == {"name", "ok", "detail", "hint"}
        assert isinstance(c["name"], str) and c["name"]
        assert isinstance(c["ok"], bool)
        assert isinstance(c["detail"], str)
        assert c["hint"] is None or isinstance(c["hint"], str)


def test_expected_checks_present(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")
    names = {c["name"] for c in report["checks"]}
    assert {
        "store_canonical",
        "agent_id",
        "notifyd_alive",
        "channel_drain",
        "channel_capable",
    } <= names


def test_ok_is_true_iff_every_check_ok(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")
    assert report["ok"] == all(c["ok"] for c in report["checks"])


def test_every_failing_check_carries_a_hint(tmp_path):
    """The contract: no silent fail — a failing check ALWAYS has a real hint."""
    # A deliberately unhealthy call (bad agent id, nonexistent store) to
    # provoke failures, then assert the invariant over whatever failed.
    report = health(store=tmp_path / "nope.yaml", agent_id="unknown")
    failing = [c for c in report["checks"] if not c["ok"]]
    assert failing, "expected at least one failing check in this scenario"
    for c in failing:
        assert c["hint"], f"failing check {c['name']!r} has no actionable hint"


# --------------------------------------------------------------------------- #
# store_canonical                                                             #
# --------------------------------------------------------------------------- #
def test_store_canonical_ok_for_healthy_tmp_store(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")
    c = _check(report, "store_canonical")
    assert c["ok"] is True
    assert c["hint"] is None


def test_store_canonical_fails_when_store_missing(tmp_path):
    report = health(store=tmp_path / "absent.yaml", agent_id="agent-x")
    c = _check(report, "store_canonical")
    assert c["ok"] is False
    assert c["hint"]


def test_store_canonical_fails_without_tasks_key(tmp_path):
    store = tmp_path / "tasks.yaml"
    store.write_text("users: {}\n", encoding="utf-8")  # valid YAML, no `tasks:`
    report = health(store=store, agent_id="agent-x")
    c = _check(report, "store_canonical")
    assert c["ok"] is False
    assert "tasks" in c["hint"]


# --------------------------------------------------------------------------- #
# agent_id                                                                    #
# --------------------------------------------------------------------------- #
def test_agent_id_ok_for_real_value(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="real-agent")
    c = _check(report, "agent_id")
    assert c["ok"] is True
    assert "real-agent" in c["detail"]


def test_agent_id_fails_on_unknown_sentinel(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="unknown")
    c = _check(report, "agent_id")
    assert c["ok"] is False
    assert c["hint"] and "SCITEX_TODO_AGENT_ID" in c["hint"]


def test_agent_id_fails_on_unexpanded_placeholder(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="${SCITEX_TODO_AGENT_ID}")
    c = _check(report, "agent_id")
    assert c["ok"] is False
    assert c["hint"]


# --------------------------------------------------------------------------- #
# channel_drain                                                               #
# --------------------------------------------------------------------------- #
def test_channel_drain_ok_for_small_backlog(tmp_path):
    store = _healthy_store(tmp_path)
    agent = "agent-x"
    for i in range(3):
        _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="bob",
            ts=f"2026-06-28T10:{i:02d}:00Z",
            store=store,
        )
    report = health(store=store, agent_id=agent)
    c = _check(report, "channel_drain")
    assert c["ok"] is True
    assert c["hint"] is None


def test_channel_drain_fails_on_large_unseen_backlog_with_no_seen(tmp_path):
    store = _healthy_store(tmp_path)
    agent = "agent-x"
    # Enqueue a backlog strictly above the threshold, all unseen (seen == 0).
    for i in range(UNSEEN_BACKLOG_THRESHOLD + 1):
        _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="bob",
            ts=f"2026-06-28T10:00:{i:02d}Z" if i < 60 else f"2026-06-28T11:{i - 60:02d}:00Z",
            store=store,
        )
    report = health(store=store, agent_id=agent)
    c = _check(report, "channel_drain")
    assert c["ok"] is False
    assert c["hint"] and "not draining" in c["hint"]
    assert "mcp start" in c["hint"]


def test_channel_drain_ok_when_some_seen_even_if_unseen_large(tmp_path):
    store = _healthy_store(tmp_path)
    agent = "agent-x"
    for i in range(UNSEEN_BACKLOG_THRESHOLD + 5):
        _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="bob",
            ts=f"2026-06-28T{10 + i // 60:02d}:{i % 60:02d}:00Z",
            store=store,
        )
    # Drain (mark seen) the first batch so seen > 0 — a busy-but-working inbox.
    _inbox.poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)
    # Now add a fresh unseen backlog on top; seen > 0 keeps it healthy.
    for i in range(UNSEEN_BACKLOG_THRESHOLD + 5):
        _inbox.enqueue(
            agent,
            event_type="completed",
            card_id=f"d{i}",
            body=f"new {i}",
            actor="alice",
            ts=f"2026-06-29T{10 + i // 60:02d}:{i % 60:02d}:00Z",
            store=store,
        )
    report = health(store=store, agent_id=agent)
    c = _check(report, "channel_drain")
    assert c["ok"] is True


# --------------------------------------------------------------------------- #
# channel_capable                                                             #
# --------------------------------------------------------------------------- #
def test_channel_capable_ok(tmp_path):
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")
    c = _check(report, "channel_capable")
    assert c["ok"] is True
    assert c["hint"] is None


# --------------------------------------------------------------------------- #
# never raises                                                                #
# --------------------------------------------------------------------------- #
def test_health_never_raises_on_bad_inputs(tmp_path):
    # Nonexistent store + bad agent id must still return a well-formed report,
    # never propagate an exception.
    report = health(store=tmp_path / "does-not-exist.yaml", agent_id="")
    assert report["package"] == "scitex-cards"
    assert isinstance(report["ok"], bool)
    assert report["checks"]


# EOF
