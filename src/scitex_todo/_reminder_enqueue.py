#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-soft dispatch helpers for the reminder engine (:mod:`_reminders`).

Extracted from :mod:`scitex_todo._reminders` to keep that orchestrator within
the file-size budget, matching the sibling ``_reminder_bodies`` /
``_reminder_liveness`` split. These two helpers wrap recipient-key resolution
and the standalone inbox enqueue so one bad resolution/enqueue is LOGGED and
skipped — never aborting the whole notifyd sweep.
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _iso(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_resolve(resolve_key: Callable[[str], str], name: str) -> str:
    try:
        return resolve_key(name) or name
    except Exception as exc:  # noqa: BLE001 — resolution must not break the sweep
        logger.warning("reminders: key resolution for %r failed: %s", name, exc)
        return name


def _safe_enqueue(
    enqueue: Callable[..., Any],
    recipient_key: str,
    event_type: str,
    card_id: str,
    body: str,
    now: _dt.datetime,
    store: str | Path | None,
    *,
    supersede: bool = False,
) -> bool:
    """Enqueue one notification; fail-soft. Returns True on a real enqueue.

    ``ts`` is the sweep instant so each re-nag is a DISTINCT inbox record (the
    inbox dedups on ``(event_type, card_id, ts, actor)``).

    ``supersede`` is forwarded to :func:`scitex_todo._inbox.enqueue`. It is set
    ONLY for the cumulative owner digest (``EVENT_DIGEST`` / ``DIGEST_CARD_ID``):
    a digest is a full point-in-time snapshot, so a fresh one strictly replaces
    any unseen predecessor — the recipient never accumulates a replay-storm of
    stale digests. Per-card events (escalation / creator_escalation) are each
    DISTINCT and are enqueued with ``supersede=False`` (the default).
    """
    try:
        rec = enqueue(
            recipient_key,
            event_type=event_type,
            card_id=card_id,
            body=body,
            actor="notifyd",
            ts=_iso(now),
            supersede=supersede,
            store=store,
        )
        return rec is not None
    except Exception as exc:  # noqa: BLE001 — one bad enqueue must not abort the sweep
        logger.warning(
            "reminders: enqueue %s for %s to %s failed: %s",
            event_type, card_id, recipient_key, exc,
        )
        return False


__all__ = ["_iso", "_safe_enqueue", "_safe_resolve"]

# EOF
