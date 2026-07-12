#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owner-SSOT + fail-loud owner/creator resolution (operator mandate 2026-06-26).

Covers the three deliverables that have NO mocks anywhere (PA-306 / STX-NM):

* :func:`scitex_cards._owner.card_owner` — the single owner rule (``agent``
  falling back to ``assignee``, ``None`` for neither).
* :func:`scitex_cards._store.add_task` FAIL-LOUD — raises (with an actionable
  hint) when the assignee/owner is missing OR the creator is unresolvable;
  succeeds with both and keeps ``agent`` == ``assignee`` in lock-step + stamps
  a real ``created_by``.
* :func:`scitex_cards._django.handlers._comment_relay.comment_inbox_toast`
  — the comment toast now reflects the standalone INBOX QUEUE (the
  recipient names the ``commented`` notification was enqueued to) rather
  than the old direct-POST result; ``target`` resolves via the owner SSOT
  (``agent`` falling back to ``assignee``), and the author (actor) is never
  in ``queued``. No network.

The store tests drive the env directly via the ``env`` fixture (the
PA-306-compliant monkeypatch replacement from ``conftest.py``), never a mock.
"""

from __future__ import annotations

import pytest

from scitex_cards import _store
from scitex_cards._model import TaskValidationError
from scitex_cards._owner import card_owner


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
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A")
    assert "assignee is required" in str(exc.value)


def test_add_task_raises_when_creator_unresolvable(tmp_path, env):
    # Owner IS supplied, but the creator cannot be resolved (no env, no arg).
    env.delete("SCITEX_TODO_AGENT_ID")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert "creator unresolved" in str(exc.value)


def test_add_task_creator_unknown_sentinel_also_raises(tmp_path, env):
    # The "unknown" placeholder is NOT a real creator — fail loud, no silent
    # stamping of "unknown".
    env.set("SCITEX_TODO_AGENT_ID", "unknown")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert "creator unresolved" in str(exc.value)


def test_add_task_succeeds_and_stamps_created_by(tmp_path, env):
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert inserted["created_by"] == "agent:creator"


def test_add_task_deprecated_agent_env_fails_loud(tmp_path, env):
    """The renamed-away $SCITEX_TODO_AGENT must never be silently honoured: if
    it is still exported, creator resolution fails LOUD pointing at the new
    name so a stale export can't quietly mis-attribute a card's creator."""
    # Arrange: a valid NEW-name creator is present AND the deprecated old name
    # is set — the guard must still fire (never silently prefers the new one).
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    env.set("SCITEX_TODO_AGENT", "legacy-agent")
    store = tmp_path / "tasks.yaml"
    # Act / Assert
    with pytest.raises(RuntimeError, match="SCITEX_TODO_AGENT_ID"):
        _store.add_task(store, id="a", title="A", assignee="agent:owner")


def test_add_task_explicit_created_by_wins(tmp_path, env):
    env.delete("SCITEX_TODO_AGENT_ID")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store, id="a", title="A", assignee="agent:owner", created_by="agent:explicit"
    )
    assert inserted["created_by"] == "agent:explicit"


def test_add_task_lockstep_assignee_only_sets_agent(tmp_path, env):
    # assignee-only call → agent stamped to the SAME owner (lock-step) so the
    # board/relay/notify never see an owner-less or half-owned card.
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:owner")
    assert inserted["assignee"] == "agent:owner"
    assert inserted["agent"] == "agent:owner"
    assert card_owner(inserted) == "agent:owner"


def test_add_task_lockstep_agent_only_sets_assignee(tmp_path, env):
    # agent-only call → assignee stamped to the SAME owner (lock-step).
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", agent="agent:owner")
    assert inserted["agent"] == "agent:owner"
    assert inserted["assignee"] == "agent:owner"


# --------------------------------------------------------------------------- #
# _default_agent — FAIL LOUD actor/author resolution (operator mandate)       #
#                                                                             #
# Completion/comment authorship used to fall back to getpass.getuser() /      #
# "unknown", which mis-attributed an unresolved acting agent on the board.    #
# It now fails loud EXACTLY like the creator resolver (constitution rule 2,   #
# "NO silent fallbacks") — it delegates to _resolve_creator_or_raise.         #
# --------------------------------------------------------------------------- #
def test_default_agent_raises_when_unresolved(env):
    # No explicit arg AND no $SCITEX_TODO_AGENT_ID → fail loud (no getuser()).
    env.delete("SCITEX_TODO_AGENT_ID")
    with pytest.raises(TaskValidationError) as exc:
        _store._default_agent(None)
    msg = str(exc.value)
    assert "SCITEX_TODO_AGENT_ID" in msg
    assert "by=" in msg


def test_default_agent_unknown_sentinel_also_raises(env):
    # The "unknown" placeholder is NOT a real actor — fail loud, never stamp it.
    env.set("SCITEX_TODO_AGENT_ID", "unknown")
    with pytest.raises(TaskValidationError):
        _store._default_agent(None)


def test_default_agent_resolves_from_env(env):
    env.set("SCITEX_TODO_AGENT_ID", "agent:actor-env")
    assert _store._default_agent(None) == "agent:actor-env"


def test_default_agent_explicit_arg_wins(env):
    # An explicit by=/actor is used even with the env unset — the raise only
    # fires when NOTHING is resolvable (legitimate callers are unaffected).
    env.delete("SCITEX_TODO_AGENT_ID")
    assert _store._default_agent("agent:explicit-actor") == "agent:explicit-actor"


def test_default_agent_matches_creator_resolver(env):
    # DRY: _default_agent now shares the SSOT fail-loud behaviour with
    # _resolve_creator_or_raise (both resolve/raise identically).
    env.set("SCITEX_TODO_AGENT_ID", "agent:same")
    assert _store._default_agent(None) == _store._resolve_creator_or_raise(None)
    env.delete("SCITEX_TODO_AGENT_ID")
    with pytest.raises(TaskValidationError):
        _store._default_agent(None)
    with pytest.raises(TaskValidationError):
        _store._resolve_creator_or_raise(None)


# --------------------------------------------------------------------------- #
# comment toast — INBOX QUEUE shape + SSOT target; actor never queued           #
# --------------------------------------------------------------------------- #
def _toast():
    from scitex_cards._django.handlers._comment_relay import comment_inbox_toast

    return comment_inbox_toast


def test_toast_no_owner_has_empty_target_and_inbox_wire():
    # The card has NEITHER agent NOR assignee — no owner to target. The toast
    # still reports the inbox wire (delivery is the always-works rail) with an
    # empty target + empty queue. No network involved (no deliver()).
    result = _toast()({"id": "orphan", "title": "x"}, "operator")
    assert result["sent"] is True
    assert result["wire"] == "inbox"
    assert result["target"] == ""
    assert result["queued"] == []


def test_toast_target_is_assignee_when_no_agent():
    # An assignee-only card resolves its owner (the toast TARGET) via the
    # owner SSOT (card_owner: agent → assignee). No network.
    result = _toast()({"id": "c1", "title": "x", "assignee": "bob"}, "operator")
    assert result["wire"] == "inbox"
    assert result["target"] == "bob"


def test_toast_target_is_agent_when_present():
    result = _toast()({"id": "c1", "title": "x", "agent": "alice"}, "operator")
    assert result["wire"] == "inbox"
    assert result["target"] == "alice"


def test_toast_self_comment_does_not_queue_the_author():
    # The author (actor) is never notified of their own comment — when they
    # are the sole recipient (owner==author), queued is empty. The target
    # still resolves to the owner via the SSOT.
    result = _toast()({"id": "c1", "title": "x", "agent": "alice"}, "alice")
    assert result["target"] == "alice"
    assert "alice" not in result["queued"]

# EOF
