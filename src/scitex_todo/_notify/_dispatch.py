#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notify DISPATCHER: deliver card-events to resolved recipients (foundation C4).

This is **C4** of the card-event / notification foundation epic — the
RESOLVER-DRIVEN delivery layer that turns a canonical card-event
(:mod:`scitex_todo._events`) into actual per-recipient pushes. It is the
single built-in CONSUMER of ``kind == "card-event"`` on the hook bus:
:func:`scitex_todo._hooks.dispatch_event` lazily calls
:func:`dispatch_notifications` for every card-event (see the wiring in
:mod:`scitex_todo._hooks._dispatch`).

It stitches together the three already-merged foundation pieces, reusing
each as SSOT (no reinvention):

* :func:`scitex_todo._notify.resolve_recipients` (C3) — *which* user-ids
  should be notified for this event on this card.
* :func:`scitex_todo._users.get_user` (C5/users) — map a recipient
  user-id back to a delivery NAME.
* :func:`scitex_todo._push.deliver` (existing wire) — actually send the
  one-shot push to a delivery name.

## NO fleet-spam — resolver-driven, not broadcast

Delivery targets ONLY the set :func:`resolve_recipients` returns, minus
the actor (we never notify the cause of the event). There is no fan-out
to "every agent" — the notify-config layer (global rules + per-user
mute/watch + per-card overrides) is the single authority on the
recipient set.

## Fail-soft contract (the dispatcher MUST never break a mutation)

A card-event is emitted from inside a board mutation (reassign, complete,
a git-link push, …) via :func:`scitex_todo._events.emit`, which already
swallows bus errors. C4 adds a SECOND layer of isolation:

* the whole dispatch is wrapped fail-soft at the bus wiring point so a
  delivery hiccup can never make ``emit()`` raise;
* **per-recipient** isolation here — one ``deliver_fn`` raising or
  returning ``ok=False`` for a recipient must NOT stop the others.

## ``deliver_fn`` injection seam (no mocks)

:func:`dispatch_notifications` takes a ``deliver_fn`` parameter defaulting
to :func:`scitex_todo._push.deliver`. Tests pass a real recorder function
(a closure that appends its args to a list) — no ``unittest.mock``, no
monkeypatch of the wire (STX-NM / PA-306-compliant).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: Event types we deliberately DO NOT deliver via C4 (yet).
#:
#: ``commented`` is owned by the interactive comment-relay
#: (:func:`scitex_todo._django.handlers._comment_relay.maybe_relay_comment`),
#: which runs in-line on ``POST /comment`` and gives the operator an
#: immediate, toast-able result. Routing ``commented`` through C4 TOO
#: would DOUBLE-notify the owner and lose the board toast. Migrating the
#: comment relay onto this dispatcher (so there is one delivery path) is a
#: deliberate FOLLOW-UP card; until then we skip it here.
_SKIP_EVENT_TYPES: frozenset[str] = frozenset({"commented"})


def _event_fields(event: Any) -> tuple[str | None, str | None, str | None, dict]:
    """Extract ``(event_type, card_id, actor, extras)`` from any event shape.

    Accepts:

    * the canonical card-event ENVELOPE — a dict carrying
      ``{"kind": "card-event", "type": ..., "card_id": ..., "actor": ...}``
      (what :meth:`scitex_todo._events.Event.to_dict` builds and the bus
      delivers);
    * an inner event dict without the ``kind`` wrapper (same keys);
    * a typed :class:`scitex_todo._events.Event` (read via attributes).

    ``extras`` collects the remaining envelope fields (``repo`` / ``branch``
    / ``pr_url`` / ``sha`` / ``version`` / any ``extra`` keys) so per-event
    bodies can include a useful detail (e.g. a status transition stashed in
    ``extra``) without this module knowing every field name.
    """
    if isinstance(event, Mapping):
        event_type = event.get("type")
        card_id = event.get("card_id")
        actor = event.get("actor")
        # Everything else (minus the envelope discriminators) is an extra.
        extras = {
            k: v
            for k, v in event.items()
            if k not in ("kind", "type", "card_id", "actor", "ts")
        }
        return event_type, card_id, actor, extras
    # Typed Event (or any object with the attributes).
    event_type = getattr(event, "type", None)
    card_id = getattr(event, "card_id", None)
    actor = getattr(event, "actor", None)
    extras: dict[str, Any] = {}
    for field in ("repo", "branch", "pr_url", "sha", "version"):
        val = getattr(event, field, None)
        if val is not None:
            extras[field] = val
    obj_extra = getattr(event, "extra", None)
    if isinstance(obj_extra, Mapping):
        for k, v in obj_extra.items():
            extras.setdefault(k, v)
    return event_type, card_id, actor, extras


