#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stale-active card detector — pure, testable, no network / DOM.

A "stale-active" card is one that is structurally ACTIVE
(``status in {in_progress, blocked}``) yet has had no recent touch:
its ``last_activity`` (falling back to ``created_at``) is older than a
staleness threshold. These are the likely-forgotten cards — the work
the owner *said* they were doing but the board shows no movement on.

This replaces the manual "card-freshness campaign" the lead used to run
by hand: instead of a human scanning the board for stalled cards, the
existing ``*/10`` nudge cron (``print-stats --by agent --notify
--nudge-quiet``) sweeps for them and nudges each OWNER directly.

This module also hosts a SECOND, distinct detector for the PENDING
backlog (:func:`detect_pending_backlog`): owned ``status=pending`` cards
that have sat untouched longer than a (more lenient) threshold. Where
stale-active says "close/update the work you said you were doing",
pending-backlog says "start or triage the cards you accepted but never
began". Both are expressed in terms of one generic core
(:func:`_detect_owned_untouched`) so the owner-resolution, oldest-first
ordering, and missing-timestamp-is-stale semantics stay identical.

Vocabulary note (board doctrine): the board entity is the USER; an
agent is a user of ``kind=agent``. We group by the card's owner field
(``agent`` first, ``assignee`` fallback) — that owner IS the user the
nudge is addressed to.

Design
------
* Pure functions only. ``detect_stale_active`` takes the task list, a
  ``now`` datetime, and a threshold; it returns a mapping of owner →
  list of stale cards. No imports of ``_push`` / Django / network here
  so the core stays unit-testable with plain list-of-dicts inputs.
* Threshold default is :data:`DEFAULT_STALE_ACTIVE_HOURS` (2 h),
  env-overridable via :data:`ENV_STALE_ACTIVE_HOURS`. Deliberately
  shorter than the 24 h ``SCITEX_TODO_STALE_HOURS`` used for the stats
  ``stale_count`` pill — *active* cards (in_progress / blocked) should
  move on a tighter clock than the general open backlog.
* Timestamp parsing reuses :mod:`scitex_todo._throughput`'s lenient
  UTC-coercing ``_parse_iso`` so a single naive timestamp can't kill
  the sweep (the same failure that silently killed the notify cron — see
  ``_throughput._parse_iso`` docstring).
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass

from ._throughput import _now_utc, _parse_iso

#: Statuses that count as "active" — the owner is claiming live work.
STALE_ACTIVE_STATUSES = frozenset({"in_progress", "blocked"})

#: Env override + default for the staleness threshold (hours). 2 h is
#: tight on purpose: an in_progress/blocked card untouched for >2 h is
#: very likely forgotten, not mid-keystroke.
ENV_STALE_ACTIVE_HOURS = "SCITEX_TODO_STALE_ACTIVE_HOURS"
DEFAULT_STALE_ACTIVE_HOURS = 2.0

#: Statuses that count as BACKLOG — accepted but not yet started.
#:
#: ``deferred`` is the backlog state since ``pending`` was abolished
#: (2026-07-10). Until this was repointed the set was ``{"pending"}``, which
#: matches no card in the store — so the backlog nudge below fired for nobody
#: and 400+ deferred cards aged in total silence. "Someday" with no reminder
#: is just "never", written down.
BACKLOG_STATUSES = frozenset({"deferred"})

#: Deprecated alias, kept for out-of-tree importers.
PENDING_STATUSES = BACKLOG_STATUSES

#: Env override + default for the BACKLOG threshold (hours). 24 h is
#: deliberately MUCH more lenient than the 2 h stale-active clock: a deferred
#: card is work the owner consciously has not begun, so a forgotten one only
#: becomes worth a nudge after a full day of no triage / no start.
ENV_BACKLOG_NUDGE_HOURS = "SCITEX_TODO_BACKLOG_NUDGE_HOURS"
#: Deprecated alias for the env knob. Both names are honoured (see
#: ``_backlog_nudge_hours``) so existing crontabs keep working.
ENV_PENDING_NUDGE_HOURS = "SCITEX_TODO_PENDING_NUDGE_HOURS"
DEFAULT_PENDING_NUDGE_HOURS = 24.0
DEFAULT_BACKLOG_NUDGE_HOURS = DEFAULT_PENDING_NUDGE_HOURS

#: Cap on ids rendered per owner line so a runaway lane doesn't produce
#: a multi-kilobyte nudge body.
NUDGE_ID_CAP = 12


