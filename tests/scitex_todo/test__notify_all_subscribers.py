#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every card mutation notifies ALL subscribers (operator 2026-07-06).

Real store + real inbox round-trips (STX-NM: no mocks). Asserts that the
card-mutation events (created / reassigned / status_changed / commented /
completed) all reach the card's full subscriber set, and that the assignee is
ALWAYS resolved into the subscribers set even when the persisted list omits it
(legacy / not-yet-backfilled card).
"""

from __future__ import annotations

from scitex_todo._events import Event, EventType
from scitex_todo._notify import resolve_recipients
from scitex_todo._notify._rules import (
    DEFAULT_NOTIFY_RULES,
    ROLE_SUBSCRIBERS,
)
from scitex_todo._notify._resolver import card_role_members
from scitex_todo._store import add_task


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


# --------------------------------------------------------------------------- #
# rules: every card-mutation event includes ROLE_SUBSCRIBERS                  #
# --------------------------------------------------------------------------- #
def test_every_card_mutation_event_includes_subscribers():
    card_mutations = [
        EventType.CREATED,
        EventType.REASSIGNED,
        EventType.STATUS_CHANGED,
        EventType.COMMENTED,
        EventType.COMPLETED,
    ]
    for et in card_mutations:
        assert ROLE_SUBSCRIBERS in DEFAULT_NOTIFY_RULES[et], (
            f"{et} must notify subscribers"
        )


# --------------------------------------------------------------------------- #
# resolver: the assignee is ALWAYS in the resolved subscribers set            #
# --------------------------------------------------------------------------- #
def test_assignee_injected_into_subscribers_even_when_persisted_list_omits_it():
    # A legacy card whose subscribers list does NOT contain the owner.
    card = {"agent": "u_owner", "subscribers": ["u_watcher"]}
    roles = card_role_members(card)
    assert roles[ROLE_SUBSCRIBERS] == {"u_owner", "u_watcher"}


def test_subscribers_role_has_owner_even_with_no_subscribers_field():
    card = {"agent": "u_owner"}
    roles = card_role_members(card)
    assert roles[ROLE_SUBSCRIBERS] == {"u_owner"}


# --------------------------------------------------------------------------- #
# end-to-end: resolve_recipients for each mutation reaches the subscribers    #
# --------------------------------------------------------------------------- #
def test_reassigned_reaches_all_subscribers(tmp_path):
    store = _store(tmp_path)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="u_owner",
        subscribers=["u_sub"],
        created_by="u_creator",
    )
    # A reassign event: subscribers (u_sub) + owner (u_owner) all resolve in;
    # the actor is dropped by the dispatcher, not here.
    recipients = resolve_recipients(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="u_owner"),
        {"agent": "u_owner", "subscribers": ["u_sub"], "created_by": "u_creator"},
        store=store,
    )
    assert "u_sub" in recipients
    assert "u_owner" in recipients


def test_created_reaches_subscribers(tmp_path):
    store = _store(tmp_path)
    card = {"agent": "u_owner", "subscribers": ["u_sub"], "created_by": "u_creator"}
    recipients = resolve_recipients(
        Event(type=EventType.CREATED, card_id="c1", actor="u_creator"),
        card,
        store=store,
    )
    # created now notifies the assignee + subscribers.
    assert "u_sub" in recipients
    assert "u_owner" in recipients


# EOF
