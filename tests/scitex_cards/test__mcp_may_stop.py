#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``may_stop`` over MCP — "do I still have runnable work?", asked directly.

WHY IT EXISTS (operator, 2026-07-18): 「今の MCP もあって欲しいんですけど」. The
Stop hook is one CONSUMER of this question; the question itself is the asset,
and an agent should be able to ask it without going through a shell hook.

Nothing here is Claude-Code-specific and that is the point — coupling the verb
to one runtime's hook contract would make our output that runtime's public API.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastmcp", reason="MCP tools require the [mcp] extra")

from scitex_cards._inbox import poll_inbox  # noqa: E402
from scitex_cards._store import add_task  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _call(store, agent="worker-x"):
    from scitex_cards._mcp_server import may_stop

    return json.loads(asyncio.run(may_stop(agent=agent, tasks_path=store)))


def _drain(store, agent):
    """Ack the created-events add_task enqueues, so they are not counted."""
    poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)


def test_an_empty_board_reports_no_runnable_work(store):
    # Act
    verdict = _call(store)
    # Assert
    assert verdict["runnable"] is False
    assert verdict["items"] == []


def test_open_work_is_reported_with_the_next_action(store):
    """The verdict must carry WHAT to do, not merely that something exists."""
    # Arrange
    add_task(store=store, id="w1", title="w1", status="in_progress", agent="worker-x")
    _drain(store, "worker-x")

    # Act
    verdict = _call(store)

    # Assert
    assert verdict["runnable"] is True
    assert [it["card_id"] for it in verdict["items"]] == ["w1"]
    assert verdict["items"][0]["next_action"]


def test_another_agents_work_is_not_mine(store):
    # Arrange
    add_task(store=store, id="w2", title="w2", status="in_progress", agent="worker-y")
    _drain(store, "worker-y")

    # Act / Assert
    assert _call(store, agent="worker-x")["runnable"] is False


def test_a_blocked_card_naming_its_gate_does_not_hold_you(store):
    """RECONCILING is the honest way out: a named gate means someone else owns it.

    This is the distinction that makes the never-stop rule livable — it does
    not demand you finish what you cannot start, it demands the board tell the
    truth about why.
    """
    # Arrange
    add_task(
        store=store,
        id="w3",
        title="w3",
        status="blocked",
        blocker="operator-decision",
        agent="worker-x",
    )
    _drain(store, "worker-x")

    # Act / Assert
    assert _call(store)["runnable"] is False


def test_a_blocked_card_with_no_gate_still_holds_you(store):
    """ "Blocked" with nothing named is an unstated excuse, and counts as work."""
    # Arrange
    add_task(store=store, id="w4", title="w4", status="blocked", agent="worker-x")
    _drain(store, "worker-x")

    # Act / Assert
    assert _call(store)["runnable"] is True


# EOF
