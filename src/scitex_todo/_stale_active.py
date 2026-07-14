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
from collections.abc import Callable
from dataclasses import dataclass

from ._throughput import _now_utc, _parse_iso

#: An extra row filter applied on top of the status filter — see
#: ``_detect_owned_untouched``'s ``where`` parameter.
_Predicate = Callable[[dict], bool]

#: Statuses that count as "active" — the owner is claiming live work.
STALE_ACTIVE_STATUSES = frozenset({"in_progress", "blocked"})

#: Blockers that put a card OUTSIDE its owner's control.
#:
#: The blocker enum exists precisely so that different blockers get different
#: signals — ``_model.VALID_BLOCKERS`` records the operator's pain verbatim:
#: "I cannot tell what is waiting on ME." This sweep used to ignore the
#: blocker entirely and nudge the OWNER every ``DEFAULT_STALE_ACTIVE_HOURS``
#: (2 h) about EVERY blocked card — including cards blocked on a dependency,
#: a compute job, another agent, or an operator decision. The owner cannot
#: move any of those. 12 identical nudges a day about work you are powerless
#: to advance is not a signal, it is training to ignore the channel — and a
#: channel that cries wolf is exactly how the REAL nudge gets missed. (Found
#: 2026-07-12: all 8 of scitex-todo's own "stale" cards were blocked on an
#: external blocker; not one was actionable.)
#:
#: So: a card blocked on one of these is NOT owner-stale on the tight clock.
#: It moves to the lenient ``blocked-check`` sweep below, whose question is
#: not "why have you abandoned this?" but "has your blocker cleared?"
#:
#: ``dep`` is the legacy alias of ``dependency`` (see ``_model._BLOCKER_ALIASES``);
#: both spellings are listed so a not-yet-normalized row is classified
#: correctly rather than falling through to the tight clock.
EXTERNAL_BLOCKERS = frozenset(
    {"compute", "dependency", "dep", "operator-decision", "agent-wait"}
)

#: Env override + default for the staleness threshold (hours). 2 h is
#: tight on purpose: an in_progress/blocked card untouched for >2 h is
#: very likely forgotten, not mid-keystroke.
ENV_STALE_ACTIVE_HOURS = "SCITEX_TODO_STALE_ACTIVE_HOURS"
DEFAULT_STALE_ACTIVE_HOURS = 2.0

#: Env override + default for the EXTERNALLY-BLOCKED re-check (hours).
#:
#: Deliberately as lenient as the backlog clock: the owner is legitimately
#: waiting, so the only thing worth asking is a periodic "is your blocker
#: still real?" — blockers DO go stale silently (the dependency shipped, the
#: compute job died, the operator answered elsewhere), and a card can rot for
#: weeks behind a blocker that cleared long ago. A daily check catches that
#: rot without the alert fatigue of the 2 h clock.
ENV_BLOCKED_NUDGE_HOURS = "SCITEX_TODO_BLOCKED_NUDGE_HOURS"
DEFAULT_BLOCKED_NUDGE_HOURS = 24.0

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


def _blocked_nudge_hours(blocked_hours: float | None) -> float:
    """Resolve the externally-blocked re-check threshold (env-overridable)."""
    return _resolve_hours(
        blocked_hours, ENV_BLOCKED_NUDGE_HOURS, DEFAULT_BLOCKED_NUDGE_HOURS
    )


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


def is_externally_blocked(task: dict) -> bool:
    """True when the card is blocked on something its OWNER cannot move.

    ``status == "blocked"`` AND ``blocker`` names an external blocker (a
    dependency, a compute job, another agent, or an operator decision).

    A blocked card with NO blocker named — or the explicit ``"none"`` —
    is NOT external: nobody has said what it is waiting for, and saying so
    IS the owner's job. That card stays on the tight clock.
    """
    if task.get("status") != "blocked":
        return False
    blocker = (task.get("blocker") or "").strip()
    return blocker in EXTERNAL_BLOCKERS


