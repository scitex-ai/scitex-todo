#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards stop-hook`` — refuses a stop while the board holds work.

The cards half of the never-stop-when-task-remains mechanism (ADR-0012). The
runtime registers this into ``.claude/settings.json`` and knows nothing about
its output; cards owns both ends of the contract, so the format below is the
whole public surface.

CONTRACT: stdout is Claude Code Stop-hook JSON.
``{"decision": "block", "reason": ...}`` refuses the stop and feeds ``reason``
back as the agent's NEXT INSTRUCTION. ``{}`` allows it.

Pinned here, with real store files and no mocks:
* work remains → block, and the reason NAMES the cards and says what to do;
* board empty → allow;
* detector failure → ALLOW (fail-open), because an agent wedged by our own bug
  is worse than one that stopped early;
* another agent's work does not block this agent;
* the reason stays bounded when the board is large — an instruction listing
  forty cards is not an instruction.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._stop_hook import stop_hook_cmd
from scitex_cards._inbox import poll_inbox
from scitex_cards._store import add_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _run(store, agent="worker-x"):
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", agent, "--tasks", store])
    return result, json.loads(result.stdout)


def _drain(store, agent):
    """Ack the created-events add_task enqueues, so they don't count as work."""
    poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)


def test_an_empty_board_allows_the_stop(store):
    # Act
    result, payload = _run(store)
    # Assert — {} is "allow"; anything else would wedge an idle agent.
    assert payload == {}
    assert result.exit_code == 0


def test_runnable_work_blocks_the_stop(store):
    # Arrange
    add_task(store=store, id="w1", title="w1", status="in_progress", agent="worker-x")
    _drain(store, "worker-x")

    # Act
    result, payload = _run(store)

    # Assert
    assert payload["decision"] == "block"
    assert result.exit_code == 0  # the hook succeeded; the STOP was refused


def test_the_reason_names_the_card_and_says_what_to_do(store):
    """A refusal that does not say what to do next leaves the agent idle."""
    # Arrange
    add_task(store=store, id="w1", title="w1", status="in_progress", agent="worker-x")
    _drain(store, "worker-x")

    # Act
    _, payload = _run(store)
    reason = payload["reason"]

    # Assert — the card id, an action, and an explicit single-item instruction.
    assert "w1" in reason
    assert "work it, update it, or close it" in reason
    assert "Pick ONE" in reason


def test_a_detector_failure_allows_the_stop(store):
    """FAIL-OPEN. Our bug must never be the reason an agent cannot finish."""
    # Act — a store path that cannot be read at all.
    result = CliRunner().invoke(
        stop_hook_cmd, ["--agent", "worker-x", "--tasks", "/nonexistent/none.yaml"]
    )

    # Assert
    assert json.loads(result.stdout) == {}
    assert result.exit_code == 0


def test_another_agents_work_does_not_block_this_agent(store):
    # Arrange
    add_task(store=store, id="w2", title="w2", status="in_progress", agent="worker-y")
    _drain(store, "worker-y")

    # Act / Assert
    _, payload = _run(store, agent="worker-x")
    assert payload == {}


def test_the_reason_stays_bounded_on_a_large_board(store):
    """An instruction listing forty cards is not an instruction."""
    # Arrange
    for i in range(12):
        add_task(
            store=store,
            id=f"c{i}",
            title=f"c{i}",
            status="in_progress",
            agent="worker-x",
        )
    _drain(store, "worker-x")

    # Act
    _, payload = _run(store)
    reason = payload["reason"]

    # Assert — capped list, and the remainder is COUNTED rather than dropped
    # silently (an omission the agent cannot see is a lie about the board).
    numbered = [
        ln for ln in reason.splitlines() if ln.strip()[:2].rstrip(".").isdigit()
    ]
    assert len(numbered) <= 6
    assert "more runnable item(s)" in reason
    assert "12 runnable item(s)" in reason


# EOF
