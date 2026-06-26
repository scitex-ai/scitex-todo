#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notify-config RESOLVER: the pure recipient resolver (foundation C3).

The resolver half of foundation C3 (see :mod:`scitex_todo._notify`). Holds
:func:`card_role_members` (role → recipient-id sets, with raw-name fallback),
the per-card ``notify`` parser, the per-user prefs reader, and THE deliverable
:func:`resolve_recipients`. PURE: deterministic, side-effect-free — it reads
users + config and returns a ``set``; it performs NO delivery (delivery is C4)
and mutates nothing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Mapping

from .._users import User, get_user, resolve_user
from ._config import (
    coerce_id_list,
    coerce_rules_mapping,
    load_notify_config,
    validate_event_type,
)
from ._rules import (
    EVENT_TYPES,
    ROLE_ASSIGNEE,
    ROLE_COLLABORATORS,
    ROLE_OWNER,
    ROLE_SUBSCRIBERS,
    VALID_ROLES,  # noqa: F401 — re-exported for callers/tests via __all__
    NotifyConfig,
    NotifyConfigError,
)


# --------------------------------------------------------------------------- #
# Role membership resolution                                                  #
# --------------------------------------------------------------------------- #
def _resolve_name_to_id(name: str, *, store: str | Path | None) -> str:
    """Map a card-owner name to a stable user id, falling back to the name.

    Reuses :func:`scitex_todo._users.resolve_user` as the SSOT for identity.
    When the name maps to no registered user (``resolve_user`` returns
    ``None``), the raw name string is returned — preserving back-compat with
    the pre-registry world where owners were free-form strings.
    """
    user = resolve_user(name, store=store)
    return user.id if user is not None else name


def _card_field_names(card: Mapping[str, Any], field: str) -> list[str]:
    """Return the raw name strings under a card ``field`` (scalar or list).

    Tolerates the scalar fields (``agent`` / ``assignee``) and the list
    fields (``collaborators`` / ``subscribers``); drops empty / non-string
    entries defensively.
    """
    value = card.get(field)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str) and v]
    return []


def card_role_members(
    card: Mapping[str, Any], *, store: str | Path | None = None
) -> dict[str, set[str]]:
    """Resolve each card role to a set of recipient ids (name-fallback).

    Roles (see :data:`~scitex_todo._notify._rules.VALID_ROLES`):

    * ``owner``         — the card ``agent`` field, falling back to
      ``assignee`` when ``agent`` is absent / empty.
    * ``assignee``      — the card ``assignee`` field (the legacy owner field,
      exposed as its own role so a rule can target it explicitly).
    * ``collaborators`` — the card ``collaborators`` list.
    * ``subscribers``   — the card ``subscribers`` list.

    Every name is mapped to a stable user id via :func:`resolve_user`,
    FALLING BACK to the raw name string when the name resolves to no
    registered user (back-compat with the pre-registry world).

    Parameters
    ----------
    card : Mapping
        A task/card dict (the same shape :class:`scitex_todo._model.Task`
        round-trips). Only the role fields are read.
    store : str | pathlib.Path | None
        Store path forwarded to :func:`resolve_user` for name resolution.

    Returns
    -------
    dict[str, set[str]]
        Role name → set of recipient ids (or raw names for unresolved).
        Every role in :data:`VALID_ROLES` is present (empty set when the
        card has no member in that role).
    """
    # owner = agent, falling back to assignee when agent is absent/empty.
    owner_names = _card_field_names(card, "agent")
    if not owner_names:
        owner_names = _card_field_names(card, "assignee")

    raw_by_role: dict[str, list[str]] = {
        ROLE_OWNER: owner_names,
        ROLE_ASSIGNEE: _card_field_names(card, "assignee"),
        ROLE_COLLABORATORS: _card_field_names(card, "collaborators"),
        ROLE_SUBSCRIBERS: _card_field_names(card, "subscribers"),
    }
    return {
        role: {_resolve_name_to_id(n, store=store) for n in names}
        for role, names in raw_by_role.items()
    }


def _card_member_ids(role_members: Mapping[str, set[str]]) -> set[str]:
    """Union of every role's members — i.e. everyone attached to the card.

    Used by the per-user ``watch`` rule (a watch only adds a user who is
    ALREADY a member of the card, in any role).
    """
    members: set[str] = set()
    for ids in role_members.values():
        members |= ids
    return members


