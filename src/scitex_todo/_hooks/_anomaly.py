#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex_todo.hooks`` consumer for sac's liveness-tick anomaly events.

sac (scitex-agent-container) ships a liveness-tick reconciler that scans
OPEN cards and, when a card has NO declared blocker but its owner agent is
dead or idle-too-long, EMITS an anomaly event on the ``scitex_todo.hooks``
importlib entry-point group (no imports either direction — loose coupling;
the PRODUCER is sac PR #475). This module is the scitex-todo side CONSUMER
registered under that group so the fleet's stuck-card alarm actually lights
up instead of emitting into the void.

## The PINNED wire contract (do not change — match exactly)

sac loads consumers via
``importlib.metadata.entry_points(group="scitex_todo.hooks")`` and calls
each with ONE positional dict::

    {
        "agent": str,      # the (dead / idle) owner agent
        "card_id": str,    # the OPEN card with no declared blocker
        "reason": str,     # one of REASONS
        "severity": str,   # one of SEVERITIES
        "ts": float,       # unix epoch seconds
    }

``reason`` is one of :data:`REASONS`; ``severity`` is one of
:data:`SEVERITIES`. sac ALREADY dedups per ``(agent, card_id)`` within a
renotify cooldown, so this consumer gets at most one event per stuck
episode per cooldown — it does NOT re-implement dedup.

## What the consumer does (record-first, NON-destructive)

1. VALIDATE the dict (required keys present; ``reason`` / ``severity`` in
   their enums). A malformed event fails LOUD to the log but NEVER raises
   out of the function — the callable runs INSIDE sac's producer process,
   so an exception could break sac's emit loop. Every side-effect is
   wrapped in a try/except that logs loudly and swallows.
2. RECORD a durable COMMENT on the named card via the SAME internal store
   path the other built-in handlers use
   (:func:`scitex_todo._store.comment_task`). The comment is the durable
   anomaly trail; the card's ``status`` / blocker are LEFT UNCHANGED — a
   liveness anomaly must not silently re-status a card.
3. DRIVE the operator push by REUSING the existing notify rail
   (:func:`scitex_todo._notify._dispatch.dispatch_notifications`). Severity
   maps to an urgency TIER expressed through the rail's existing
   recipient-routing rules (:data:`scitex_todo._notify.DEFAULT_NOTIFY_RULES`)
   — see :data:`_SEVERITY_EVENT_TYPE`. No new push path is built.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Author stamped on the durable anomaly comment. A stable, recognisable
#: machine author (mirrors how the other built-in handlers stamp a non-human
#: ``by`` when the producer did not name one).
_ANOMALY_AUTHOR = "scitex-todo-liveness"

#: Comment ``kind`` tag so the board can render "how the card was routed"
#: (mirrors the ``push`` / ``unblock`` kinds the other handlers stamp).
_ANOMALY_COMMENT_KIND = "anomaly"

#: Closed set of accepted ``reason`` values (the pinned contract).
REASONS: frozenset[str] = frozenset({"owner-not-live", "owner-idle"})

#: Closed set of accepted ``severity`` values (the pinned contract).
SEVERITIES: frozenset[str] = frozenset({"warning", "critical"})

#: Required keys on the inbound event dict.
_REQUIRED_KEYS: tuple[str, ...] = ("agent", "card_id", "reason", "severity", "ts")


def _severity_event_type(severity: str) -> str:
    """Map an anomaly ``severity`` to the notify rail's urgency TIER.

    The notify rail (:mod:`scitex_todo._notify`) has no native
    urgency/phone field — its ONLY urgency lever is recipient BREADTH,
    encoded per card-event type in
    :data:`scitex_todo._notify.DEFAULT_NOTIFY_RULES`. We reuse that map
    rather than inventing a parallel one:

    * ``critical`` → :data:`~scitex_todo._events.EventType.REASSIGNED`,
      whose default rule routes to the card OWNER — the most-direct,
      phone-eligible recipient (the responsible agent's escalation tier).
    * ``warning``  → :data:`~scitex_todo._events.EventType.STATUS_CHANGED`,
      whose default rule routes to the card SUBSCRIBERS — the lower-signal
      telegram/email watcher tier.

    The chosen type drives ONLY the recipient set; the durable human
    record is the comment (step 2), and the anomaly detail rides in the
    event's ``extra`` so the rail's body is informative.
    """
    from .._events import EventType

    if severity == "critical":
        return EventType.REASSIGNED
    return EventType.STATUS_CHANGED


def _validate(event: Any) -> dict | None:
    """Fail-loud validation of the pinned anomaly dict.

    Returns the event dict on success; logs LOUD + returns ``None`` on any
    shape violation. NEVER raises — a malformed event must not propagate
    into sac's producer emit loop.
    """
    if not isinstance(event, dict):
        logger.error(
            "scitex_todo._hooks._anomaly: event must be a dict, got %r",
            type(event).__name__,
        )
        return None
    missing = [k for k in _REQUIRED_KEYS if k not in event]
    if missing:
        logger.error(
            "scitex_todo._hooks._anomaly: malformed event, missing keys %r "
            "(got keys %r)",
            missing,
            sorted(event.keys()),
        )
        return None
    reason = event.get("reason")
    if reason not in REASONS:
        logger.error(
            "scitex_todo._hooks._anomaly: malformed event, reason %r not in %r",
            reason,
            sorted(REASONS),
        )
        return None
    severity = event.get("severity")
    if severity not in SEVERITIES:
        logger.error(
            "scitex_todo._hooks._anomaly: malformed event, severity %r not in %r",
            severity,
            sorted(SEVERITIES),
        )
        return None
    agent = event.get("agent")
    card_id = event.get("card_id")
    if not isinstance(agent, str) or not agent:
        logger.error(
            "scitex_todo._hooks._anomaly: malformed event, 'agent' must be a "
            "non-empty string (got %r)",
            agent,
        )
        return None
    if not isinstance(card_id, str) or not card_id:
        logger.error(
            "scitex_todo._hooks._anomaly: malformed event, 'card_id' must be a "
            "non-empty string (got %r)",
            card_id,
        )
        return None
    return event


def _human_ts(ts: Any) -> str:
    """Render the unix-epoch ``ts`` as a UTC-ISO string (best-effort).

    Falls back to the raw repr if ``ts`` is not a number — the body is a
    human aid, never a parse target, so an odd ts must never break the
    record.
    """
    try:
        import datetime as _dt

        return (
            _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return repr(ts)


def _anomaly_body(agent: str, reason: str, severity: str, ts: Any) -> str:
    """Build the durable, human-readable anomaly line for the comment."""
    return (
        f"[anomaly] liveness: owner {agent!r} {reason} "
        f"(severity={severity}, at {_human_ts(ts)}) — card has no declared "
        f"blocker but its owner is not making progress."
    )


def consume_anomaly(event: dict) -> None:
    """``scitex_todo.hooks`` consumer for sac's liveness anomaly events.

    Record-first + NON-destructive: append a durable anomaly COMMENT to the
    named card (status / blocker UNCHANGED), then drive the operator push
    through the existing notify rail with an urgency tier mapped from
    ``severity``. NEVER raises — it runs inside sac's producer process, so
    every side-effect is wrapped in a try/except that logs loudly and
    swallows. Validation failures fail loud to the log and return early.

    Parameters
    ----------
    event : dict
        The pinned anomaly dict
        ``{"agent", "card_id", "reason", "severity", "ts"}``.
    """
    validated = _validate(event)
    if validated is None:
        return  # already logged loud in _validate

    agent = validated["agent"]
    card_id = validated["card_id"]
    reason = validated["reason"]
    severity = validated["severity"]
    ts = validated["ts"]

    # (1) RECORD — durable comment via the SAME internal store path the
    #     other built-in handlers use (record-first, non-destructive: append
    #     only, never mutate status/blocker). Wrapped fail-soft so a transient
    #     store error can never propagate into sac's emit loop.
    try:
        from .. import _store

        _store.comment_task(
            task_id=card_id,
            text=_anomaly_body(agent, reason, severity, ts),
            by=_ANOMALY_AUTHOR,
            kind=_ANOMALY_COMMENT_KIND,
        )
    except Exception:  # noqa: BLE001 — must never break sac's producer
        logger.error(
            "scitex_todo._hooks._anomaly: failed to record anomaly comment on "
            "card_id=%r (agent=%r reason=%r severity=%r)",
            card_id,
            agent,
            reason,
            severity,
            exc_info=True,
        )

    # (2) PUSH — drive the operator notification through the EXISTING notify
    #     rail. Severity → urgency tier via the rail's own recipient-routing
    #     rules (see _severity_event_type). The anomaly detail rides in
    #     `extra` so the rail's body is informative. Wrapped fail-soft.
    try:
        from .._events import Event
        from .._notify._dispatch import dispatch_notifications

        event_type = _severity_event_type(severity)
        card_event = Event(
            type=event_type,
            card_id=card_id,
            actor=None,  # the producer is the liveness reconciler, not a member
            extra={
                "anomaly": True,
                "anomaly_reason": reason,
                "anomaly_severity": severity,
                "anomaly_agent": agent,
                # status_changed body reads from/to — frame the anomaly as a
                # liveness observation so a `warning` push renders meaningfully.
                "from": "live",
                "to": reason,
            },
        )
        dispatch_notifications(card_event)
    except Exception:  # noqa: BLE001 — push must never break sac's producer
        logger.error(
            "scitex_todo._hooks._anomaly: failed to push anomaly notification "
            "for card_id=%r (agent=%r severity=%r)",
            card_id,
            agent,
            severity,
            exc_info=True,
        )


__all__ = ["REASONS", "SEVERITIES", "consume_anomaly"]

# EOF
