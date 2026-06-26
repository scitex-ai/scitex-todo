#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owner-SSOT + fail-loud owner/creator resolution (operator mandate 2026-06-26).

Covers the three deliverables that have NO mocks anywhere (PA-306 / STX-NM):

* :func:`scitex_todo._owner.card_owner` — the single owner rule (``agent``
  falling back to ``assignee``, ``None`` for neither).
* :func:`scitex_todo._store.add_task` FAIL-LOUD — raises (with an actionable
  hint) when the assignee/owner is missing OR the creator is unresolvable;
  succeeds with both and keeps ``agent`` == ``assignee`` in lock-step + stamps
  a real ``created_by``.
* :func:`scitex_todo._django.handlers._comment_relay.maybe_relay_comment`
  FAIL-LOUD — an owner-less card returns the loud ``error:no-owner`` result
  instead of the old silent ``skip:no-agent`` no-op; an assignee-only card
  resolves its owner via the SSOT (proven via the self-comment skip, which
  fires only when the resolved owner == the author — no network).

The store tests drive the env directly via the ``env`` fixture (the
PA-306-compliant monkeypatch replacement from ``conftest.py``), never a mock.
"""

from __future__ import annotations

import pytest

from scitex_todo import _store
from scitex_todo._model import TaskValidationError
from scitex_todo._owner import card_owner


# --------------------------------------------------------------------------- #
# card_owner — the owner SSOT                                                 #
# --------------------------------------------------------------------------- #
def test_card_owner_prefers_agent():
    assert card_owner({"agent": "alice", "assignee": "bob"}) == "alice"


def test_card_owner_falls_back_to_assignee():
    assert card_owner({"assignee": "bob"}) == "bob"


def test_card_owner_none_for_neither():
    assert card_owner({"id": "x", "title": "t"}) is None


def test_card_owner_strips_and_treats_blank_as_none():
    assert card_owner({"agent": "  ", "assignee": "  "}) is None
    assert card_owner({"agent": "  alice  "}) == "alice"


def test_card_owner_non_mapping_is_none():
    assert card_owner(None) is None
    assert card_owner("not-a-card") is None


# --------------------------------------------------------------------------- #
# add_task — FAIL LOUD on missing owner / unresolvable creator                #
# --------------------------------------------------------------------------- #
def test_add_task_raises_when_owner_missing(tmp_path, env):
    # A resolvable creator is set (suite default), but NO assignee/agent.
    env.set("SCITEX_TODO_AGENT", "agent:creator")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A")
    assert "assignee is required" in str(exc.value)


def test_add_task_raises_when_creator_unresolvable(tmp_path, env):
    # Owner IS supplied, but the creator cannot be resolved (no env, no arg).
    env.delete("SCITEX_TODO_AGENT")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert "creator unresolved" in str(exc.value)


def test_add_task_creator_unknown_sentinel_also_raises(tmp_path, env):
    # The "unknown" placeholder is NOT a real creator — fail loud, no silent
    # stamping of "unknown".
    env.set("SCITEX_TODO_AGENT", "unknown")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert "creator unresolved" in str(exc.value)


def test_add_task_succeeds_and_stamps_created_by(tmp_path, env):
    env.set("SCITEX_TODO_AGENT", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert inserted["created_by"] == "agent:creator"


def test_add_task_explicit_created_by_wins(tmp_path, env):
    env.delete("SCITEX_TODO_AGENT")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store, id="a", title="A", assignee="agent:owner", created_by="agent:explicit"
    )
    assert inserted["created_by"] == "agent:explicit"


def test_add_task_lockstep_assignee_only_sets_agent(tmp_path, env):
    # assignee-only call → agent stamped to the SAME owner (lock-step) so the
    # board/relay/notify never see an owner-less or half-owned card.
    env.set("SCITEX_TODO_AGENT", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert inserted["assignee"] == "agent:owner"
    assert inserted["agent"] == "agent:owner"
    assert card_owner(inserted) == "agent:owner"


def test_add_task_lockstep_agent_only_sets_assignee(tmp_path, env):
    # agent-only call → assignee stamped to the SAME owner (lock-step).
    env.set("SCITEX_TODO_AGENT", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", agent="agent:owner")
    assert inserted["agent"] == "agent:owner"
    assert inserted["assignee"] == "agent:owner"


# --------------------------------------------------------------------------- #
# comment relay — FAIL LOUD on no owner; SSOT targeting                        #
# --------------------------------------------------------------------------- #
def _relay():
    from scitex_todo._django.handlers._comment_relay import maybe_relay_comment

    return maybe_relay_comment


def test_relay_no_owner_returns_loud_error():
    # The card has NEITHER agent NOR assignee — the comment reached nobody.
    # The OLD behaviour silently returned skip:no-agent; the NEW behaviour
    # returns a LOUD error result the board JS toasts. No network involved
    # (the function returns before any deliver()).
    result = _relay()(
        {"id": "orphan", "title": "x"},
        {"author": "operator", "text": "ping"},
    )
    assert result["sent"] is False
    assert result["wire"] == "error:no-owner"
    assert "reached nobody" in result["reason"]
    assert result["target"] == ""


def test_relay_targets_assignee_when_no_agent():
    # An assignee-only card resolves its owner via card_owner. Make the
    # comment author == that owner so the self-comment branch fires — proving
    # the relay TARGET resolved to the assignee (no network, no deliver()).
    result = _relay()(
        {"id": "c1", "title": "x", "assignee": "bob"},
        {"author": "bob", "text": "self note"},
    )
    assert result["wire"] == "skip:self-comment"
    assert result["target"] == "bob"


def test_relay_self_comment_skips_for_agent_owner():
    result = _relay()(
        {"id": "c1", "title": "x", "agent": "alice"},
        {"author": "alice", "text": "self note"},
    )
    assert result["wire"] == "skip:self-comment"
    assert result["target"] == "alice"

# EOF