# --------------------------------------------------------------------------- #
# Per-card override (layer 3)                                                 #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class _CardNotify:
    """Parsed + validated per-card ``notify`` override.

    Internal helper produced by :func:`_coerce_card_notify`. Keeps the three
    sub-fields typed so the resolver can apply them without re-checking shape.
    """

    events: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    add: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    mute: list[str] = dataclasses.field(default_factory=list)


def _coerce_card_notify(raw: object) -> _CardNotify:
    """Validate a per-card ``notify`` field, returning a typed view.

    ``None`` / absent → an empty :class:`_CardNotify` (no overrides). A
    non-mapping or a malformed sub-field fails loud
    (:class:`NotifyConfigError`).
    """
    if raw is None:
        return _CardNotify()
    if not isinstance(raw, Mapping):
        raise NotifyConfigError(
            f"card 'notify' must be a mapping, got {raw!r}"
        )
    events = coerce_rules_mapping(
        raw.get("events", {}), where="card notify 'events'"
    )
    add_raw = raw.get("add", {})
    if not isinstance(add_raw, Mapping):
        raise NotifyConfigError(
            f"card notify 'add' must be a mapping of event_type -> "
            f"[id-or-name, ...], got {add_raw!r}"
        )
    add: dict[str, list[str]] = {}
    for event_type, ids in add_raw.items():
        et = validate_event_type(event_type, where="card notify 'add'")
        add[et] = coerce_id_list(ids, where=f"card notify 'add'[{et!r}]")
    mute = coerce_id_list(raw.get("mute", []), where="card notify 'mute'")
    return _CardNotify(events=events, add=add, mute=mute)


# --------------------------------------------------------------------------- #
# Per-user prefs (layer 2)                                                    #
# --------------------------------------------------------------------------- #
def _user_event_set(user: User, key: str) -> set[str]:
    """Validate + return one ``User.notify`` event-type list as a set.

    Only known event types are kept (an unknown type in a user's prefs is
    ignored rather than fatal — a per-user typo should not break delivery
    for everyone). A non-list value for a present key fails loud.
    """
    value = user.notify.get(key)
    if value is None:
        return set()
    if not isinstance(value, (list, tuple)):
        raise NotifyConfigError(
            f"user {user.id!r} notify {key!r} must be a list of event types, "
            f"got {value!r}"
        )
    return {et for et in value if isinstance(et, str) and et in EVENT_TYPES}


def _lookup_recipient(uid: str, store: str | Path | None) -> "User | None":
    """Find the registered user for a recipient id (or raw-name fallback).

    A recipient in the set is EITHER a stable ``u_*`` id (the common case —
    :func:`card_role_members` resolves names to ids) OR a raw name string
    (the back-compat fallback when a card member is not registered). So we
    try an id lookup first (:func:`get_user`), then a name/host lookup
    (:func:`resolve_user`). Returns ``None`` when neither matches — an
    unregistered raw-name recipient simply has no prefs.
    """
    return get_user(uid, store=store) or resolve_user(uid, store=store)


def _user_notify_prefs(uid: str, store: str | Path | None) -> dict[str, set[str]]:
    """Return a user's parsed ``{mute, watch}`` prefs as event-type sets.

    Looks the recipient up via :func:`_lookup_recipient` (id first, then
    name/host). A user that resolves to nothing — or whose ``notify`` has no
    ``mute`` / ``watch`` — yields empty sets. Unknown keys in ``User.notify``
    are ignored (forward-compat). A non-list ``mute`` / ``watch`` fails loud.
    """
    user = _lookup_recipient(uid, store)
    if user is None or not isinstance(user.notify, Mapping):
        return {"mute": set(), "watch": set()}
    return {
        "mute": _user_event_set(user, "mute"),
        "watch": _user_event_set(user, "watch"),
    }


