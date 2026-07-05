#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Store-level tests for the subscriber invariant (real store, no mocks).

Exercises add_task / reassign_task / set_subscriber against a real tmp_path
store, asserting the operator's asymmetric rule (2026-07-06): the assignee is
a mandatory subscriber (seeded, re-added on reassign, un-removable); the
creator is a default subscriber that CAN be removed.
"""

from __future__ import annotations

import pytest

from scitex_todo._model import TaskValidationError
from scitex_todo._store import (
    add_task,
    get_task,
    reassign_task,
    set_subscriber,
)


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _add(store, *, assignee, created_by, tid="c1", **kw):
    return add_task(
        store=store, id=tid, title="t", assignee=assignee, created_by=created_by, **kw
    )


# --------------------------------------------------------------------------- #
# add_task seeds subscribers = {assignee, creator}                            #
# --------------------------------------------------------------------------- #
def test_add_task_seeds_assignee_and_creator(tmp_path):
    store = _store(tmp_path)
    t = _add(store, assignee="u_owner", created_by="u_creator")
    assert t["subscribers"] == ["u_owner", "u_creator"]


def test_add_task_dedupes_when_creator_is_assignee(tmp_path):
    store = _store(tmp_path)
    t = _add(store, assignee="u_x", created_by="u_x")
    assert t["subscribers"] == ["u_x"]


def test_add_task_unions_explicit_subscribers(tmp_path):
    store = _store(tmp_path)
    t = _add(
        store, assignee="u_owner", created_by="u_creator", subscribers=["u_watch"]
    )
    assert t["subscribers"] == ["u_watch", "u_owner", "u_creator"]


# --------------------------------------------------------------------------- #
# reassign adds the new assignee as a mandatory subscriber                    #
# --------------------------------------------------------------------------- #
def test_reassign_adds_new_owner_to_subscribers(tmp_path):
    store = _store(tmp_path)
    _add(store, assignee="u_owner", created_by="u_creator")
    reassign_task(store=store, task_id="c1", new_owner="u_new", by="u_creator")
    card = get_task(store=store, task_id="c1")
    # New owner added; old owner + creator remain (append-only).
    assert "u_new" in card["subscribers"]
    assert "u_owner" in card["subscribers"]
    assert "u_creator" in card["subscribers"]
    assert card["subscribers"][-1] == "u_new"  # appended last


# --------------------------------------------------------------------------- #
# assignee cannot unsubscribe; creator can                                    #
# --------------------------------------------------------------------------- #
def test_cannot_remove_the_current_assignee(tmp_path):
    store = _store(tmp_path)
    _add(store, assignee="u_owner", created_by="u_creator")
    with pytest.raises(TaskValidationError, match="MANDATORY"):
        set_subscriber(store=store, task_id="c1", who="u_owner", action="remove")
    # Still subscribed.
    card = get_task(store=store, task_id="c1")
    assert "u_owner" in card["subscribers"]


def test_creator_can_unsubscribe(tmp_path):
    store = _store(tmp_path)
    _add(store, assignee="u_owner", created_by="u_creator")
    set_subscriber(store=store, task_id="c1", who="u_creator", action="remove")
    card = get_task(store=store, task_id="c1")
    assert "u_creator" not in card["subscribers"]
    assert "u_owner" in card["subscribers"]  # assignee untouched


def test_can_remove_a_plain_subscriber(tmp_path):
    store = _store(tmp_path)
    _add(store, assignee="u_owner", created_by="u_creator", subscribers=["u_watch"])
    set_subscriber(store=store, task_id="c1", who="u_watch", action="remove")
    card = get_task(store=store, task_id="c1")
    assert "u_watch" not in card["subscribers"]


def test_old_assignee_becomes_removable_after_reassign(tmp_path):
    """Once reassigned away, the OLD owner is no longer mandatory → removable."""
    store = _store(tmp_path)
    _add(store, assignee="u_owner", created_by="u_creator")
    reassign_task(store=store, task_id="c1", new_owner="u_new", by="u_creator")
    # u_owner is no longer the assignee → can now unsubscribe.
    set_subscriber(store=store, task_id="c1", who="u_owner", action="remove")
    card = get_task(store=store, task_id="c1")
    assert "u_owner" not in card["subscribers"]
    # But the NEW owner still cannot.
    with pytest.raises(TaskValidationError, match="MANDATORY"):
        set_subscriber(store=store, task_id="c1", who="u_new", action="remove")


# EOF
