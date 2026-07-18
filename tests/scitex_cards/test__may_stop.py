#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``may_stop`` — the never-stop detector: runnable work blocks a stop.

The cards half of the never-stop infrastructure (operator 2026-07-18:
「通知だけしてもエージェントが無視する。決定的に止めるにはフックしかない」;
card ``may-stop-hook-cards-runnable-work-20260718``). The verdict's exit
code is the contract sac's Stop hook keys on: 0 = the board is empty for
this agent, 2 = runnable work exists and stderr carries the hint list the
idle-at-prompt re-drive injects.

Real store files, AAA, no mocks. The CLI is driven through click's
CliRunner (real argv → real exit codes).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._may_stop import may_stop_cmd
from scitex_cards._inbox import enqueue, poll_inbox
from scitex_cards._may_stop import may_stop
from scitex_cards._store import add_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _drain(store, agent):
    """Ack the created-event notifications add_task itself enqueues."""
    poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)


# === the verdict ===========================================================


def test_empty_board_means_the_agent_may_stop(store):
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is False
    assert verdict["items"] == []
    assert verdict["idle_seconds"] is None


def test_an_in_progress_card_is_runnable_work(store):
    # Arrange
    add_task(store=store, id="w1", title="w1", status="in_progress", agent="worker-a")
    _drain(store, "worker-a")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is True
    assert [i["card_id"] for i in verdict["items"]] == ["w1"]
    assert verdict["idle_seconds"] is not None and verdict["idle_seconds"] >= 0


def test_a_blocked_card_with_a_named_gate_is_the_one_legitimate_wait(store):
    # Arrange: blocked WITH a named external gate — not runnable.
    add_task(
        store=store,
        id="w2",
        title="w2",
        status="blocked",
        blocker="dependency",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    # Act / Assert
    assert may_stop("worker-a", store)["runnable"] is False


def test_a_blocked_card_with_no_named_gate_is_runnable(store):
    # Arrange: blocker 'none' names nothing — the agent must act.
    add_task(
        store=store,
        id="w3",
        title="w3",
        status="blocked",
        blocker="none",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is True
    assert verdict["items"][0]["reason"] == "blocked with no named gate"


def test_a_deferred_card_whose_schedule_arrived_is_runnable(store):
    # Arrange: one past-scheduled, one future-scheduled.
    add_task(
        store=store,
        id="w4",
        title="w4",
        status="deferred",
        scheduled="2020-01-01",
        agent="worker-a",
    )
    add_task(
        store=store,
        id="w5",
        title="w5",
        status="deferred",
        scheduled="2099-01-01",
        agent="worker-a",
    )
    _drain(store, "worker-a")
    # Act
    verdict = may_stop("worker-a", store)
    # Assert: only the arrived schedule counts.
    assert [i["card_id"] for i in verdict["items"]] == ["w4"]


def test_unread_inbox_notifications_are_runnable_work(store):
    # Arrange
    enqueue(
        "worker-a",
        event_type="dm",
        card_id="dm:x",
        body="hello",
        actor="peer",
        store=store,
    )
    # Act
    verdict = may_stop("worker-a", store)
    # Assert
    assert verdict["runnable"] is True
    assert verdict["items"][0]["card_id"] == "(inbox)"


def test_other_agents_cards_do_not_bind_this_agent(store):
    # Arrange
    add_task(store=store, id="w6", title="w6", status="in_progress", agent="worker-b")
    # Act / Assert
    assert may_stop("worker-a", store)["runnable"] is False


# === the CLI contract (exit codes + hints) =================================


def test_cli_exit_zero_and_json_on_an_empty_board(store):
    # Act
    result = CliRunner().invoke(may_stop_cmd, ["--agent", "worker-a", "--tasks", store])
    # Assert
    assert result.exit_code == 0
    assert json.loads(result.output)["runnable"] is False


def test_cli_exit_two_with_numbered_stderr_hints_on_runnable_work(store):
    # Arrange
    add_task(store=store, id="w7", title="w7", status="in_progress", agent="worker-a")
    _drain(store, "worker-a")
    # Act
    result = CliRunner().invoke(may_stop_cmd, ["--agent", "worker-a", "--tasks", store])
    # Assert: exit 2; stdout = JSON verdict; stderr = the injectable hints.
    assert result.exit_code == 2
    assert json.loads(result.stdout)["runnable"] is True
    assert "1. w7" in result.stderr
    assert "in_progress card" in result.stderr


# EOF
