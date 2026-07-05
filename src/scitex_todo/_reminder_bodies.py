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

#: Digest occurrences (this one included) at/above which a card gets an
#: IGNORED flag — it has now survived this many due-digests without being
#: closed or touched. 3 gives an owner two clean passes before the harsher
#: framing kicks in.
IGNORED_REPEAT_THRESHOLD = 3


def _card_line(sc, seen_count: int | None = None) -> str:
    title = (sc.title or "").strip() or "(untitled)"
    prio = "" if sc.priority is None else f"P{sc.priority} "
    age = "" if sc.age_hours is None else f", ~{sc.age_hours:.0f}h"
    stale_flag = (
        _STALE_HINT_TAG
        if sc.age_hours is None or sc.age_hours >= STALE_HINT_HOURS
        else ""
    )
    ignored_flag = (
        f" [IGNORED {seen_count}x — appeared in {seen_count} digests "
        f"with no verdict]"
        if seen_count is not None and seen_count >= IGNORED_REPEAT_THRESHOLD
        else ""
    )
    return (
        f"  - {prio}{sc.id} [{sc.status}{age}]{stale_flag}{ignored_flag} "
        f"\"{title}\""
    )


#: The five verdicts a recipient must choose from for each digested card,
#: rendered as ``"CODE — instruction"`` lines under the digest header.
#: Order is deliberate: act on it, name the blocker, retire it, hand it
#: off, or push back on it — roughly most- to least-common outcome.
_VERDICT_LINES = (
    "  - WORKING — you are advancing it now.",
    "  - BLOCKED — state the blocker, link it correctly (set the "
    "depends_on edge to the blocking card), and comment on THAT card so "
    "its assignee is notified through the card's own history; remaining "
    "silent is not acceptable.",
    "  - OBSOLETE — close it. Cards marked LONG-UNTOUCHED below are "
    "especially likely to be obsolete, superseded, or drifted from "
    "current reality.",
    "  - REASSIGN — act on it now: update the assignee (update_task) or "
    "add a collaborator (set_collaborator); do not merely name who "
    "should take it.",
    "  - QUESTION — if the task itself seems unreasonable or wrong, "
    "comment on the card asking its creator directly instead of "
    "silently complying or silently skipping it.",
)


#: One-line pointer to the exact write tools, so the recipient does not have
#: to guess the API. ``pr_url`` / ``issue_url`` / ``job_id`` are named
#: explicitly so evidence (the PR, the tracking issue, the CI run) is linked
#: on the card itself, not left to live only in chat/channel history.
_TOOLS_LINE = (
    "Tools: update_task (status, assignee, pr_url, issue_url, job_id), "
    "set_edge (depends_on), comment_task (blocker/question/verdict/reason), "
    "set_collaborator (pull in help). Link the PR, tracking issue, and CI "
    "run on the card as you go — evidence belongs on the card, not only in "
    "chat."
)


def _digest_body(
    cards: list, attempt: int, *, seen_counts: dict[str, int] | None = None
) -> str:
    """One digest listing an owner's open stale cards; demands a real verdict.

    Lists up to :data:`DIGEST_CARD_CAP` cards (priority-then-oldest-first,
    as the detectors order them) with a "+K more" tail. Every card in this
    list is already UNBLOCKED — the caller (:mod:`_reminders`) excludes
    parked/blocked-on-dependency cards before this body is built — so the
    framing is deliberately direct: these are the recipient's move right
    now, and repeating "nothing new" across digests is read as the card
    being ignored, not as evidence it's fine. Cards untouched past
    :data:`STALE_HINT_HOURS` carry a long-untouched flag; cards whose
    ``seen_counts`` entry has reached :data:`IGNORED_REPEAT_THRESHOLD`
    (see :func:`_card_line`) carry an IGNORED flag — the two are
    independent signals (a card can be freshly created yet already
    digested 3x, or ancient but seen for the first time). Which card to
    work first is still the agent's call; giving each card an explicit
    verdict (:data:`_VERDICT_LINES`) is not.

    ``seen_counts`` maps ``card.id`` → how many due-digests (this one
    included) the card has now appeared in unresolved; the caller
    (:func:`scitex_todo._reminders.sweep_reminders`) tracks this in the
    reminder sidecar and resets it whenever the card leaves the stale set
    (closed or touched). ``None``/missing entries render with no flag.
    """
    shown = cards[:DIGEST_CARD_CAP]
    counts = seen_counts or {}
    lines = [_card_line(sc, counts.get(sc.id)) for sc in shown]
    if len(cards) > DIGEST_CARD_CAP:
        lines.append(f"  - (+{len(cards) - DIGEST_CARD_CAP} more)")
    return (
        f"Assigned-card digest #{attempt}: {len(cards)} card(s) require "
        f"your action now. All are unblocked — cards genuinely blocked by "
        f"a dependency are already excluded from this list.\n\n"
        f"Policy: act on every card below immediately unless it is "
        f"genuinely blocked. Repeating \"nothing new\" across digests is "
        f"not acceptable — it reads as neglect, not confirmation that all "
        f"is well. Any deferral requires a stated, commented reason; a "
        f"bare status change with no justification does not count. When "
        f"several cards are independent and resources allow, advance them "
        f"in parallel rather than one at a time.\n\n"
        f"For each card, record exactly one verdict:\n"
        + "\n".join(_VERDICT_LINES)
        + f"\n\n{_TOOLS_LINE}"
        + "\n\nCards:\n" + "\n".join(lines)
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
