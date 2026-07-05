#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the pure subscriber-invariant helpers (no I/O)."""

from __future__ import annotations

from scitex_todo._subscribers import (
    ensure_assignee_subscribed,
    is_mandatory_subscriber,
    owner_of,
    seed_subscribers,
)


# --------------------------------------------------------------------------- #
# owner_of                                                                    #
# --------------------------------------------------------------------------- #
def test_owner_of_prefers_agent_then_assignee():
    assert owner_of({"agent": "u_a", "assignee": "u_b"}) == "u_a"
    assert owner_of({"assignee": "u_b"}) == "u_b"
    assert owner_of({}) == ""
    assert owner_of({"agent": "  u_x  "}) == "u_x"


# --------------------------------------------------------------------------- #
# seed_subscribers                                                            #
# --------------------------------------------------------------------------- #
def test_seed_includes_owner_and_creator_deduped_order_stable():
    assert seed_subscribers(owner="u_owner", creator="u_creator") == [
        "u_owner",
        "u_creator",
    ]


def test_seed_dedupes_when_owner_is_creator():
    assert seed_subscribers(owner="u_x", creator="u_x") == ["u_x"]


def test_seed_unions_explicit_existing_first():
    assert seed_subscribers(
        owner="u_owner", creator="u_creator", existing=["u_sub1", "u_owner"]
    ) == ["u_sub1", "u_owner", "u_creator"]


def test_seed_skips_empty_roles():
    assert seed_subscribers(owner="", creator="u_c") == ["u_c"]
    assert seed_subscribers(owner="u_o", creator="") == ["u_o"]


# --------------------------------------------------------------------------- #
# ensure_assignee_subscribed                                                  #
# --------------------------------------------------------------------------- #
def test_ensure_adds_missing_owner():
    card = {"agent": "u_owner", "subscribers": ["u_other"]}
    ensure_assignee_subscribed(card)
    assert card["subscribers"] == ["u_other", "u_owner"]


def test_ensure_is_noop_when_owner_present():
    card = {"agent": "u_owner", "subscribers": ["u_owner", "u_other"]}
    ensure_assignee_subscribed(card)
    assert card["subscribers"] == ["u_owner", "u_other"]


def test_ensure_seeds_from_empty():
    card = {"assignee": "u_owner"}
    ensure_assignee_subscribed(card)
    assert card["subscribers"] == ["u_owner"]


def test_ensure_noop_when_no_owner():
    card = {"subscribers": ["u_x"]}
    ensure_assignee_subscribed(card)
    assert card["subscribers"] == ["u_x"]


# --------------------------------------------------------------------------- #
# is_mandatory_subscriber                                                     #
# --------------------------------------------------------------------------- #
def test_owner_is_mandatory_others_are_not():
    card = {"agent": "u_owner", "created_by": "u_creator"}
    assert is_mandatory_subscriber(card, "u_owner") is True
    assert is_mandatory_subscriber(card, "u_creator") is False
    assert is_mandatory_subscriber(card, "u_random") is False
    assert is_mandatory_subscriber(card, "") is False


# EOF