def is_owner_actionable(task: dict) -> bool:
    """True when the card is active AND its owner can actually move it.

    This is the predicate the TIGHT (2 h) nudge is allowed to fire on:
    ``in_progress`` work, or a ``blocked`` card whose blocker nobody has
    named. Everything blocked on a real external blocker is excluded —
    see :data:`EXTERNAL_BLOCKERS` for why nudging those is anti-signal.
    """
    if task.get("status") not in STALE_ACTIVE_STATUSES:
        return False
    return not is_externally_blocked(task)


def is_stale_active(
    task: dict,
    *,
    now: _dt.datetime | None = None,
    stale_hours: float | None = None,
) -> bool:
    """True when ``task`` is OWNER-ACTIONABLE and stale.

    Stale = age (``last_activity`` else ``created_at``) older than the
    threshold, OR no parseable timestamp at all (can't prove fresh).

    An externally-blocked card is never stale-active however old it is:
    its owner is waiting, not neglecting it. Those are reported by
    :func:`detect_blocked_external` on the lenient clock instead.
    """
    if not is_owner_actionable(task):
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
    # Priority drives digest RANKING (P1 before P2; None sorts last). Added so
    # the digest can lead with "act on THESE", not "here are 15 of your 98" —
    # a list of 98 is a list of 0. Defaulted for back-compat with any caller
    # constructing a StaleCard positionally.
    priority: int | None = None


def _detect_owned_untouched(
    tasks: list[dict],
    *,
    statuses: frozenset[str],
    threshold_hours: float,
    now: _dt.datetime | None = None,
    where: _Predicate | None = None,
) -> dict[str, list[StaleCard]]:
    """Generic core: owned cards in ``statuses`` untouched > threshold.

    Returns ``{owner: [StaleCard, ...]}`` — only owners with at least
    one matching card appear (no empty rows). Within each owner the
    cards are sorted oldest-first (most-forgotten on top); cards with no
    parseable timestamp (age ``None``) sort first as maximally stale.

    ``where`` is an optional extra predicate applied AFTER the status
    filter — used to split ``blocked`` rows between the tight
    owner-actionable sweep and the lenient externally-blocked one, so the
    two never double-report the same card.

    :func:`detect_stale_active`, :func:`detect_blocked_external` and
    :func:`detect_pending_backlog` are thin wrappers over this so
    owner-resolution, ordering, and the missing-timestamp-is-stale rule
    stay identical between them.

    Pure: no env reads, no network — the caller resolves the threshold.
    """
    cur = now or _now_utc()
    out: dict[str, list[StaleCard]] = {}
    for t in tasks:
        if t.get("status") not in statuses:
            continue
        if where is not None and not where(t):
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
                priority=t.get("priority") if isinstance(t.get("priority"), int) else None,
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
    """Group OWNER-ACTIONABLE stale cards by OWNER.

    Returns ``{owner: [StaleCard, ...]}`` — only owners that have at
    least one stale-active card appear (no empty rows). Within each
    owner the cards are sorted oldest-first (most-forgotten on top);
    cards with no timestamp (age ``None``) sort first as maximally
    stale.

    EXCLUDES externally-blocked cards (see :data:`EXTERNAL_BLOCKERS`):
    nudging an owner every 2 h about a card they are powerless to move is
    anti-signal. Those are reported by :func:`detect_blocked_external` on
    the lenient clock instead — the two sweeps partition the ``blocked``
    rows between them and never double-report a card.

    Pure: no env reads beyond the threshold resolution, no network.
    """
    return _detect_owned_untouched(
        tasks,
        statuses=STALE_ACTIVE_STATUSES,
        threshold_hours=_stale_active_hours(stale_hours),
        now=now,
        where=is_owner_actionable,
    )


