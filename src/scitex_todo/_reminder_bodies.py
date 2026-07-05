#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure message-body formatters for the reminder engine.

Extracted from :mod:`scitex_todo._reminders` (which stays the sweep state
machine) so the human-readable NOTE BODY strings live in one focused, pure
module — no engine state, no I/O, just ``StaleCard`` → text. The engine
imports these back; the public API is unchanged.
"""

from __future__ import annotations

#: Max cards listed in one digest body; a runaway lane gets a "+K more" tail
#: instead of a multi-kilobyte note.
DIGEST_CARD_CAP = 15

#: Age threshold (hours) above which a listed card gets a long-untouched
#: flag. A card nobody has touched in this long is disproportionately
#: likely to be obsolete, superseded by other work, or drifted from
#: reality — the flag nudges the agent to actually check that, rather than
#: reflexively re-affirming it as still-relevant. 168h = 7 days.
STALE_HINT_HOURS = 168.0

#: Appended to a card line whose age (or an unparseable/missing timestamp)
#: clears :data:`STALE_HINT_HOURS`.
_STALE_HINT_TAG = " [LONG-UNTOUCHED — check obsolete/superseded/drifted]"


def _card_line(sc) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    age = "" if sc.age_hours is None else f", ~{sc.age_hours:.0f}h"
    flag = (
        _STALE_HINT_TAG
        if sc.age_hours is None or sc.age_hours >= STALE_HINT_HOURS
        else ""
    )
    return f"  - {sc.id} [{sc.status}{age}]{flag} \"{title}\""


def _digest_body(cards: list, attempt: int) -> str:
    """One digest listing an owner's open stale cards; demands a real verdict.

    Lists up to :data:`DIGEST_CARD_CAP` cards (oldest-first, as the detectors
    order them) with a "+K more" tail. Every card in this list is already
    UNBLOCKED — the caller (:mod:`_reminders`) excludes parked/blocked-on-
    dependency cards before this body is built — so the framing is
    deliberately blunt: these are the recipient's move RIGHT NOW, and a
    repeated "nothing new" acknowledgement across digests is treated as the
    card being ignored, not as evidence it's fine. Cards untouched past
    :data:`STALE_HINT_HOURS` carry a long-untouched flag (see
    :func:`_card_line`) since staleness that extreme is disproportionately
    likely to mean the card is obsolete/superseded/drifted, not merely
    delayed. The specific card SELECTED to work first is still left to the
    agent; what's no longer optional is giving each card an explicit
    verdict. When completing a card needs another agent's action, waiting
    silently is not progress — the owner is expected to a2a-nudge that
    agent directly, per the constitution's "ownership never dangles" rule.
    """
    shown = cards[:DIGEST_CARD_CAP]
    lines = [_card_line(sc) for sc in shown]
    if len(cards) > DIGEST_CARD_CAP:
        lines.append(f"  - (+{len(cards) - DIGEST_CARD_CAP} more)")
    return (
        f"Assigned-card digest #{attempt}: {len(cards)} card(s) below are YOUR "
        f"MOVE right now — all are unblocked (parked/blocked-on-dependency "
        f"cards are already excluded from this list). Rule: tackle each one "
        f"immediately unless it is genuinely blocked; \"nothing new, no "
        f"action taken\" is NOT a valid response to a repeated digest. For "
        f"EACH card give one verdict — WORKING (doing it now), BLOCKED (say "
        f"on what — and a2a-nudge whichever agent needs to act, don't just "
        f"wait silently), OBSOLETE (close it — especially likely for cards "
        f"marked LONG-UNTOUCHED below, since drift/supersession is common "
        f"over that long), or REASSIGN (don't just name who — actually "
        f"update_task the assignee, or set_collaborator to pull in help, "
        f"right now). Deferring ANY card (BLOCKED or otherwise leaving it "
        f"pending) requires a stated reason, comment it — a bare status "
        f"with no justification is not acceptable:\n" + "\n".join(lines)
    )


def _escalation_body(sc, owner: str, count: int) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    return (
        f"ESCALATION: high-priority card {sc.id} owned by {owner} has been "
        f"digested {count}x and is still {sc.status} and untouched. Needs "
        f"attention. \"{title}\""
    )


def _humanize_age(age_seconds: "int | None") -> str:
    """Compact 'how long since last_seen' for a creator-escalation body.

    ``None`` (owner never seen → liveness ``"unknown"``) reads as "never
    seen"; otherwise the largest sensible unit (min / h / d). Pure + total.
    """
    if age_seconds is None:
        return "never seen"
    if age_seconds < 3600:
        return f"~{max(age_seconds // 60, 0)}m ago"
    if age_seconds < 86400:
        return f"~{age_seconds // 3600}h ago"
    return f"~{age_seconds // 86400}d ago"


def _creator_escalation_body(sc, owner: str, age_seconds: "int | None") -> str:
    """A liveness-triggered nudge to a stuck card's CREATOR.

    Names the card, its DEAD owner + how long since ``last_seen``, and the
    ask: the assignee isn't running, so reassign / drive / close.
    """
    title = (sc.title or "").strip() or "(untitled)"
    seen = _humanize_age(age_seconds)
    return (
        f"CREATOR ESCALATION: card {sc.id} is still {sc.status} but its "
        f"assignee {owner} is not running (last seen {seen}) — they will not "
        f"pick it up. As the creator: reassign it, drive it yourself, or "
        f"close it. \"{title}\""
    )


__all__ = [
    "DIGEST_CARD_CAP",
    "_card_line",
    "_creator_escalation_body",
    "_digest_body",
    "_escalation_body",
    "_humanize_age",
]

# EOF
