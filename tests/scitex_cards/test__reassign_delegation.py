#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delegation keeps responsibility — reassign auto-subscribes the delegator.

Operator directive 2026-07-18 (「渡しました、委任しましたで終わられると
困る」) mechanizing constitution §2 "ownership never dangles": handing a
card off must NOT end the delegator's accountability. Both reassign verbs
(single + bulk) keep the PREVIOUS owner and the card's CREATOR on
``subscribers`` through the handoff, so notify rules and late-nudges still
reach the people who delegated. Leaving the loop is an explicit
``set_subscriber`` remove, never a side effect of handing off.

Real store files, AAA, no mocks (STX-NM).
"""

from __future__ import annotations

import pytest

from scitex_cards._model import load_tasks
from scitex_cards._store import add_task, reassign_all, reassign_task


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def _card(store, task_id: str, owner: str, created_by: str) -> None:
    add_task(
        store=store,
        id=task_id,
        title=f"card {task_id}",
        status="in_progress",
        assignee=owner,
        agent=owner,
        created_by=created_by,
    )


def _by_id(store, task_id: str) -> dict:
    return next(t for t in load_tasks(store) if t.get("id") == task_id)


# === reassign_task (single) ================================================


def test_reassign_subscribes_previous_owner_and_creator(store):
    # Arrange: alice owns a card that carol created.
    _card(store, "t1", owner="alice", created_by="carol")

    # Act: alice hands it to bob.
    reassign_task(store, "t1", "bob", by="alice")

    # Assert: both the delegator and the creator stay in the loop.
    subs = _by_id(store, "t1").get("subscribers") or []
    assert "alice" in subs and "carol" in subs
    assert "bob" not in subs  # the new owner is notified as OWNER, not twice


def test_reassign_never_duplicates_an_existing_subscriber(store):
    # Arrange: alice is already subscribed (e.g. from an earlier handoff).
    _card(store, "t2", owner="alice", created_by="alice")
    reassign_task(store, "t2", "bob", by="alice")
    assert (_by_id(store, "t2").get("subscribers") or []).count("alice") == 1

    # Act: the card comes BACK to alice, then leaves again.
    reassign_task(store, "t2", "alice", by="bob")
    reassign_task(store, "t2", "carol", by="alice")

    # Assert: each keeper appears exactly once despite repeated handoffs.
    subs = _by_id(store, "t2").get("subscribers") or []
    assert subs.count("alice") == 1
    assert subs.count("bob") == 1


def test_same_owner_noop_adds_no_subscribers(store):
    # Arrange
    _card(store, "t3", owner="alice", created_by="alice")

    # Act: reassigning to the current owner is the documented no-op.
    result = reassign_task(store, "t3", "alice", by="alice")

    # Assert: no write, no subscriber side effects.
    assert result["changed"] is False
    assert _by_id(store, "t3").get("subscribers") in (None, [])


# === reassign_all (bulk) ===================================================


def test_bulk_reassign_subscribes_delegator_on_every_moved_card(store):
    # Arrange: alice owns two cards, one created by carol.
    _card(store, "b1", owner="alice", created_by="alice")
    _card(store, "b2", owner="alice", created_by="carol")

    # Act: the whole cohort moves to bob.
    result = reassign_all(store, "alice", "bob", by="alice")

    # Assert: every moved card keeps its delegation chain.
    assert result["count"] == 2
    for tid, expected in (("b1", {"alice"}), ("b2", {"alice", "carol"})):
        subs = set(_by_id(store, tid).get("subscribers") or [])
        assert expected <= subs
        assert "bob" not in subs


# EOF
