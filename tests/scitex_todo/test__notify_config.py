#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the C3 notify config + pure recipient resolver.

Real round-trips: real users via ``register_user`` in a ``tmp_path`` store,
real cards as plain dicts, real ``notify.yaml`` sidecars on disk. No mocks
(STX-NM / PA-306). AAA pattern.

Coverage mirrors the C3 card's test checklist:

* default resolution per event type (built-in rules),
* role → user-id resolution via the registry; unresolved name → raw string,
* per-user ``mute`` removes a recipient; ``watch`` adds a non-default member,
* per-card ``events`` override / ``add`` force-include / ``mute``
  force-exclude (card beats user/global),
* precedence end-to-end (global < user < card),
* ``notify.yaml`` sidecar override honored; malformed sidecar fails loud;
  absent sidecar → built-in defaults.
"""

from __future__ import annotations

import pytest
import yaml

from scitex_todo._events import Event, EventType
from scitex_todo._notify import (
    DEFAULT_NOTIFY_RULES,
    NOTIFY_SIDECAR_NAME,
    NotifyConfig,
    NotifyConfigError,
    card_role_members,
    load_notify_config,
    resolve_recipients,
)
from scitex_todo._users import register_user, set_notify


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_sidecar(tmp_path, payload):
    """Write a notify.yaml sidecar next to the (tmp) tasks store."""
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return sidecar


# --------------------------------------------------------------------------- #
# load_notify_config: zero-config defaults + sidecar                          #
# --------------------------------------------------------------------------- #
def test_default_config_is_built_in_rules_when_no_sidecar(tmp_path):
    cfg = load_notify_config(store=_store(tmp_path))
    assert isinstance(cfg, NotifyConfig)
    # The built-in SSOT is returned unchanged (zero-config works).
    assert cfg.rules == DEFAULT_NOTIFY_RULES


def test_default_rules_cover_every_event_type():
    # Complete-over-taxonomy invariant: a reader sees the full policy.
    from scitex_todo._events import EVENT_TYPES

    assert set(DEFAULT_NOTIFY_RULES) == set(EVENT_TYPES)


def test_sidecar_overrides_built_in_rules(tmp_path):
    # Arrange — sidecar replaces the role list for `commented`.
    _write_sidecar(tmp_path, {"rules": {"commented": ["subscribers"]}})
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert — overridden event uses the sidecar; others keep the default.
    assert cfg.roles_for("commented") == ["subscribers"]
    assert cfg.roles_for("merged") == DEFAULT_NOTIFY_RULES["merged"]


def test_sidecar_bare_mapping_shape_is_accepted(tmp_path):
    # A bare top-level mapping (no `rules:` key) is treated as the rules map.
    _write_sidecar(tmp_path, {"pulled": ["owner"]})
    cfg = load_notify_config(store=_store(tmp_path))
    assert cfg.roles_for("pulled") == ["owner"]


def test_malformed_sidecar_unknown_event_fails_loud(tmp_path):
    _write_sidecar(tmp_path, {"rules": {"not_an_event": ["owner"]}})
    with pytest.raises(NotifyConfigError):
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_unknown_role_fails_loud(tmp_path):
    _write_sidecar(tmp_path, {"rules": {"commented": ["wizard"]}})
    with pytest.raises(NotifyConfigError):
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_non_mapping_top_level_fails_loud(tmp_path):
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(NotifyConfigError):
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_bad_yaml_fails_loud(tmp_path):
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text("commented: [owner\n  : :::\n", encoding="utf-8")
    with pytest.raises(NotifyConfigError):
        load_notify_config(store=_store(tmp_path))


def test_empty_sidecar_yields_built_in_defaults(tmp_path):
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text("", encoding="utf-8")
    cfg = load_notify_config(store=_store(tmp_path))
    assert cfg.rules == DEFAULT_NOTIFY_RULES


# --------------------------------------------------------------------------- #
# card_role_members: role -> user-id resolution + raw-name fallback           #
# --------------------------------------------------------------------------- #
def test_card_role_members_resolves_to_ids(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    card = {"id": "c1", "agent": "alice", "collaborators": ["bob"]}

    members = card_role_members(card, store=store)

    assert members["owner"] == {owner.id}
    assert members["collaborators"] == {collab.id}
    assert members["subscribers"] == set()


def test_card_role_members_owner_falls_back_to_assignee(tmp_path):
    # No `agent` field → owner role resolves from `assignee` (legacy).
    store = _store(tmp_path)
    u = register_user(kind="agent", names=["carol"], store=store)
    card = {"id": "c1", "assignee": "carol"}

    members = card_role_members(card, store=store)

    assert members["owner"] == {u.id}
    assert members["assignee"] == {u.id}


def test_unresolved_name_falls_back_to_raw_string(tmp_path):
    # `dave` is NOT registered → the raw name string is the recipient id.
    store = _store(tmp_path)
    card = {"id": "c1", "agent": "dave"}

    members = card_role_members(card, store=store)

    assert members["owner"] == {"dave"}


# --------------------------------------------------------------------------- #
# resolve_recipients: default resolution per event type                       #
# --------------------------------------------------------------------------- #
def test_commented_resolves_owner_collaborators_subscribers(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "collaborators": ["bob"],
        "subscribers": ["eve"],
    }

    got = resolve_recipients(
        Event(type=EventType.COMMENTED, card_id="c1"), card, store=store
    )

    assert got == {owner.id, collab.id, sub.id}


def test_pulled_resolves_to_empty_by_default(tmp_path):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice", "subscribers": ["alice"]}

    got = resolve_recipients(
        Event(type=EventType.PULLED, card_id="c1"), card, store=store
    )

    # `pulled` is default-quiet (empty role list).
    assert got == set()


def test_resolver_accepts_event_dict(tmp_path):
    # The resolver accepts an Event OR a plain wire dict.
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice"}

    got = resolve_recipients({"type": "reassigned"}, card, store=store)

    assert got == {owner.id}


def test_resolver_unknown_event_type_fails_loud(tmp_path):
    store = _store(tmp_path)
    card = {"id": "c1", "agent": "alice"}
    with pytest.raises(NotifyConfigError):
        resolve_recipients({"type": "teleported"}, card, store=store)


# --------------------------------------------------------------------------- #
# per-user mute / watch (layer 2)                                             #
# --------------------------------------------------------------------------- #
def test_per_user_mute_removes_recipient(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    # eve mutes `commented` → dropped even though she's a subscriber.
    set_notify(sub.id, {"mute": ["commented"]}, store=store)
    card = {"id": "c1", "agent": "alice", "subscribers": ["eve"]}

    got = resolve_recipients(
        Event(type=EventType.COMMENTED), card, store=store
    )

    assert owner.id in got
    assert sub.id not in got


def test_per_user_watch_adds_member_not_in_default_roles(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    # `merged` default = [owner, subscribers]; bob is a COLLABORATOR (not in
    # the default roles) but watches `merged` → opted in because he's a member.
    set_notify(collab.id, {"watch": ["merged"]}, store=store)
    card = {"id": "c1", "agent": "alice", "collaborators": ["bob"]}

    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)

    assert got == {owner.id, collab.id}


def test_per_user_watch_does_not_add_non_member(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    outsider = register_user(kind="human", names=["mallory"], store=store)
    # mallory watches `merged` but is NOT on the card → never added.
    set_notify(outsider.id, {"watch": ["merged"]}, store=store)
    card = {"id": "c1", "agent": "alice"}

    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)

    assert got == {owner.id}
    assert outsider.id not in got


def test_user_mute_beats_role_membership_but_card_add_can_reinclude(tmp_path):
    # mute drops a recipient; a per-card `add` re-includes them (card wins).
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    set_notify(owner.id, {"mute": ["completed"]}, store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"add": {"completed": ["alice"]}},
    }

    got = resolve_recipients(Event(type=EventType.COMPLETED), card, store=store)

    assert owner.id in got


# --------------------------------------------------------------------------- #
# per-card overrides (layer 3)                                                #
# --------------------------------------------------------------------------- #
def test_per_card_events_override_changes_roles(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    # Default `commented` = owner+collab+subscribers; this card narrows it
    # to subscribers only.
    card = {
        "id": "c1",
        "agent": "alice",
        "subscribers": ["eve"],
        "notify": {"events": {"commented": ["subscribers"]}},
    }

    got = resolve_recipients(
        Event(type=EventType.COMMENTED), card, store=store
    )

    assert got == {sub.id}
    assert owner.id not in got


def test_per_card_add_force_includes(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    extra = register_user(kind="human", names=["frank"], store=store)
    # frank is not in any role but is force-added for `merged`.
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"add": {"merged": ["frank"]}},
    }

    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)

    assert got == {owner.id, extra.id}


def test_per_card_mute_force_excludes_beating_user_and_global(tmp_path):
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    # eve WATCHES commented (opt-in) but the card mutes her → card wins.
    set_notify(sub.id, {"watch": ["commented"]}, store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "subscribers": ["eve"],
        "notify": {"mute": ["eve"]},
    }

    got = resolve_recipients(
        Event(type=EventType.COMMENTED), card, store=store
    )

    assert owner.id in got
    assert sub.id not in got


def test_per_card_mute_beats_per_card_add(tmp_path):
    # mute is applied LAST, so it removes someone `add` just included.
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    frank = register_user(kind="human", names=["frank"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {
            "add": {"merged": ["frank"]},
            "mute": ["frank"],
        },
    }

    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)

    assert got == {owner.id}
    assert frank.id not in got


def test_malformed_card_notify_fails_loud(tmp_path):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice", "notify": ["not", "a", "mapping"]}
    with pytest.raises(NotifyConfigError):
        resolve_recipients(Event(type=EventType.COMMENTED), card, store=store)


def test_malformed_card_events_unknown_role_fails_loud(tmp_path):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"events": {"commented": ["sorcerer"]}},
    }
    with pytest.raises(NotifyConfigError):
        resolve_recipients(Event(type=EventType.COMMENTED), card, store=store)


# --------------------------------------------------------------------------- #
# precedence end-to-end: global < user < card                                 #
# --------------------------------------------------------------------------- #
def test_precedence_global_then_user_then_card(tmp_path):
    """One scenario exercising all three layers + the sidecar at once."""
    store = _store(tmp_path)
    # Sidecar (global): `merged` notifies owner + collaborators (override the
    # built-in owner+subscribers).
    _write_sidecar(
        tmp_path, {"rules": {"merged": ["owner", "collaborators"]}}
    )

    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    watcher = register_user(kind="human", names=["eve"], store=store)
    added = register_user(kind="human", names=["frank"], store=store)

    # User layer: bob mutes merged (opt-out); eve watches merged (opt-in,
    # she's a subscriber so a member).
    set_notify(collab.id, {"mute": ["merged"]}, store=store)
    set_notify(watcher.id, {"watch": ["merged"]}, store=store)

    card = {
        "id": "c1",
        "agent": "alice",
        "collaborators": ["bob"],
        "subscribers": ["eve"],
        # Card layer: add frank; mute eve (card beats her watch).
        "notify": {"add": {"merged": ["frank"]}, "mute": ["eve"]},
    }

    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)

    # Walk-through:
    #   global(sidecar) merged roles = owner+collaborators = {alice, bob}
    #   user mute: bob drops        -> {alice}
    #   user watch: eve is a member -> {alice, eve}
    #   card add: frank             -> {alice, eve, frank}
    #   card mute: eve              -> {alice, frank}
    assert got == {owner.id, added.id}
    assert collab.id not in got
    assert watcher.id not in got


def test_shared_config_is_reused_across_resolves(tmp_path):
    # Passing a pre-loaded config should give the same answer as auto-load.
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice"}
    cfg = load_notify_config(store=store)

    a = resolve_recipients(Event(type=EventType.REASSIGNED), card, store=store)
    b = resolve_recipients(
        Event(type=EventType.REASSIGNED), card, store=store, config=cfg
    )

    assert a == b == {owner.id}

# EOF