def _resolve_hours(
    explicit: float | None, env_name: str, default: float
) -> float:
    """Resolve a threshold: explicit arg > env override > default.

    The env override is read at CALL time (not import time) so a test or
    cron can flip it per-invocation. A non-numeric env value falls back
    to the default rather than raising into the sweep.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _stale_active_hours(stale_hours: float | None) -> float:
    """Resolve the stale-active threshold, honoring the env override."""
    return _resolve_hours(
        stale_hours, ENV_STALE_ACTIVE_HOURS, DEFAULT_STALE_ACTIVE_HOURS
    )


def _pending_nudge_hours(pending_hours: float | None) -> float:
    """Resolve the backlog threshold, honoring either env override.

    ``SCITEX_TODO_BACKLOG_NUDGE_HOURS`` is the current name;
    ``SCITEX_TODO_PENDING_NUDGE_HOURS`` still works so live crontabs written
    against the old name do not silently revert to the 24 h default.
    """
    if pending_hours is not None:
        return pending_hours
    if os.environ.get(ENV_BACKLOG_NUDGE_HOURS) is not None:
        return _resolve_hours(None, ENV_BACKLOG_NUDGE_HOURS, DEFAULT_BACKLOG_NUDGE_HOURS)
    return _resolve_hours(None, ENV_PENDING_NUDGE_HOURS, DEFAULT_BACKLOG_NUDGE_HOURS)


def _owner_of(task: dict) -> str:
    """The card's owner = the USER the nudge is addressed to.

    ``agent`` is the canonical owner field; ``assignee`` is the
    fallback for cards that predate the ``agent`` rename. Empty owner
    surfaces as ``"(unassigned)"`` so the gap is visible, never
    silently dropped (mirrors ``_throughput.aggregate``).
    """
    owner = (task.get("agent") or task.get("assignee") or "").strip()
    return owner or "(unassigned)"


def _age_hours(task: dict, now: _dt.datetime) -> float | None:
    """Hours since the card was last touched.

    ``last_activity`` is authoritative; ``created_at`` is the fallback
    for cards that have never been touched since creation. Returns
    ``None`` only when BOTH are missing/unparseable — such a card is
    treated as stale (we can't prove it's fresh).
    """
    ts = task.get("last_activity") or task.get("created_at")
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 3600.0


def is_stale_active(
    task: dict,
    *,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> bool:
    """True when ``task`` is active (in_progress / blocked) AND stale.

    Stale = age (``last_activity`` else ``created_at``) older than the
    threshold, OR no parseable timestamp at all (can't prove fresh).
    """
    if task.get("status") not in STALE_ACTIVE_STATUSES:
        return False
    cur = now or _now_utc()
    age = _age_hours(task, cur)
    if age is None:
        return True
    return age > _stale_active_hours(stale_hours)


@dataclass
class StaleCard:
    """One untouched card, with the bits a nudge line needs.

    Used for BOTH the stale-active and pending-backlog detectors — the
    shape (id / title / status / age) is identical; only the status set
    and threshold differ between the two.
    """

    id: str
    title: str
    status: str
    age_hours: float | None  # None when no parseable timestamp.


def _detect_owned_untouched(
    tasks: list[dict],
    *,
    statuses: frozenset[str],
    threshold_hours: float,
    now: _dt.datetime | None = None,
) -> dict[str, list[StaleCard]]:
    """Generic core: owned cards in ``statuses`` untouched > threshold.

    Returns ``{owner: [StaleCard, ...]}`` — only owners with at least
    one matching card appear (no empty rows). Within each owner the
    cards are sorted oldest-first (most-forgotten on top); cards with no
    parseable timestamp (age ``None``) sort first as maximally stale.

    Both :func:`detect_stale_active` and :func:`detect_pending_backlog`
    are thin wrappers over this so owner-resolution, ordering, and the
    missing-timestamp-is-stale rule stay identical between them.

    Pure: no env reads, no network — the caller resolves the threshold.
    """
    cur = now or _now_utc()
    out: dict[str, list[StaleCard]] = {}
    for t in tasks:
        if t.get("status") not in statuses:
            continue
        age = _age_hours(t, cur)
        if age is not None and age <= threshold_hours:
            continue  # fresh
        owner = _owner_of(t)
        out.setdefault(owner, []).append(
            StaleCard(
                id=str(t.get("id") or ""),
                title=str(t.get("title") or "(untitled)"),
                status=str(t.get("status") or "?"),
                age_hours=age,
            )
        )
    for cards in out.values():
        # Oldest-first; None (no timestamp) sorts ahead of any finite age.
        cards.sort(key=lambda c: (c.age_hours is not None, -(c.age_hours or 0.0)))
    return out


def detect_stale_active(
    tasks: list[dict],
    *,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> dict[str, list[StaleCard]]:
    """Group stale-active cards by OWNER.

    Returns ``{owner: [StaleCard, ...]}`` — only owners that have at
    least one stale-active card appear (no empty rows). Within each
    owner the cards are sorted oldest-first (most-forgotten on top);
    cards with no timestamp (age ``None``) sort first as maximally
    stale.

    Pure: no env reads beyond the threshold resolution, no network.
    """
    return _detect_owned_untouched(
        tasks,
        statuses=STALE_ACTIVE_STATUSES,
        threshold_hours=_stale_active_hours(stale_hours),
        now=now,
    )


def detect_pending_backlog(
    tasks: list[dict],
    *,
    now: _dt.datetime | None = None,
    pending_hours: float | None = None,
) -> dict[str, list[StaleCard]]:
    """Group untouched BACKLOG cards by OWNER.

    Mirrors :func:`detect_stale_active` but targets ``status=deferred``
    cards — work the owner accepted but never started — against the more
    lenient :data:`DEFAULT_BACKLOG_NUDGE_HOURS` threshold (env-overridable
    via :data:`ENV_BACKLOG_NUDGE_HOURS`). Same owner-resolution,
    oldest-first ordering, and missing-timestamp-is-stale semantics.

    This is the "you have untouched backlog" reminder, and it deliberately
    keeps its oldest-first ordering: it reports a fact. It is NOT the
    pick-for-action draw — that lives in :mod:`scitex_todo._backlog_triage`
    and weights toward RECENT cards, because handing an agent its oldest
    cards to work is handing it its least valuable ones.

    Pure: no env reads beyond the threshold resolution, no network.
    """
    return _detect_owned_untouched(
        tasks,
        statuses=PENDING_STATUSES,
        threshold_hours=_pending_nudge_hours(pending_hours),
        now=now,
    )


def stale_active_nudge_line(
    owner: str,
    cards: list[StaleCard],
    *,
    stale_hours: float | None = None,
) -> str:
    """Compose the concise per-owner nudge line.

    Shape (single line; caller wraps / delivers):

        STALE-ACTIVE: N stale cards (in_progress/blocked, untouched
        >Nh) — reconcile or update: <id>, <id>, …

    Ids are capped at :data:`NUDGE_ID_CAP` with a "+K more" tail so a
    runaway lane can't produce a huge body.
    """
    threshold = _stale_active_hours(stale_hours)
    thr = f"{threshold:g}"
    ids = [c.id for c in cards if c.id]
    shown = ids[:NUDGE_ID_CAP]
    tail = ""
    if len(ids) > NUDGE_ID_CAP:
        tail = f", +{len(ids) - NUDGE_ID_CAP} more"
    id_str = ", ".join(shown) + tail if shown else "(no ids)"
    return (
        f"STALE-ACTIVE: {len(cards)} stale card(s) "
        f"(in_progress/blocked, untouched >{thr}h) — "
        f"reconcile or update: {id_str}"
    )


def pending_backlog_nudge_line(
    owner: str,
    cards: list[StaleCard],
    *,
    pending_hours: float | None = None,
) -> str:
    """Compose the concise per-owner PENDING-backlog nudge line.

    Distinct wording from :func:`stale_active_nudge_line`: stale-active
    says "reconcile/update the work you said you were doing"; pending
    says "start or triage the cards you accepted but never began".

    Shape (single line; caller wraps / delivers):

        PENDING-BACKLOG: N untouched pending card(s) (>Nh) — start or
        triage (begin, re-prioritise, or close): <id>, <id>, …

    Ids are capped at :data:`NUDGE_ID_CAP` with a "+K more" tail so a
    runaway lane can't produce a huge body.
    """
    threshold = _pending_nudge_hours(pending_hours)
    thr = f"{threshold:g}"
    ids = [c.id for c in cards if c.id]
    shown = ids[:NUDGE_ID_CAP]
    tail = ""
    if len(ids) > NUDGE_ID_CAP:
        tail = f", +{len(ids) - NUDGE_ID_CAP} more"
    id_str = ", ".join(shown) + tail if shown else "(no ids)"
    return (
        f"PENDING-BACKLOG: {len(cards)} untouched pending card(s) "
        f"(>{thr}h) — start or triage "
        f"(begin, re-prioritise, or close): {id_str}"
    )


__all__ = [
    "STALE_ACTIVE_STATUSES",
    "PENDING_STATUSES",
    "BACKLOG_STATUSES",
    "ENV_BACKLOG_NUDGE_HOURS",
    "DEFAULT_BACKLOG_NUDGE_HOURS",
    "ENV_STALE_ACTIVE_HOURS",
    "DEFAULT_STALE_ACTIVE_HOURS",
    "ENV_PENDING_NUDGE_HOURS",
    "DEFAULT_PENDING_NUDGE_HOURS",
    "NUDGE_ID_CAP",
    "StaleCard",
    "is_stale_active",
    "detect_stale_active",
    "detect_pending_backlog",
    "stale_active_nudge_line",
    "pending_backlog_nudge_line",
]
