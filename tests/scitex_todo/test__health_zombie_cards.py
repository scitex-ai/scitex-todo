#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card cannot be CLOSED and OPEN at the same time.

THE INVARIANT: a card carrying ``_log_meta.closed_at`` was closed. It must not
also sit in an open status (``deferred`` / ``in_progress`` / ``blocked`` /
``goal``). If it does, the close DID NOT STICK and the card is a ZOMBIE —
finished work that nags its owner in every digest, forever, invisible precisely
because it looks like ordinary backlog.

This is not hypothetical. On 2026-07-13 exactly two such cards were found on the
live board, and only by hand-scanning all 1,467 rows:

    selftest-card-20260701                              closed_at set, status=deferred
    todo-board-reads-stale-project-store-not-canonical  closed_at set, status=deferred

Both carried COMMENTS saying they had been moved to a terminal state. The prose
claimed the change; the FIELD never took it. They nagged the fleet for two days.

An invariant nobody runs is not an invariant — so it is a health check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_todo import _store
from scitex_todo._health import _check_terminal_state_honest


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.yaml"
    _store.add_task(path, id="live", title="ordinary open work", status="deferred", agent="a")
    _store.add_task(path, id="finished", title="really done", status="done", agent="a")
    return path


def _zombify(path: Path, task_id: str, status: str) -> None:
    """Stamp closed_at but leave the card in an OPEN status — the exact bug."""
    _store.update_task(path, task_id, status=status)
    doc = _store.load_tasks(path)
    for t in doc:
        if t.get("id") == task_id:
            t["_log_meta"] = {"closed_at": "2026-07-07T10:38:58Z", "closed_by": "someone"}
    from scitex_todo._model import save_tasks

    save_tasks(doc, path)


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------
def test_a_clean_store_passes(store):
    # Arrange / Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


def test_a_genuinely_done_card_is_not_a_zombie(store):
    # `done` + closed_at is the CORRECT shape. The check must not flag it — a
    # check that cries wolf on healthy data is one its reader learns to ignore.
    _zombify(store, "finished", "done")  # closed_at on a `done` card: legitimate
    # Act
    result = _check_terminal_state_honest(store)
    # Assert
    assert result["ok"] is True


def test_a_cancelled_card_is_not_a_zombie(store):
    _zombify(store, "finished", "cancelled")
    assert _check_terminal_state_honest(store)["ok"] is True


# --------------------------------------------------------------------------
# the bug it exists to catch — one test per open status, because each one is a
# distinct way the close can fail to stick
# --------------------------------------------------------------------------
def test_closed_at_on_a_DEFERRED_card_is_caught(store):
    # THE EXACT SHAPE of both real zombies found on 2026-07-13.
    _zombify(store, "live", "deferred")
    result = _check_terminal_state_honest(store)
    assert result["ok"] is False


def test_closed_at_on_an_IN_PROGRESS_card_is_caught(store):
    _zombify(store, "live", "in_progress")
    assert _check_terminal_state_honest(store)["ok"] is False


def test_closed_at_on_a_BLOCKED_card_is_caught(store):
    _zombify(store, "live", "blocked")
    assert _check_terminal_state_honest(store)["ok"] is False


def test_the_failure_NAMES_the_offending_card(store):
    # A check that says "something is wrong" without saying WHICH card sends its
    # reader hand-scanning 1,467 rows — which is exactly what I had to do.
    _zombify(store, "live", "deferred")
    result = _check_terminal_state_honest(store)
    assert "live" in result["detail"]


def test_the_hint_says_what_to_actually_DO(store):
    # Every failing check owes its reader the next step, not just a verdict.
    _zombify(store, "live", "deferred")
    result = _check_terminal_state_honest(store)
    assert "status" in result["hint"].lower()


# --------------------------------------------------------------------------
# it must never raise — a health check that explodes reports nothing
# --------------------------------------------------------------------------
def test_an_unreadable_store_is_REPORTED_not_raised(tmp_path: Path):
    # Arrange — a path that is not a readable store.
    missing = tmp_path / "nope" / "tasks.yaml"
    # Act — must not raise.
    result = _check_terminal_state_honest(missing)
    # Assert — it reports the problem instead of taking the whole doctor down.
    assert result["ok"] in (True, False)
    assert isinstance(result["detail"], str)
