#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The subscriber invariant — assignee mandatory, creator default-removable.

Operator rule (2026-07-06), the ASYMMETRIC subscriber model this module
enforces:

* The **assignee** (a card's owner = ``agent`` else ``assignee``) is ALWAYS a
  subscriber. It is seeded at creation, re-added on every reassign, and CANNOT
  be removed (:func:`is_mandatory_subscriber` → :func:`set_subscriber` refuses
  a remove that targets the current assignee).
* The **creator** (``created_by``) is a subscriber BY DEFAULT — seeded once at
  creation — but is freely removable (the creator may unsubscribe later).

The store keeps ``subscribers`` as an explicit persisted list; this module is
the single source of truth for the two rules above so ``add_task`` /
``reassign_task`` / ``set_subscriber`` (and a backfill sweep) all agree.

Pure + side-effect-free: every function takes plain dicts / strings and
returns values or mutates the passed card in place — no I/O, no store lock,
no imports of the mutation layer (the store calls THESE, never the reverse).
"""

from __future__ import annotations

#: Card field holding the explicit subscriber id list.
SUBSCRIBERS_FIELD = "subscribers"


def owner_of(card: dict) -> str:
    """The card's owner = the ASSIGNEE. ``agent`` first, ``assignee`` fallback.

    Mirrors the owner resolution used across the package (board / notify /
    reminders): the operator-co-designed ``agent`` field is canonical, the
    legacy ``assignee`` is the fallback. Returns ``""`` when neither is set.
    """
    owner = card.get("agent") or card.get("assignee") or ""
    return owner.strip() if isinstance(owner, str) else ""


def _clean_list(values) -> list[str]:
    """Coerce a raw subscribers value to a de-duplicated str list (order-stable)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values if isinstance(values, list) else []:
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def seed_subscribers(
    *, owner: str, creator: str, existing=None
) -> list[str]:
    """Return the initial subscriber list for a NEW card.

    Order-stable, de-duplicated union of: any ``existing`` subscribers the
    caller passed explicitly, then the ``owner`` (assignee — mandatory), then
    the ``creator`` (default). Both roles are seeded; the owner is the one
    that later becomes non-removable. Empty owner/creator are skipped (the
    ``add_task`` fail-loud gate already rejects an owner-less/creatorless
    card, so in practice both are present — this stays defensive).
    """
    out = _clean_list(existing)
    seen = set(out)
    for who in (owner, creator):
        who = who.strip() if isinstance(who, str) else ""
        if who and who not in seen:
            seen.add(who)
            out.append(who)
    return out


def ensure_assignee_subscribed(card: dict) -> dict:
    """Guarantee the card's current owner (assignee) is in ``subscribers``.

    Mutates ``card`` in place and returns it. The assignee is the MANDATORY
    subscriber — this re-adds it if a hand-edit, a legacy card, or a reassign
    dropped it. A no-op when the owner is empty or already present. Keeps the
    list de-duplicated + order-stable (appends the owner last if missing).
    """
    owner = owner_of(card)
    if not owner:
        return card
    current = _clean_list(card.get(SUBSCRIBERS_FIELD))
    if owner not in current:
        current.append(owner)
    if current:
        card[SUBSCRIBERS_FIELD] = current
    return card


def is_mandatory_subscriber(card: dict, who: str) -> bool:
    """True if ``who`` is the card's current owner (assignee) → cannot unsubscribe.

    The single predicate :func:`set_subscriber` consults before honoring a
    ``remove``: the assignee is mandatory, everyone else (creator included)
    is removable.
    """
    who = who.strip() if isinstance(who, str) else ""
    return bool(who) and who == owner_of(card)


__all__ = [
    "SUBSCRIBERS_FIELD",
    "ensure_assignee_subscribed",
    "is_mandatory_subscriber",
    "owner_of",
    "seed_subscribers",
]

# EOF
