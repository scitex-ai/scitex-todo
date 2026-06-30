#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag-until-closed reminder engine — per-owner digest + operator escalation.

The board is the fleet's direction system, so a requested-and-carded task
that silently stops progressing is a real incident (operator, 2026-06-30:
"register ALL manuscript claims in clew" stalled at 6/17 and was found by
hand). This engine forces the board to keep surfacing such cards until they
are closed — and to ESCALATE the worst ones to the operator.

Why a DIGEST, not per-card reminders
------------------------------------
The first cut nagged once PER stale card, which produced a wall of near-
identical "Reminder #N: card X…" lines whenever an owner had many open
cards — noise that trains the owner to ignore the whole stream. The
operator's refinement (2026-06-30, via NeuroVista): do NOT push a single
rigid "next card" either. Instead, present the owner's ASSIGNED-CARD LIST
as ONE digest and let the agent decide AGENTICALLY which to advance —
priority is dynamic and lives in the agent's head, not in a fixed per-card
schedule. So the MECHANICAL part is: (a) detect an owner has open assigned
cards, (b) periodically surface that scoped list; the SELECTION stays
agentic.

What it does each sweep
-----------------------
1. Detect the cards that need nagging — the SAME pure detectors the stats
   cron uses (:func:`scitex_todo._stale_active.detect_stale_active` for
   ``in_progress``/``blocked`` untouched cards, and
   :func:`~scitex_todo._stale_active.detect_pending_backlog` for accepted-
   but-unstarted ``pending`` cards), grouped by owner.
2. Decide, PER OWNER, whether a digest is *due* now — using an ESCALATING
   cadence (the more times we've already digested an owner, the longer we
   wait, capped) tracked in a sidecar so we do not re-nag every tick. When
   an owner closes/touches their cards they drop out of the stale set and
   the digest naturally stops — the "until closed" guarantee.
3. Enqueue ONE digest per due owner into that owner's inbox via the
   standalone delivery rail (:func:`scitex_todo._inbox.enqueue`). The
   digest lists the owner's open stale cards (capped) so the agent picks
   which to advance.
4. ESCALATE: a HIGH-PRIORITY card still stale after
   :data:`DEFAULT_ESCALATE_AFTER` digests to its owner also enqueues a
   rising-urgency notice to the OPERATOR, once per card, until the card
   leaves the stale set. Escalation stays PER CARD (the operator needs the
   specific stuck card, not a digest).

Design
------
* The reminder STATE lives in a sidecar ``reminders.yaml`` next to
  ``tasks.yaml`` (same SoC as ``notify.yaml`` / ``recipients.yaml``) — the
  card payloads are never mutated, so the detectors stay pure and the
  620-card board stays clean. Two sections:
  ``owners: {owner_name: {count, last_at}}`` (the per-owner digest cadence)
  and ``cards: {card_id: {escalated}}`` (the per-card escalation latch).
  Owner entries are pruned when the owner has no stale cards; card entries
  are pruned when the card leaves the stale set (so a future stall
  re-escalates).
* Recipient keying matches the producer/dispatch convention exactly
  (:func:`scitex_todo._notify._resolver._resolve_name_to_id`): a registered
  owner name resolves to its stable user-id, else the raw name — so the
  digest lands on the SAME key the channel/notifyd drain.
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

#: Sidecar file (sibling of ``tasks.yaml``) holding the reminder state.
REMINDER_SIDECAR_NAME = "reminders.yaml"

#: Digests to an owner before a HIGH-PRIORITY card also escalates to the
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

#: Optional owner ALLOWLIST for a phased rollout. Comma-separated owner
#: names; when set, ONLY those owners are nagged (every other owner is left
#: untouched). Empty / unset = nag every owner. Lets the engine start scoped
#: to one agent and widen deliberately, with no fleet-wide first-sweep storm.
ENV_REMINDER_OWNERS = "SCITEX_TODO_REMINDER_OWNERS"

#: Max cards listed in one digest body; a runaway lane gets a "+K more" tail
#: instead of a multi-kilobyte note.
DIGEST_CARD_CAP = 15

#: Event types (also the inbox dedup discriminator + the ledger key prefix).
EVENT_DIGEST = "reminder"
EVENT_ESCALATION = "escalation"

#: Synthetic ``card_id`` for a digest record (a digest is about many cards,
#: not one). One digest per owner per sweep + a fresh ``ts`` keeps the inbox
#: dedup ``(event_type, card_id, ts, actor)`` distinct across sweeps.
DIGEST_CARD_ID = "(digest)"


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    """Read an int knob from the ``reminders:`` config section, else default."""
    raw = cfg.get(key)
    if isinstance(raw, bool):  # bool is an int subclass — never a knob value
        return default
    return raw if isinstance(raw, int) else default


