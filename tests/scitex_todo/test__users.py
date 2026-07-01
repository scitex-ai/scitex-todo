#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the standalone user registry (`scitex_todo._users`).

Real round-trips against a `tmp_path` YAML store — no mocks (Req STX-NM /
PA-306). Covers stable-id generation + persistence round-trip, fail-loud
validation, cross-registry name uniqueness, alias resolution (including an
OLD alias surviving a rename), and the hard guarantee that user writes do
NOT disturb the `tasks:` payload living in the same file.
"""

from __future__ import annotations

import pytest

from scitex_todo import _model, _store, _users


# --------------------------------------------------------------------------- #
# register_user: stable id, persistence, round-trip                          #
# --------------------------------------------------------------------------- #
def test_register_user_generates_stable_prefixed_id(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["proj-scitex-dev"], store=store)
    assert user.id.startswith("u_")
    # 12 hex chars after the prefix.
    token = user.id[len("u_"):]
    assert len(token) == 12 and all(c in "0123456789abcdef" for c in token)


def test_register_user_ids_are_unique(tmp_path):
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a"], store=store)
    b = _users.register_user(kind="human", names=["b"], store=store)
    assert a.id != b.id


def test_register_user_round_trips_on_reload(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["scitex-dev", "proj-scitex-dev"],
        host_at_name="ywata-note-win@scitex-dev",
        notify={"telegram": True},
        store=store,
    )
    # Fresh read from disk (no in-memory carry-over).
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None
    assert reloaded.id == created.id
    assert reloaded.kind == "agent"
    assert reloaded.names == ["scitex-dev", "proj-scitex-dev"]
    assert reloaded.host_at_name == "ywata-note-win@scitex-dev"
    assert reloaded.notify == {"telegram": True}
    assert reloaded.created_at  # stamped


def test_register_user_string_names_coerced_to_list(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="human", names="alice", store=store)
    assert user.names == ["alice"]


# --------------------------------------------------------------------------- #
# tasks in the SAME file are untouched by a user write                       #
# --------------------------------------------------------------------------- #
def test_user_write_does_not_disturb_tasks(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="t1", title="Task one", status="pending", assignee="agent:test-suite")
    _store.add_task(store, id="t2", title="Task two", status="in_progress", assignee="agent:test-suite")
    before = _model.load_tasks(store)

    _users.register_user(kind="agent", names=["owner-1"], store=store)

    after = _model.load_tasks(store)
    assert after == before
    # And the users section is now present + readable alongside the tasks.
    assert [u.names[0] for u in _users.load_users(store)] == ["owner-1"]


def test_task_write_after_user_write_keeps_users(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["owner-1"], store=store)
    _store.add_task(store, id="t1", title="Task one", status="pending", assignee="agent:test-suite")
    # The user survives a subsequent task write (round-trip preserves it).
    assert _users.get_user(user.id, store=store) is not None
    assert [t["id"] for t in _model.load_tasks(store)] == ["t1"]


# --------------------------------------------------------------------------- #
# validate_user: fail-loud                                                   #
# --------------------------------------------------------------------------- #
def test_validate_user_rejects_bad_kind(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="robot", names=["x"], store=store)


def test_validate_user_rejects_empty_names(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=[], store=store)


def test_validate_user_rejects_empty_string_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=[""], store=store)


def test_validate_user_rejects_malformed_host_at_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="agent",
            names=["x"],
            host_at_name="host@",  # malformed: empty name half
            store=store,
        )


def test_validate_user_accepts_bare_host_at_name(tmp_path):
    # A bare name (no '@') is a valid host-unknown id per canonical_agent_id.
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent", names=["x"], host_at_name="just-a-name", store=store
    )
    assert user.host_at_name == "just-a-name"


# --------------------------------------------------------------------------- #
# name uniqueness across the registry                                        #
# --------------------------------------------------------------------------- #
def test_duplicate_name_on_register_rejected(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["dup"], store=store)
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="human", names=["dup"], store=store)


def test_duplicate_name_on_add_alias_rejected(tmp_path):
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a-name"], store=store)
    _users.register_user(kind="human", names=["b-name"], store=store)
    with pytest.raises(_users.UserValidationError):
        _users.add_alias(a.id, "b-name", store=store)


def test_add_alias_is_idempotent(tmp_path):
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a-name"], store=store)
    _users.add_alias(a.id, "a-name", store=store)  # no-op
    reloaded = _users.get_user(a.id, store=store)
    assert reloaded is not None
    assert reloaded.names == ["a-name"]


# --------------------------------------------------------------------------- #
# resolve_user: alias, host_at_name, unknown, post-rename old alias          #
# --------------------------------------------------------------------------- #
def test_resolve_user_by_alias(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    found = _users.resolve_user("scitex-dev", store=store)
    assert found is not None and found.id == user.id


def test_resolve_user_by_host_at_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["scitex-dev"],
        host_at_name="ywata-note-win@scitex-dev",
        store=store,
    )
    found = _users.resolve_user("ywata-note-win@scitex-dev", store=store)
    assert found is not None and found.id == user.id


def test_resolve_user_unknown_returns_none(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    assert _users.resolve_user("nobody", store=store) is None


def test_old_alias_still_resolves_after_rename(tmp_path):
    store = tmp_path / "tasks.yaml"
    # Original name proj-scitex-dev; the rename adds the new name as an alias
    # while keeping the old one so historic card references still resolve.
    user = _users.register_user(
        kind="agent", names=["proj-scitex-dev"], store=store
    )
    _users.add_alias(user.id, "scitex-dev", store=store)
    # Both the new and the OLD name resolve to the same stable id.
    new = _users.resolve_user("scitex-dev", store=store)
    old = _users.resolve_user("proj-scitex-dev", store=store)
    assert new is not None and old is not None
    assert new.id == old.id == user.id


# --------------------------------------------------------------------------- #
# delivery endpoint: turn_url / a2a_port field + user_turn_url helper         #
# --------------------------------------------------------------------------- #
def test_register_user_persists_explicit_turn_url(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["proj-x"],
        turn_url="https://explicit/v1/turn/x",
        store=store,
    )
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None and reloaded.turn_url == "https://explicit/v1/turn/x"


def test_register_user_persists_a2a_port(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["proj-y"],
        host_at_name="ywata-note-win@proj-y",
        a2a_port=19007,
        store=store,
    )
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None and reloaded.a2a_port == 19007


def test_user_turn_url_prefers_explicit_over_port(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["proj-z"],
        host_at_name="some-host@proj-z",
        turn_url="https://explicit/turn",
        a2a_port=19007,
        store=store,
    )
    assert _users.user_turn_url(user) == "https://explicit/turn"


def test_user_turn_url_derives_from_port_and_host_at_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["proj-w"],
        host_at_name="my-host@proj-w",
        a2a_port=19007,
        store=store,
    )
    assert _users.user_turn_url(user) == "http://my-host:19007/v1/turn"


def test_user_turn_url_bare_host_at_name_uses_loopback(tmp_path):
    # A bare (host-unknown) id → loopback host, mirroring the sac-registry
    # a2a_port derivation convention.
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent", names=["proj-bare"], a2a_port=18080, store=store
    )
    assert _users.user_turn_url(user) == "http://127.0.0.1:18080/v1/turn"


def test_user_turn_url_none_when_no_endpoint(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["proj-none"], store=store)
    assert _users.user_turn_url(user) is None


def test_validate_user_rejects_non_positive_a2a_port(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="agent", names=["x"], a2a_port=0, store=store
        )


def test_validate_user_rejects_non_int_a2a_port(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="agent", names=["x"], a2a_port="19007", store=store
        )


def test_validate_user_rejects_empty_string_turn_url(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="agent", names=["x"], turn_url="", store=store
        )


def test_endpoint_absent_user_round_trips_without_endpoint_keys(tmp_path):
    # Backward-compat: a user with no endpoint round-trips with both fields
    # None (and to_dict omits them, keeping the YAML compact).
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(kind="human", names=["alice"], store=store)
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None
    assert reloaded.turn_url is None and reloaded.a2a_port is None
    assert "turn_url" not in created.to_dict()
    assert "a2a_port" not in created.to_dict()


# --------------------------------------------------------------------------- #
# set_notify                                                                  #
# --------------------------------------------------------------------------- #
def test_set_notify_replaces_dict(tmp_path):
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent", names=["x"], notify={"a": 1}, store=store
    )
    _users.set_notify(user.id, {"b": 2}, store=store)
    reloaded = _users.get_user(user.id, store=store)
    assert reloaded is not None and reloaded.notify == {"b": 2}


def test_set_notify_unknown_user_raises(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.set_notify("u_deadbeef0000", {"x": 1}, store=store)


# --------------------------------------------------------------------------- #
# list_users / get_user                                                      #
# --------------------------------------------------------------------------- #
def test_list_users_returns_all(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["a"], store=store)
    _users.register_user(kind="human", names=["b"], store=store)
    assert sorted(u.names[0] for u in _users.list_users(store=store)) == ["a", "b"]


def test_list_users_empty_when_no_section(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="t1", title="Task one", assignee="agent:test-suite")  # tasks but no users
    assert _users.list_users(store=store) == []


def test_get_user_unknown_returns_none(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["a"], store=store)
    assert _users.get_user("u_nope00000000", store=store) is None


# --------------------------------------------------------------------------- #
# is_alive: alive / stale / unknown boundaries (pure classifier)              #
# --------------------------------------------------------------------------- #
import datetime as _dt  # noqa: E402

from scitex_todo._users import User as _User  # noqa: E402
from scitex_todo._users import is_alive as _is_alive  # noqa: E402


def _now():
    return _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)


def test_is_alive_unknown_when_no_last_seen():
    u = _User(id="u_x", kind="agent", names=["a"])
    out = _is_alive(u, now=_now())
    assert out == {"status": "unknown", "last_seen": None, "age_seconds": None}


def test_is_alive_unknown_when_user_none():
    out = _is_alive(None, now=_now())
    assert out["status"] == "unknown"
    assert out["age_seconds"] is None


def test_is_alive_alive_within_ttl():
    # 5 minutes ago, default ttl 600s → alive.
    seen = (_now() - _dt.timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
    u = _User(id="u_x", kind="agent", names=["a"], last_seen=seen)
    out = _is_alive(u, now=_now())
    assert out["status"] == "alive"
    assert out["age_seconds"] == 300
    assert out["last_seen"] == seen


def test_is_alive_at_ttl_boundary_is_alive():
    # Exactly ttl old → alive (inclusive boundary).
    seen = (_now() - _dt.timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
    u = _User(id="u_x", kind="agent", names=["a"], last_seen=seen)
    assert _is_alive(u, now=_now(), ttl_seconds=600)["status"] == "alive"


def test_is_alive_stale_past_ttl():
    # 601s old, ttl 600 → stale.
    seen = (_now() - _dt.timedelta(seconds=601)).isoformat().replace("+00:00", "Z")
    u = _User(id="u_x", kind="agent", names=["a"], last_seen=seen)
    out = _is_alive(u, now=_now(), ttl_seconds=600)
    assert out["status"] == "stale"
    assert out["age_seconds"] == 601


def test_is_alive_unknown_on_malformed_last_seen():
    u = _User(id="u_x", kind="agent", names=["a"], last_seen="not-a-timestamp")
    assert _is_alive(u, now=_now())["status"] == "unknown"


def test_is_alive_accepts_plain_offset_stamp():
    # +00:00 form (not the Z form) still parses.
    seen = (_now() - _dt.timedelta(seconds=10)).isoformat()  # +00:00
    u = _User(id="u_x", kind="agent", names=["a"], last_seen=seen)
    assert _is_alive(u, now=_now())["status"] == "alive"


# --------------------------------------------------------------------------- #
# last_seen persistence + touch_user heartbeat                               #
# --------------------------------------------------------------------------- #
def test_last_seen_round_trips_on_reload(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(kind="agent", names=["hb"], store=store)
    assert created.last_seen is None  # never seen at registration
    touched = _users.touch_user("hb", store=store)
    assert touched is not None and touched.last_seen
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None and reloaded.last_seen == touched.last_seen


def test_touch_user_by_id_and_host_at_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    u = _users.register_user(
        kind="agent",
        names=["hb2"],
        host_at_name="ywata-note-win@hb2",
        store=store,
    )
    assert _users.touch_user(u.id, store=store) is not None
    assert _users.touch_user("ywata-note-win@hb2", store=store) is not None


def test_touch_user_unknown_returns_none(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["hb3"], store=store)
    # An unregistered actor has no record to stamp → None (never raises).
    assert _users.touch_user("nobody", store=store) is None


def test_touch_user_preserves_other_keys(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["hb4", "old-hb4"],
        host_at_name="host@hb4",
        notify={"telegram": True},
        store=store,
    )
    _users.touch_user("hb4", store=store)
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None
    assert reloaded.names == ["hb4", "old-hb4"]
    assert reloaded.host_at_name == "host@hb4"
    assert reloaded.notify == {"telegram": True}
    assert reloaded.last_seen


def test_store_action_stamps_last_seen_on_actor(tmp_path, monkeypatch):
    # A store action (comment) by a REGISTERED actor stamps its last_seen.
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["actor-1"], store=store)
    monkeypatch.setenv("SCITEX_TODO_AGENT", "actor-1")
    _store.add_task(store, id="t1", title="T", assignee="actor-1", created_by="actor-1")
    # created_by heartbeat already stamped; comment re-stamps.
    _store.comment_task(store, "t1", "hello", by="actor-1")
    seen = _users.resolve_user("actor-1", store=store)
    assert seen is not None and seen.last_seen

# EOF