def _body_for(event_type: str, card_id: str, actor: str | None, extras: Mapping) -> str:
    """Build a concise, human-readable per-event notification body.

    Deterministic and side-effect-free. Every branch leads with the card
    id so the recipient can grep for it; the ``actor`` (when known) is
    named so the recipient sees who caused it. Unknown / future event
    types fall through to a generic one-liner rather than failing — a new
    event type should never break delivery.
    """
    by = f" (by {actor})" if actor else ""
    if event_type == "reassigned":
        return f"Card {card_id} reassigned to you{by}"
    if event_type == "completed":
        return f"Card {card_id} completed{by}"
    if event_type == "status_changed":
        # The status transition (if the producer stashed it) lives in extras
        # under from/to or status_from/status_to. Best-effort, never required.
        frm = extras.get("from") or extras.get("status_from")
        to = extras.get("to") or extras.get("status_to")
        if frm or to:
            return f"Card {card_id}: {frm or '?'}→{to or '?'}{by}"
        return f"Card {card_id} status changed{by}"
    if event_type == "created":
        return f"Card {card_id} created{by}"
    if event_type == "committed":
        sha = extras.get("sha")
        sha_s = f" {str(sha)[:10]}" if sha else ""
        repo = extras.get("repo")
        repo_s = f" in {repo}" if repo else ""
        return f"Card {card_id}: new commit{sha_s}{repo_s}{by}"
    if event_type == "pushed":
        branch = extras.get("branch")
        branch_s = f" to {branch}" if branch else ""
        repo = extras.get("repo")
        repo_s = f" in {repo}" if repo else ""
        return f"Card {card_id}: pushed{branch_s}{repo_s}{by}"
    if event_type == "merged":
        pr = extras.get("pr_url")
        pr_s = f" {pr}" if pr else ""
        return f"Card {card_id}: PR merged{pr_s}{by}"
    if event_type == "released":
        version = extras.get("version")
        ver_s = f" {version}" if version else ""
        repo = extras.get("repo")
        repo_s = f" {repo}" if repo else ""
        return f"Card {card_id}: released{repo_s}{ver_s}{by}".replace("  ", " ")
    if event_type == "deployed":
        service = extras.get("service")
        svc_s = f" {service}" if service else ""
        return f"Card {card_id}: deployed{svc_s}{by}"
    if event_type == "pulled":
        return f"Card {card_id}: pulled{by}"
    # Forward-compatible default — never fail on an unseen type.
    return f"Card {card_id}: {event_type}{by}"


def _recipient_to_name(uid: str, *, store: Any | None) -> str | None:
    """Map a recipient user-id to a delivery NAME, or ``None`` if unmappable.

    A recipient is EITHER a stable ``u_*`` id (the common case —
    :func:`resolve_recipients` resolves names to ids) OR a raw name string
    (the back-compat fallback when a card member is not registered).

    * registered id → first of the user's ``names`` (the delivery handle),
      falling back to ``host_at_name`` when ``names`` is somehow empty;
    * unregistered string → it IS already a raw name, so return it verbatim.

    Returns ``None`` only when the id resolves to a registered user that has
    NEITHER a usable name NOR a host_at_name (degenerate — skipped + recorded).
    """
    from .._users import get_user

    user = get_user(uid, store=store)
    if user is None:
        # Not a registered id → it is the raw-name fallback. Deliver to it.
        return uid or None
    for name in user.names or ():
        if isinstance(name, str) and name:
            return name
    if user.host_at_name:
        return user.host_at_name
    return None


