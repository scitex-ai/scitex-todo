#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag-until-closed reminder engine + operator escalation.

The board is the fleet's direction system, so a requested-and-carded task
that silently stops progressing is a real incident (operator, 2026-06-30:
"register ALL manuscript claims in clew" stalled at 6/17 and was found by
hand). This engine forces the board to keep surfacing such cards until they
are closed — and to ESCALATE the worst ones to the operator.

What it does each sweep
-----------------------
1. Detect the cards that need nagging — the SAME pure detectors the stats
   cron uses (:func:`scitex_todo._stale_active.detect_stale_active` for
   ``in_progress``/``blocked`` untouched cards, and
   :func:`~scitex_todo._stale_active.detect_pending_backlog` for accepted-
   but-unstarted ``pending`` cards), grouped by owner.
2. Decide, PER CARD, whether a reminder is *due* now — using an ESCALATING
   cadence (the longer a card is ignored, the… no: the MORE times we've
   already nagged, the longer we wait, capped) tracked in a sidecar so we
   do not re-nag every tick. A card that the owner CLOSES or TOUCHES drops
   out of the stale set on its own, so the nag naturally stops — that is
   the "until closed" guarantee, and a plain touch acts as an ack.
3. Enqueue each due reminder into the owner's inbox via the standalone
   delivery rail (:func:`scitex_todo._inbox.enqueue`) — NOT the dead
   turn-URL push the old ``_stale_active_nudge`` used. notifyd then delivers
   it (terminal channel / Telegram).
4. ESCALATE: a HIGH-PRIORITY card still stale after
   :data:`DEFAULT_ESCALATE_AFTER` reminders also enqueues a rising-urgency
   notice to the OPERATOR, once, until the card leaves the stale set.

Design
------
* The reminder STATE lives in a sidecar ``reminders.yaml`` next to
  ``tasks.yaml`` (same SoC as ``notify.yaml`` / ``recipients.yaml``) — the
  card payloads are never mutated, so the detectors stay pure and the
  620-card board stays clean. State keyed by ``card_id``:
  ``{count, last_at, escalated}``. Entries for cards no longer stale are
  pruned every sweep (bounded growth).
* Recipient keying matches the producer/dispatch convention exactly
  (:func:`scitex_todo._notify._resolver._resolve_name_to_id`): a registered
  owner name resolves to its stable user-id, else the raw name — so the
  reminder lands on the SAME key the channel/notifyd drain.
* Pure-ish + injectable: ``enqueue``, ``resolve_key`` and ``now`` are
  injectable so tests drive the cadence/escalation state machine with real
  fakes (no mocks, no network, no clock).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Any, Callable

from ._stale_active import detect_pending_backlog, detect_stale_active
from ._throughput import _now_utc, _parse_iso

logger = logging.getLogger(__name__)

#: Sidecar file (sibling of ``tasks.yaml``) holding per-card reminder state.
REMINDER_SIDECAR_NAME = "reminders.yaml"

#: First re-nag interval (hours): how long after a reminder before the next.
ENV_REMINDER_BASE_HOURS = "SCITEX_TODO_REMINDER_BASE_HOURS"
DEFAULT_REMINDER_BASE_HOURS = 2.0

#: Cap on the (escalating) re-nag interval so it never goes silent for long.
ENV_REMINDER_MAX_HOURS = "SCITEX_TODO_REMINDER_MAX_HOURS"
DEFAULT_REMINDER_MAX_HOURS = 24.0

#: Reminders to an owner before a HIGH-PRIORITY card also escalates to the
#: operator. Escalation fires once per stale streak (reset when the card
#: leaves the stale set).
ENV_ESCALATE_AFTER = "SCITEX_TODO_REMINDER_ESCALATE_AFTER"
DEFAULT_ESCALATE_AFTER = 3

#: A card with ``priority <= this`` is "high priority" (lower int = higher
#: priority, matching the card model). Cards without a priority never
#: escalate (only the owner is nagged).
ENV_ESCALATE_PRIORITY = "SCITEX_TODO_REMINDER_ESCALATE_PRIORITY"
DEFAULT_ESCALATE_PRIORITY = 1

#: The operator identity escalations are addressed to (resolved like any
#: other recipient). Delivery to Telegram is operator-gated config
#: (recipients.yaml + token); the engine only enqueues.
ENV_OPERATOR = "SCITEX_TODO_OPERATOR"
DEFAULT_OPERATOR = "operator"