# --------------------------------------------------------------------------- #
# resolve_recipients — THE deliverable                                        #
# --------------------------------------------------------------------------- #
def _event_type_of(event: Any) -> str:
    """Extract the event type from an :class:`Event` or a plain dict.

    Accepts a :class:`scitex_todo._events.Event` (``.type``) or a wire dict
    (``event["type"]``). Fails loud on a missing / unknown type.
    """
    if isinstance(event, Mapping):
        event_type = event.get("type")
    else:
        event_type = getattr(event, "type", None)
    return validate_event_type(event_type, where="event")


def resolve_recipients(
    event: Any,
    card: Mapping[str, Any],
    *,
    store: str | Path | None = None,
    config: NotifyConfig | None = None,
) -> set[str]:
    """Resolve the recipient id set for ``event`` on ``card`` (PURE).

    Composes the three config layers (precedence GLOBAL ← PER-USER ←
    PER-CARD, most-specific wins) and returns a ``set`` of recipient ids
    (raw-name fallback for any member that :func:`resolve_user` cannot
    resolve). Deterministic and side-effect-free: it reads users + config
    and computes a set; it performs NO delivery and mutates nothing
    (delivery is C4).

    Algorithm
    ---------
    a. ``event_type`` from ``event["type"]`` (a dict) or ``event.type``
       (an :class:`~scitex_todo._events.Event`); validated against the
       taxonomy.
    b. ``roles`` = the per-card ``events[event_type]`` override if present,
       else the global ``config`` default for ``event_type`` (else ``[]``).
    c. ``base`` = union of each role's members (via :func:`card_role_members`).
    d. PER-USER: drop any member whose ``User.notify.mute`` includes
       ``event_type``; add any CARD MEMBER whose ``User.notify.watch``
       includes ``event_type`` (a watch never adds a non-member).
    e. PER-CARD: add every id/name in ``notify.add[event_type]`` (resolved to
       ids); then remove every id/name in ``notify.mute`` (resolved to ids).
       The card ``mute`` is applied LAST, so it beats ``add`` and ``watch``.
    f. Return the resulting set.

    Parameters
    ----------
    event : Event | Mapping
        The canonical event (typed or wire-dict). Only ``type`` is read.
    card : Mapping
        The task/card dict. Role fields + an optional ``notify`` override
        are read.
    store : str | pathlib.Path | None
        Store path forwarded to the registry for name/id resolution and to
        :func:`load_notify_config` when ``config`` is not supplied.
    config : NotifyConfig | None
        Pre-loaded global config. ``None`` loads it via
        :func:`load_notify_config` (so the resolver works standalone). Pass
        a shared config when resolving many events to avoid re-reading the
        sidecar.

    Returns
    -------
    set[str]
        Recipient user ids (raw names for unresolved members).

    Raises
    ------
    NotifyConfigError
        On a malformed per-card ``notify`` field, a malformed sidecar (when
        ``config`` is loaded here), or an unknown / missing event type.
    """
    # (a) event type
    event_type = _event_type_of(event)

    # global config (layer 1)
    if config is None:
        config = load_notify_config(store)

    # per-card override view (layer 3, parsed up front for steps b + e)
    card_notify = _coerce_card_notify(card.get("notify"))

    # role membership (shared by steps b/c/d)
    role_members = card_role_members(card, store=store)

    # (b) roles: per-card events override wins over the global default
    if event_type in card_notify.events:
        roles = card_notify.events[event_type]
    else:
        roles = config.roles_for(event_type)

    # (c) base = union of role -> members
    recipients: set[str] = set()
    for role in roles:
        recipients |= role_members.get(role, set())

    # (d) per-user mute / watch
    card_members = _card_member_ids(role_members)
    # mute: drop current recipients who opted out of this event type.
    for uid in list(recipients):
        if event_type in _user_notify_prefs(uid, store)["mute"]:
            recipients.discard(uid)
    # watch: add card members who opted IN to this event type (even if no
    # role matched them). A watch never pulls in a non-member.
    for uid in card_members:
        if event_type in _user_notify_prefs(uid, store)["watch"]:
            recipients.add(uid)

    # (e) per-card add / mute (card layer wins, applied last)
    for name in card_notify.add.get(event_type, []):
        recipients.add(_resolve_name_to_id(name, store=store))
    for name in card_notify.mute:
        recipients.discard(_resolve_name_to_id(name, store=store))

    # (f) result
    return recipients


__all__ = [
    "card_role_members",
    "resolve_recipients",
]

# EOF