def detect_blocked_external(
    tasks: list[dict],
    *,
    now: _dt.datetime | None = None,
    blocked_hours: float | None = None,
) -> dict[str, list[StaleCard]]:
    """Group long-externally-blocked cards by OWNER (the lenient sweep).

    The complement of :func:`detect_stale_active` over the ``blocked``
    rows: cards whose blocker is real and external, untouched for longer
    than the lenient threshold (default 24 h).

    The question this sweep asks is NOT "why have you abandoned this?"
    but "has your blocker cleared?" — blockers go stale silently (the
    dependency shipped, the compute job died, the operator answered
    somewhere else), and a card can rot for weeks behind one that lifted
    long ago. Nobody re-checks a blocker they set and forgot.

    Pure: no env reads beyond the threshold resolution, no network.
    """
    return _detect_owned_untouched(
        tasks,
        statuses=frozenset({"blocked"}),
        threshold_hours=_blocked_nudge_hours(blocked_hours),
        now=now,
        where=is_externally_blocked,
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

    PARKED cards are skipped (:func:`_backlog_triage.park_reason`): a card that
    states WHY it is deliberately standing is not backlog nobody got to, and
    nudging it is unanswerable by construction — there is nothing to start and
    no gate to clear, so "untouched" is its steady state. An alarm that cannot
    be satisfied is one its reader learns to discard, and it takes the genuinely
    abandoned cards down with it.

    THE SKIP IS DELIBERATELY NARROW — this sweep ONLY. It is NOT applied in
    :func:`_detect_owned_untouched`, even though that would be one tidier line,
    because the same core drives the stale-active sweep over ``in_progress`` /
    ``blocked`` cards. Honouring ``parked`` there would let an agent park a card
    it claims to be WORKING and silence the abandonment guard — and a claimed,
    silenced, untouched card is the exact incident the board exists to prevent.
    You may park work you are NOT doing. You may not park work you say you ARE.

    Pure: no env reads beyond the threshold resolution, no network.
    """
    from ._backlog_triage import is_parked

    return _detect_owned_untouched(
        tasks,
        statuses=PENDING_STATUSES,
        threshold_hours=_pending_nudge_hours(pending_hours),
        now=now,
        where=lambda t: not is_parked(t),
    )


# ---------------------------------------------------------------------------
# Nudge-line composition lives in ``_stale_active_lines`` (split out when the
# third sweep pushed this module past the line limit). Re-exported here so
# every existing importer — ``_stale_active_nudge``, notifyd, the CLI, the
# tests — keeps working against the original import path. The split is an
# internal reorganisation, not an API break.
#
# Imported at the BOTTOM, after the detectors and threshold resolvers this
# module defines, because the composers import those back (they render the
# resolved threshold into the line). Top-of-file would be a circular import.
from ._stale_active_lines import (  # noqa: E402,F401  (re-export)
    NUDGE_ID_CAP,
    blocked_external_nudge_line,
    pending_backlog_nudge_line,
    stale_active_nudge_line,
)

__all__ = [
    # Policy / detection.
    "STALE_ACTIVE_STATUSES",
    "EXTERNAL_BLOCKERS",
    "BACKLOG_STATUSES",
    "PENDING_STATUSES",
    "StaleCard",
    "is_stale_active",
    "is_owner_actionable",
    "is_externally_blocked",
    "detect_stale_active",
    "detect_blocked_external",
    "detect_pending_backlog",
    # Thresholds.
    "ENV_STALE_ACTIVE_HOURS",
    "DEFAULT_STALE_ACTIVE_HOURS",
    "ENV_BLOCKED_NUDGE_HOURS",
    "DEFAULT_BLOCKED_NUDGE_HOURS",
    "ENV_BACKLOG_NUDGE_HOURS",
    "ENV_PENDING_NUDGE_HOURS",
    "DEFAULT_BACKLOG_NUDGE_HOURS",
    "DEFAULT_PENDING_NUDGE_HOURS",
    # Presentation (re-exported from _stale_active_lines).
    "NUDGE_ID_CAP",
    "stale_active_nudge_line",
    "blocked_external_nudge_line",
    "pending_backlog_nudge_line",
]