#: Event types (also the inbox dedup discriminator + the ledger key prefix).
EVENT_REMINDER = "reminder"
EVENT_ESCALATION = "escalation"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def reminder_interval_hours(count: int, *, base: float, cap: float) -> float:
    """Escalating gap before the next reminder, given prior ``count`` sent.

    Exponential backoff on the NUDGE cadence: each successive reminder waits
    longer (``base * 2**(count-1)``) up to ``cap`` — so a freshly-stale card
    is nagged promptly, a chronically-ignored one is not spammed every tick
    but never goes silent for more than ``cap`` hours. ``count<=0`` means
    "never reminded" → due immediately (interval 0).
    """
    if count <= 0:
        return 0.0
    return min(base * (2.0 ** (count - 1)), cap)


def _due(last_at: str | None, count: int, now: _dt.datetime, *, base: float, cap: float) -> bool:
    """True when a reminder is due: never sent, or the interval has elapsed."""
    if not last_at:
        return True
    parsed = _parse_iso(last_at)
    if parsed is None:
        return True
    elapsed_h = (now - parsed).total_seconds() / 3600.0
    return elapsed_h >= reminder_interval_hours(count, base=base, cap=cap)


def _sidecar_path(store: str | Path | None) -> Path:
    """``reminders.yaml`` sibling of the resolved task store."""
    from ._paths import resolve_tasks_path

    tasks_path = Path(resolve_tasks_path(store))
    return tasks_path.parent / REMINDER_SIDECAR_NAME


