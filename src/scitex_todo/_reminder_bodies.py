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


def _card_line(sc) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    age = "" if sc.age_hours is None else f", ~{sc.age_hours:.0f}h"
    return f"  - {sc.id} [{sc.status}{age}] \"{title}\""


def _digest_body(cards: list, attempt: int) -> str:
    """One digest listing an owner's open stale cards; agent picks which to advance.

    Lists up to :data:`DIGEST_CARD_CAP` cards (oldest-first, as the detectors
    order them) with a "+K more" tail. The selection is intentionally LEFT TO
    THE AGENT — the digest surfaces the scoped list, it does not dictate a
    single "next card".
    """
    shown = cards[:DIGEST_CARD_CAP]
    lines = [_card_line(sc) for sc in shown]
    if len(cards) > DIGEST_CARD_CAP:
        lines.append(f"  - (+{len(cards) - DIGEST_CARD_CAP} more)")
    return (
        f"Assigned-card digest #{attempt}: you own {len(cards)} open card(s) "
        f"that need attention — decide which to advance now (work it, update "
        f"it, reassign, or close):\n" + "\n".join(lines)
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
