#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-loud OWNER / CREATOR identity enforcement (``scitex_todo._owner_guard``).

Closes ``todo-failloud-rollout-fleet-identity-createform-20260626``. Real
round-trips against ``tmp_path`` YAML stores — no mocks (Req STX-NM /
PA-306). Every enforcement assertion turns ``SCITEX_TODO_STRICT_IDENTITY``
ON via the ``env`` fixture FIRST — see ``_owner_guard``'s module docstring
for why the gate defaults OFF (production registry has 1 user; the suite's
autouse ``_default_resolvable_creator`` fixture + several existing tests
rely on ``created_by`` being freely settable).
"""

from __future__ import annotations

import pytest

from scitex_todo import _store, _users
from scitex_todo._model import TaskValidationError
from scitex_todo._owner_guard import (
    ENV_ALLOW_UNKNOWN_OWNER,
    check_created_by_not_forged,
    check_owner_known,
    enforcement_enabled,
    escape_hatch_enabled,
)
from scitex_todo._users import ENV_STRICT_IDENTITY

_ENV_AGENT = "SCITEX_TODO_AGENT_ID"


# --------------------------------------------------------------------------- #
# enforcement_enabled / escape_hatch_enabled — the gates themselves          #
# --------------------------------------------------------------------------- #
def test_enforcement_disabled_by_default(env):
    env.delete(ENV_STRICT_IDENTITY)
    assert enforcement_enabled() is False


def test_enforcement_enabled_when_flag_set(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    assert enforcement_enabled() is True


def test_escape_hatch_off_by_default(env):
    env.delete(ENV_ALLOW_UNKNOWN_OWNER)
    assert escape_hatch_enabled(False) is False


def test_escape_hatch_via_explicit_flag(env):
    env.delete(ENV_ALLOW_UNKNOWN_OWNER)
    assert escape_hatch_enabled(True) is True


def test_escape_hatch_via_env(env):
    env.set(ENV_ALLOW_UNKNOWN_OWNER, "1")
    assert escape_hatch_enabled(False) is True


# --------------------------------------------------------------------------- #
# check_owner_known — rule (2)                                              #
# --------------------------------------------------------------------------- #
def test_check_owner_known_noop_when_enforcement_disabled(tmp_path, env):
    env.delete(ENV_STRICT_IDENTITY)
    store = tmp_path / "tasks.yaml"
    # Unknown owner, but enforcement is OFF -> no raise (back-compat).
    check_owner_known("totally-unknown-xyz", store=store)


def test_check_owner_known_raises_on_unknown_when_enforced(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        check_owner_known("totally-unknown-xyz", store=store)
    assert "totally-unknown-xyz" in str(exc.value)


def test_check_owner_known_accepts_registered_owner(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Must not raise.
    check_owner_known("scitex-dev", store=store)


def test_check_owner_known_proj_prefix_gives_near_match_hint(tmp_path, env):
    # Neither 'proj-scitex-dev' nor 'scitex-dev' is registered — this is
    # THE exact incident shape. The error must still name the bad value and
    # hint at the mechanically-stripped form so a human can tell "did you
    # mean scitex-dev, or is this a fully dead identity?"
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        check_owner_known("proj-scitex-dev", store=store)
    msg = str(exc.value)
    assert "proj-scitex-dev" in msg
    assert "scitex-dev" in msg


def test_check_owner_known_alias_resolution_still_works(tmp_path, env):
    # 'scitex-agent-container' is registered (NOT the literal alias 'sac');
    # resolve_user's canonicalised retry must still resolve 'sac' via
    # _users.IDENTITY_ALIASES without raising.
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    _users.register_user(
        kind="agent", names=["scitex-agent-container"], store=store
    )
    check_owner_known("sac", store=store)


def test_check_owner_known_escape_hatch_explicit(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.delete(ENV_ALLOW_UNKNOWN_OWNER)
    store = tmp_path / "tasks.yaml"
    # Explicit allow_unknown_owner=True bypasses even with strict mode on.
    check_owner_known("dead-identity", store=store, allow_unknown_owner=True)


def test_check_owner_known_escape_hatch_via_env(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(ENV_ALLOW_UNKNOWN_OWNER, "1")
    store = tmp_path / "tasks.yaml"
    check_owner_known("dead-identity", store=store)


def test_check_owner_known_blank_owner_is_noop(tmp_path, env):
    # Blank-owner rejection is a SEPARATE, always-on check at the call site
    # (add_task's "assignee is required"); this guard has nothing to say
    # about blank/None.
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    check_owner_known(None, store=store)
    check_owner_known("   ", store=store)


# --------------------------------------------------------------------------- #
# check_created_by_not_forged — rule (3)                                    #
# --------------------------------------------------------------------------- #
def test_created_by_noop_when_enforcement_disabled(env):
    env.delete(ENV_STRICT_IDENTITY)
    env.set(_ENV_AGENT, "agent:me")
    # Would be a mismatch if enforced — must NOT raise with the gate off.
    check_created_by_not_forged("someone-else")


def test_created_by_mismatch_raises_when_enforced(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:me")
    with pytest.raises(TaskValidationError) as exc:
        check_created_by_not_forged("someone-else")
    msg = str(exc.value)
    assert "someone-else" in msg
    assert "agent:me" in msg


def test_created_by_match_does_not_raise(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:me")
    check_created_by_not_forged("agent:me")


def test_created_by_noop_when_env_unset(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.delete(_ENV_AGENT)
    # Nothing to compare against -> the explicit value is accepted.
    check_created_by_not_forged("whoever")


def test_created_by_alias_match_does_not_raise(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "sac")
    # 'sac' canonicalises to 'scitex-agent-container' via IDENTITY_ALIASES.
    check_created_by_not_forged("scitex-agent-container")


def test_created_by_escape_hatch_permits_mismatch(env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:me")
    check_created_by_not_forged("someone-else", allow_unknown_owner=True)


# --------------------------------------------------------------------------- #
# add_task integration                                                       #
# --------------------------------------------------------------------------- #
def test_add_task_rejects_unknown_owner_when_strict(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(store, id="a", title="A", assignee="proj-scitex-dev")
    assert "proj-scitex-dev" in str(exc.value)


def test_add_task_accepts_known_owner_when_strict(tmp_path, env):
    # This is the reassignment-target / normal-assign case: a KNOWN owner
    # MUST keep working even with strict enforcement on.
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["known-owner"], store=store)
    inserted = _store.add_task(store, id="a", title="A", assignee="known-owner")
    assert inserted["assignee"] == "known-owner"


def test_add_task_rejects_forged_created_by_when_strict(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:me")
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["known-owner"], store=store)
    with pytest.raises(TaskValidationError) as exc:
        _store.add_task(
            store,
            id="a",
            title="A",
            assignee="known-owner",
            created_by="someone-else",
        )
    assert "someone-else" in str(exc.value)


def test_add_task_created_by_defaults_from_env_when_omitted(tmp_path, env):
    # Rule (1): omitted created_by resolves from $SCITEX_TODO_AGENT_ID.
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:me")
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["known-owner"], store=store)
    inserted = _store.add_task(store, id="a", title="A", assignee="known-owner")
    assert inserted["created_by"] == "agent:me"


def test_add_task_escape_hatch_permits_unknown_owner(tmp_path, env):
    # Genuine repair-tooling shape: reassigning/creating against an already-
    # dead identity on purpose. Off by default; explicit opt-in only.
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store,
        id="a",
        title="A",
        assignee="dead-proj-identity",
        allow_unknown_owner=True,
    )
    assert inserted["assignee"] == "dead-proj-identity"


def test_add_task_owner_check_off_by_default(tmp_path, env):
    # Suite default: SCITEX_TODO_STRICT_IDENTITY unset -> unknown owners are
    # still accepted (back-compat; unaffected by this rollout).
    env.delete(ENV_STRICT_IDENTITY)
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(store, id="a", title="A", assignee="whoever")
    assert inserted["assignee"] == "whoever"


# --------------------------------------------------------------------------- #
# update_task integration                                                    #
# --------------------------------------------------------------------------- #
def test_update_task_rejects_unknown_owner_when_strict(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:creator")
    with pytest.raises(TaskValidationError):
        _store.update_task(store, "a", assignee="totally-unknown-xyz")


def test_update_task_accepts_known_owner_when_strict(tmp_path, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["known-owner"], store=store)
    _store.add_task(store, id="a", title="A", assignee="agent:creator")
    result = _store.update_task(store, "a", assignee="known-owner")
    assert result["assignee"] == "known-owner"


def test_update_task_clearing_owner_is_not_validated(tmp_path, env):
    # Clearing a field (None) is a delete, not a "set" — nothing to
    # validate the existence of.
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="agent:creator")
    result = _store.update_task(store, "a", note=None)
    assert "note" not in result


# --------------------------------------------------------------------------- #
# reassign_task integration — the "must keep working" case                  #
# --------------------------------------------------------------------------- #
def test_reassign_task_to_known_owner_still_works_when_strict(tmp_path, env):
    env.delete(ENV_STRICT_IDENTITY)
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="orig-owner")
    _users.register_user(kind="agent", names=["known-new-owner"], store=store)
    # NOW turn enforcement on and reassign to the KNOWN owner.
    env.set(ENV_STRICT_IDENTITY, "1")
    result = _store.reassign_task(store, "a", "known-new-owner")
    assert result["changed"] is True
    assert result["to_owner"] == "known-new-owner"


def test_reassign_task_rejects_unknown_new_owner_when_strict(tmp_path, env):
    env.delete(ENV_STRICT_IDENTITY)
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="orig-owner")
    env.set(ENV_STRICT_IDENTITY, "1")
    with pytest.raises(TaskValidationError):
        _store.reassign_task(store, "a", "totally-unknown-xyz")


def test_reassign_task_escape_hatch_permits_unknown_new_owner(tmp_path, env):
    # The repair-sweep shape: reassigning cards OFF a dead identity onto
    # another (possibly also not-yet-registered) owner during cleanup.
    env.delete(ENV_STRICT_IDENTITY)
    env.set(_ENV_AGENT, "agent:creator")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="orig-owner")
    env.set(ENV_STRICT_IDENTITY, "1")
    result = _store.reassign_task(
        store, "a", "still-unknown-owner", allow_unknown_owner=True
    )
    assert result["changed"] is True

# EOF