def dispatch_notifications(
    event: Any,
    *,
    store: Any | None = None,
    deliver_fn: Callable[..., Mapping] | None = None,
) -> dict:
    """Deliver a card-event to its resolved recipients (the C4 deliverable).

    The single built-in consumer of ``kind == "card-event"``: resolve the
    recipient set (C3), drop the actor, map each recipient id to a delivery
    name (users registry), and push a concise per-event message to each via
    ``deliver_fn`` — fail-soft per recipient.

    Parameters
    ----------
    event : Event | Mapping
        The canonical card-event. Accepts the bus ENVELOPE
        (``{"kind": "card-event", "type", "card_id", "actor", ...}``), an
        inner event dict, or a typed :class:`scitex_todo._events.Event`.
    store : path-like, optional
        Store path forwarded to :func:`resolve_recipients` and
        :func:`get_user`. ``None`` resolves via the normal precedence chain.
    deliver_fn : callable, optional
        INJECTION SEAM for the delivery wire. Defaults to
        :func:`scitex_todo._push.deliver`. Called as
        ``deliver_fn(name, body, kind=..., task_id=..., timeout=..., dispatched_is_ok=False)``.
        Tests pass a real recorder (no mocks).

    Returns
    -------
    dict
        A summary::

            {
                "event_type": <str | None>,
                "card_id": <str | None>,
                "delivered": [<delivery name>, ...],
                "skipped": [<reason / name>, ...],
                "errors": [{"recipient": <name>, "error": <str>}, ...],
            }

        Never raises: malformed events, a missing card, and per-recipient
        delivery failures are all folded into the summary (fail-soft).
    """
    event_type, card_id, actor, extras = _event_fields(event)
    summary: dict[str, Any] = {
        "event_type": event_type,
        "card_id": card_id,
        "delivered": [],
        "skipped": [],
        "errors": [],
    }

    # (a) no card → nothing to resolve against (repo-level events without a
    #     card_id, e.g. a release/deploy not tied to a card). No-op.
    if not card_id:
        summary["skipped"].append("no-card-id")
        return summary

    # (b) commented is owned by the interactive comment-relay (see
    #     _SKIP_EVENT_TYPES). Skipping here avoids a double-notify + keeps
    #     the board toast. Migrating the relay onto C4 is a follow-up.
    if event_type in _SKIP_EVENT_TYPES:
        summary["skipped"].append(f"event-type:{event_type}")
        return summary

    # (c) load the card the event is about. Fail-soft on ANY load error
    #     (TaskNotFoundError for an unknown id, or a transient store read
    #     error): a missing/unreadable card must never break the mutation
    #     that emitted the event.
    from .._store import get_task

    try:
        card = get_task(store=store, task_id=card_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft (incl. TaskNotFoundError)
        logger.warning(
            "[scitex-todo._notify] card %r not loadable for %r event; "
            "skipping notify: %s",
            card_id, event_type, exc,
        )
        summary["skipped"].append("card-not-found")
        return summary

    # (d) resolve recipients (C3 — the SSOT for who gets notified).
    from . import resolve_recipients

    try:
        recipients = set(resolve_recipients(event, card, store=store))
    except Exception as exc:  # noqa: BLE001 — a config error must not break emit
        logger.warning(
            "[scitex-todo._notify] resolve_recipients failed for %r event "
            "on card %r; skipping notify: %s",
            event_type, card_id, exc,
        )
        summary["skipped"].append("resolve-failed")
        return summary

    # (e) never notify the ACTOR (the cause of the event). The actor is a
    #     raw name; resolve it to an id the SAME way card members are
    #     resolved, then discard both the id and the raw name from the set.
    if actor:
        from .._users import resolve_user

        actor_user = resolve_user(actor, store=store)
        actor_id = actor_user.id if actor_user is not None else actor
        recipients.discard(actor_id)
        recipients.discard(actor)

    # (f) map each recipient id → a delivery NAME (dedup via a set).
    delivery_names: set[str] = set()
    for uid in recipients:
        name = _recipient_to_name(uid, store=store)
        if name:
            delivery_names.add(name)
        else:
            summary["skipped"].append(f"unmappable:{uid}")

    if not delivery_names:
        return summary

    # (g) deliver per name — FAIL-SOFT per recipient.
    if deliver_fn is None:
        from .._push import deliver as deliver_fn  # default real wire

    from .._push import NOTIFY_TIMEOUT_S

    body = _body_for(event_type, card_id, actor, extras)
    kind = f"notify:{event_type}"
    for name in sorted(delivery_names):
        try:
            result = deliver_fn(
                name,
                body,
                kind=kind,
                task_id=card_id,
                timeout=NOTIFY_TIMEOUT_S,
                dispatched_is_ok=False,
            )
        except Exception as exc:  # noqa: BLE001 — one bad delivery != stop all
            logger.warning(
                "[scitex-todo._notify] delivery to %r raised for %r event "
                "on card %r: %s",
                name, event_type, card_id, exc,
            )
            summary["errors"].append({"recipient": name, "error": str(exc)})
            continue
        # A wire that returns ok=False is a soft failure, not an exception —
        # record it but keep going (other recipients still get notified).
        if isinstance(result, Mapping) and result.get("ok") is False:
            summary["errors"].append(
                {"recipient": name, "error": result.get("reason", "not-ok")}
            )
            continue
        summary["delivered"].append(name)

    return summary


__all__ = ["dispatch_notifications"]

# EOF
