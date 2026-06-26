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

#: Cap on ids rendered per owner line so a runaway lane doesn't produce
#: a multi-kilobyte nudge body.
NUDGE_ID_CAP = 12


def _stale_active_hours(stale_hours: float | None) -> float:
    """Resolve the threshold, honoring the env override at call time."""
    if stale_hours is not None:
        return stale_hours
    raw = os.environ.get(ENV_STALE_ACTIVE_HOURS)
    if raw is None:
        return DEFAULT_STALE_ACTIVE_HOURS
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_STALE_ACTIVE_HOURS


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
    """One stale-active card, with the bits a nudge line needs."""

    id: str
    title: str
    status: str
    age_hours: float | None  # None when no parseable timestamp.


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
    cur = now or _now_utc()
    threshold = _stale_active_hours(stale_hours)
    out: dict[str, list[StaleCard]] = {}
    for t in tasks:
        if t.get("status") not in STALE_ACTIVE_STATUSES:
            continue
        age = _age_hours(t, cur)
        if age is not None and age <= threshold:
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


__all__ = [
    "STALE_ACTIVE_STATUSES",
    "ENV_STALE_ACTIVE_HOURS",
    "DEFAULT_STALE_ACTIVE_HOURS",
    "NUDGE_ID_CAP",
    "StaleCard",
    "is_stale_active",
    "detect_stale_active",
    "stale_active_nudge_line",
]