def _owner_allowlist(cfg: dict | None = None) -> set[str]:
    """Owner ALLOWLIST for a phased rollout (empty = nag every owner).

    Env :data:`ENV_REMINDER_OWNERS` (comma-separated) wins so the agent spec
    can scope the sweep; otherwise the ``reminders.owners`` config list
    (``[name, ...]``) applies. Either way an empty result means "all owners".
    """
    raw = os.environ.get(ENV_REMINDER_OWNERS)
    if raw is not None:
        return {o.strip() for o in raw.split(",") if o.strip()}
    owners = (cfg or {}).get("owners")
    if isinstance(owners, (list, tuple)):
        return {str(o).strip() for o in owners if str(o).strip()}
    return set()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _due(last_at: str | None, now: _dt.datetime, *, interval_minutes: float) -> bool:
    """True when a digest is due: never sent, or the flat interval has elapsed.

    Flat cadence (operator knob): an owner is re-digested every
    ``interval_minutes`` for as long as they hold open stale cards. No
    backoff — the gap is a single configured value (default 5 min), tightenable
    per card. ``last_at`` missing/unparseable → due now.
    """
    if not last_at:
        return True
    parsed = _parse_iso(last_at)
    if parsed is None:
        return True
    elapsed_min = (now - parsed).total_seconds() / 60.0
    return elapsed_min >= interval_minutes


def _sidecar_path(store: str | Path | None) -> Path:
    """``reminders.yaml`` under the store's ``runtime/`` dir (scitex convention)."""
    from ._paths import runtime_dir

    return runtime_dir(store) / REMINDER_SIDECAR_NAME