def load_reminder_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the per-card reminder sidecar (``{card_id: {...}}``).

    Missing / unreadable / malformed sidecar → empty state (fail-soft: a bad
    sidecar must never break a sweep). Returns a plain dict.
    """
    import yaml

    path = _sidecar_path(store)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("reminders: cannot read %s: %s", path, exc)
        return {}
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("reminders: malformed %s: %s", path, exc)
        return {}
    cards = data.get("cards") if isinstance(data, dict) else None
    return cards if isinstance(cards, dict) else {}


def save_reminder_state(state: dict[str, dict], store: str | Path | None = None) -> None:
    """Atomically persist the reminder sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump({"cards": state}, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("reminders: cannot write %s: %s", path, exc)


def _iso(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _card_priority(card: dict) -> int | None:
    p = card.get("priority")
    return p if isinstance(p, int) else None


def _is_high_priority(card: dict, *, threshold: int) -> bool:
    p = _card_priority(card)
    return p is not None and p <= threshold


def sweep_reminders(
    tasks: list[dict],
    *,
    store: str | Path | None = None,
    now: _dt.datetime | None = None,
    enqueue: Callable[..., Any] | None = None,
    resolve_key: Callable[[str], str] | None = None,
    operator: str | None = None,
    base_hours: float | None = None,
    cap_hours: float | None = None,
    escalate_after: int | None = None,
    escalate_priority: int | None = None,
) -> dict[str, list[str]]:
    """One nag sweep: enqueue due reminders + escalate high-priority overdue.

    Returns ``{"reminded": [card_id, ...], "escalated": [card_id, ...],
    "skipped": [card_id, ...]}`` (skipped = stale but not yet due).

    Fail-soft: a per-card enqueue error is logged and the sweep continues.
    Never raises into the caller (notifyd tick).
    """
    cur = now or _now_utc()
    base = base_hours if base_hours is not None else _env_float(
        ENV_REMINDER_BASE_HOURS, DEFAULT_REMINDER_BASE_HOURS
    )
    cap = cap_hours if cap_hours is not None else _env_float(
        ENV_REMINDER_MAX_HOURS, DEFAULT_REMINDER_MAX_HOURS
    )
    esc_after = escalate_after if escalate_after is not None else _env_int(
        ENV_ESCALATE_AFTER, DEFAULT_ESCALATE_AFTER
    )
    esc_prio = escalate_priority if escalate_priority is not None else _env_int(
        ENV_ESCALATE_PRIORITY, DEFAULT_ESCALATE_PRIORITY
    )
    operator_name = operator or os.environ.get(ENV_OPERATOR, DEFAULT_OPERATOR)

    if enqueue is None:
        from ._inbox import enqueue as enqueue  # type: ignore[no-redef]
    if resolve_key is None:
        from ._notify._resolver import _resolve_name_to_id

        def resolve_key(name: str) -> str:  # type: ignore[misc]
            return _resolve_name_to_id(name, store=store)

    # Index cards by id for priority/title lookup during escalation.
    by_id = {str(t.get("id") or ""): t for t in tasks if t.get("id")}

    # The set of (owner, StaleCard) needing a nag this sweep.
    buckets: dict[str, list] = {}
    for owner, cards in detect_stale_active(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)
    for owner, cards in detect_pending_backlog(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)

    state = load_reminder_state(store)
    stale_ids: set[str] = set()
    reminded: list[str] = []
    escalated: list[str] = []
    skipped: list[str] = []

    for owner in sorted(buckets):
        if owner == "(unassigned)":
            continue  # nobody to nag; the gap is surfaced by the stats sweep
        owner_key = _safe_resolve(resolve_key, owner)
        for sc in buckets[owner]:
            cid = sc.id
            if not cid:
                continue
            stale_ids.add(cid)
            entry = state.get(cid) or {}
            count = int(entry.get("count") or 0)
            if not _due(entry.get("last_at"), count, cur, base=base, cap=cap):
                skipped.append(cid)
                continue
            body = _reminder_body(sc, count + 1)
            if _safe_enqueue(
                enqueue, owner_key, EVENT_REMINDER, cid, body, cur, store
            ):
                count += 1
                entry["count"] = count
                entry["last_at"] = _iso(cur)
                reminded.append(cid)
            # Escalate a high-priority card that has been nagged enough and
            # has not yet escalated this stale streak.
            card = by_id.get(cid, {})
            if (
                count >= esc_after
                and not entry.get("escalated")
                and _is_high_priority(card, threshold=esc_prio)
            ):
                op_key = _safe_resolve(resolve_key, operator_name)
                ebody = _escalation_body(sc, owner, count)
                if _safe_enqueue(
                    enqueue, op_key, EVENT_ESCALATION, cid, ebody, cur, store
                ):
                    entry["escalated"] = True
                    escalated.append(cid)
            state[cid] = entry

    # Prune entries for cards that are no longer stale — the nag STOPS when a
    # card is closed or touched (it drops out of the stale set), and its
    # escalation resets so a future stall re-escalates.
    for cid in list(state):
        if cid not in stale_ids:
            del state[cid]

    save_reminder_state(state, store)
    return {"reminded": reminded, "escalated": escalated, "skipped": skipped}


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
) -> bool:
    """Enqueue one notification; fail-soft. Returns True on a real enqueue.

    ``ts`` is the reminder instant so each re-nag is a DISTINCT inbox record
    (the inbox dedups on ``(event_type, card_id, ts, actor)``); a new
    reminder at a new time is genuinely new, not a duplicate.
    """
    try:
        rec = enqueue(
            recipient_key,
            event_type=event_type,
            card_id=card_id,
            body=body,
            actor="notifyd",
            ts=_iso(now),
            store=store,
        )
        return rec is not None
    except Exception as exc:  # noqa: BLE001 — one bad enqueue must not abort the sweep
        logger.warning(
            "reminders: enqueue %s for %s to %s failed: %s",
            event_type, card_id, recipient_key, exc,
        )
        return False


def _reminder_body(sc, attempt: int) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    age = "" if sc.age_hours is None else f", untouched ~{sc.age_hours:.0f}h"
    return (
        f"Reminder #{attempt}: your card {sc.id} ({sc.status}{age}) is still "
        f"open — close it, update it, or reassign it. \"{title}\""
    )


def _escalation_body(sc, owner: str, count: int) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    return (
        f"ESCALATION: high-priority card {sc.id} owned by {owner} has been "
        f"nagged {count}x and is still {sc.status} and untouched. Needs "
        f"attention. \"{title}\""
    )


__all__ = [
    "REMINDER_SIDECAR_NAME",
    "EVENT_REMINDER",
    "EVENT_ESCALATION",
    "DEFAULT_REMINDER_BASE_HOURS",
    "DEFAULT_REMINDER_MAX_HOURS",
    "DEFAULT_ESCALATE_AFTER",
    "DEFAULT_ESCALATE_PRIORITY",
    "DEFAULT_OPERATOR",
    "reminder_interval_hours",
    "load_reminder_state",
    "save_reminder_state",
    "sweep_reminders",
]

# EOF