def load_reminder_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the reminder sidecar → ``{"owners": {...}, "cards": {...}}``.

    Missing / unreadable / malformed sidecar → empty sections (fail-soft: a
    bad sidecar must never break a sweep). Always returns both sections so
    callers can index them without guarding.

    A legacy sidecar from the per-card engine carried only a ``cards:``
    mapping of ``{card_id: {count, last_at, escalated}}``. We read it
    leniently: any ``cards`` mapping loads into the ``cards`` section (the
    stale per-card cadence fields are simply ignored; only ``escalated`` is
    still meaningful), and ``owners`` starts empty — the per-owner cadence
    rebuilds itself from the first sweep. No migration step needed.
    """
    import yaml

    from ._yaml import safe_load

    path = _sidecar_path(store)
    empty = {"owners": {}, "cards": {}}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("reminders: cannot read %s: %s", path, exc)
        return empty
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("reminders: malformed %s: %s", path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    owners = data.get("owners")
    cards = data.get("cards")
    return {
        "owners": owners if isinstance(owners, dict) else {},
        "cards": cards if isinstance(cards, dict) else {},
    }


def save_reminder_state(state: dict[str, dict], store: str | Path | None = None) -> None:
    """Atomically persist the reminder sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    payload = {
        "owners": state.get("owners") or {},
        "cards": state.get("cards") or {},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("reminders: cannot write %s: %s", path, exc)


def _iso(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_parked(card: dict | None) -> bool:
    # A blocked card WITH a blocker waits on someone else — owner can't act, so
    # it's not actionable staleness (excluded from the nag). No blocker = ambiguous, kept.
    return bool(card) and card.get("status") == "blocked" and bool(card.get("blocker"))


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
    interval_minutes: float | None = None,
    escalate_after: int | None = None,
    escalate_priority: int | None = None,
    owners: set[str] | None = None,
) -> dict[str, list[str]]:
    """One nag sweep: enqueue a due DIGEST per owner + escalate stuck cards.

    Returns ``{"digested": [owner, ...], "escalated": [card_id, ...],
    "skipped": [owner, ...]}`` (skipped = owner has stale cards but the
    digest is not yet due this sweep).

    The re-digest cadence is a flat interval (operator knob): per owner it is
    the TIGHTEST of their cards' resolved intervals — a per-card
    ``reminder_interval_minutes`` > ``config.yaml`` ``reminders.interval_minutes``
    > the built-in default. ``interval_minutes`` here forces a flat value for
    every owner (a test seam); leave it ``None`` for the config/card resolution.

    Fail-soft: a per-owner enqueue error is logged and the sweep continues.
    Never raises into the caller (notifyd tick).
    """
    from ._config import reminders_config, resolve_interval_minutes

    cur = now or _now_utc()
    cfg = reminders_config()
    esc_after = escalate_after if escalate_after is not None else _env_int(
        ENV_ESCALATE_AFTER, _cfg_int(cfg, "escalate_after", DEFAULT_ESCALATE_AFTER)
    )
    esc_prio = escalate_priority if escalate_priority is not None else _env_int(
        ENV_ESCALATE_PRIORITY,
        _cfg_int(cfg, "escalate_priority", DEFAULT_ESCALATE_PRIORITY),
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

    # The set of (owner, [StaleCard]) needing a nag this sweep, oldest-first
    # within each owner (the detectors already sort that way).
    buckets: dict[str, list] = {}
    for owner, cards in detect_stale_active(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)
    for owner, cards in detect_pending_backlog(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)

    # Phased-rollout allowlist: when set (arg or env), nag ONLY these owners
    # and leave every other owner untouched (no fleet-wide first-sweep storm).
    allow = owners if owners is not None else _owner_allowlist(cfg)
    if allow:
        buckets = {o: c for o, c in buckets.items() if o in allow}

    state = load_reminder_state(store)
    owner_state: dict[str, dict] = state["owners"]
    card_state: dict[str, dict] = state["cards"]

    stale_owners: set[str] = set()
    stale_card_ids: set[str] = set()
    digested: list[str] = []
    escalated: list[str] = []
    skipped: list[str] = []

    for owner in sorted(buckets):
        if owner == "(unassigned)":
            continue  # nobody to nag; the gap is surfaced by the stats sweep
        # Skip PARKED cards (blocked WITH a blocker = waiting on someone else,
        # owner can't act → noise). Nag only ACTIONABLE staleness.
        cards = [sc for sc in buckets[owner]
                 if sc.id and not _is_parked(by_id.get(sc.id))]
        if not cards:
            continue
        stale_owners.add(owner)
        stale_card_ids.update(sc.id for sc in cards)

        # Effective cadence for this owner: the TIGHTEST interval any of their
        # stale cards asks for (a per-card override pulls the whole digest onto
        # a faster clock). A flat `interval_minutes` arg forces one value.
        if interval_minutes is not None:
            owner_interval = interval_minutes
        else:
            owner_interval = min(
                resolve_interval_minutes(by_id.get(sc.id), cfg) for sc in cards
            )

        entry = owner_state.get(owner) or {}
        count = int(entry.get("count") or 0)
        if not _due(entry.get("last_at"), cur, interval_minutes=owner_interval):
            skipped.append(owner)
            owner_state[owner] = entry
            continue

        owner_key = _safe_resolve(resolve_key, owner)
        body = _digest_body(cards, count + 1)
        if _safe_enqueue(
            enqueue, owner_key, EVENT_DIGEST, DIGEST_CARD_ID, body, cur, store
        ):
            count += 1
            entry["count"] = count
            entry["last_at"] = _iso(cur)
            digested.append(owner)
        owner_state[owner] = entry

        # Escalate each high-priority card that has survived enough digests to
        # its owner and has not yet escalated this stale streak. Per CARD: the
        # operator needs the specific stuck card, not the owner's whole list.
        if count >= esc_after:
            for sc in cards:
                card = by_id.get(sc.id, {})
                if not _is_high_priority(card, threshold=esc_prio):
                    continue
                centry = card_state.get(sc.id) or {}
                if centry.get("escalated"):
                    card_state[sc.id] = centry
                    continue
                op_key = _safe_resolve(resolve_key, operator_name)
                ebody = _escalation_body(sc, owner, count)
                if _safe_enqueue(
                    enqueue, op_key, EVENT_ESCALATION, sc.id, ebody, cur, store
                ):
                    centry["escalated"] = True
                    escalated.append(sc.id)
                card_state[sc.id] = centry

    # Prune state for owners/cards no longer stale — the nag STOPS when work
    # is closed or touched (it drops out of the stale set), and a card's
    # escalation latch resets so a future stall re-escalates.
    for owner in list(owner_state):
        if owner not in stale_owners:
            del owner_state[owner]
    for cid in list(card_state):
        if cid not in stale_card_ids:
            del card_state[cid]

    save_reminder_state(state, store)
    return {"digested": digested, "escalated": escalated, "skipped": skipped}


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

    ``ts`` is the sweep instant so each re-nag is a DISTINCT inbox record
    (the inbox dedups on ``(event_type, card_id, ts, actor)``); a new digest
    at a new time is genuinely new, not a duplicate.
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


__all__ = [
    "REMINDER_SIDECAR_NAME",
    "ENV_REMINDER_OWNERS",
    "EVENT_DIGEST",
    "EVENT_ESCALATION",
    "DIGEST_CARD_CAP",
    "DIGEST_CARD_ID",
    "DEFAULT_ESCALATE_AFTER",
    "DEFAULT_ESCALATE_PRIORITY",
    "DEFAULT_OPERATOR",
    "load_reminder_state",
    "save_reminder_state",
    "sweep_reminders",
]

# EOF
