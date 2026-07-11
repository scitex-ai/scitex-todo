#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stale-active + pending-backlog nudge delivery — the network side.

Pairs with the pure detectors in :mod:`scitex_todo._stale_active`:
that module decides WHICH cards are stale-active / pending-backlog and
groups them by owner; THIS module delivers a concise per-owner nudge
over the SAME push wire ``--notify`` uses
(:func:`scitex_todo._push.deliver`). One sweep emits up to two distinct
per-owner lines:

* ``stale-active`` (kind="stale-active"): "close/update the in_progress
  / blocked work you said you were doing".
* ``pending-backlog`` (kind="pending-backlog"): "start or triage the
  pending cards you accepted but never began".

Both ride the SAME ``*/10`` ``--nudge-quiet`` cron — no new cron is
added — and both are fail-soft per owner.

Why a separate module (not inline in ``_cli/_stats.py``): ``_stats.py``
is at the line cap. Keeping the delivery loop here also keeps the pure
detector free of any ``_push`` / network import so it stays unit-testable
with plain list-of-dicts inputs.

Behaviour (rides the existing ``*/10`` ``--nudge-quiet`` cron):

* NOT liveness-gated. We nudge the owner regardless of whether the agent
  is currently online. An idle / offline owner is exactly the case where
  a stale active card is most likely forgotten; gating on liveness would
  suppress the most important nudges. Detection is purely time-based
  (``last_activity`` recency) — liveness is a separate concern owned by
  the wake-watcher / mesh.
* Fail-soft per owner. A delivery failure (bad turn URL, transport error,
  even an unexpected raise) for one owner is recorded and the sweep
  continues — one bad owner never breaks the batch.
* Short timeout. Reuses :data:`scitex_todo._push.NOTIFY_TIMEOUT_S` so a
  slow receiver can't stall the cron tick.
* DELIVER ON CHANGE. The sweep is scheduled (the notifyd low-cadence tick
  as well as the ``*/10`` cron), and re-pushing an IDENTICAL nudge on every
  tick is how a signal becomes noise — the same pathology the digest path
  hit on 2026-07-11 (26 identical digests in ~2 h). So a per-(owner, kind)
  fingerprint of the STALE CARD SET is persisted in a sidecar
  (:data:`NUDGE_SIDECAR_NAME`) and the push is skipped while that set is
  unchanged, UNTIL :data:`ENV_NUDGE_FLOOR_HOURS` elapses (a genuinely stuck
  owner is still nudged daily). Suppressed owners are still LOGGED — a
  silent sweep is one nobody trusts.

Optional hook event: when ``SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK=1`` and
the package's hook dispatcher is importable, the sweep also emits a
``stale-active`` finding so scitex-dev's ecosystem reconcile can consume
it. The primary deliverable is the per-owner nudge; the hook emission is
best-effort and never affects the nudge result.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
from pathlib import Path

from ._reminder_enqueue import _iso
from ._stale_active import (
    detect_pending_backlog,
    detect_stale_active,
    pending_backlog_nudge_line,
    stale_active_nudge_line,
)
from ._throughput import _now_utc, _parse_iso

logger = logging.getLogger(__name__)

ENV_EMIT_HOOK = "SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK"

#: Sidecar holding the per-(owner, kind) deliver-on-change state. A SIBLING of
#: the reminder sidecar in the store's ``runtime/`` dir — deliberately its own
#: file, not a third section of ``reminders.yaml``: the two sweeps run from
#: DIFFERENT processes (notifyd vs the ``*/10`` stats cron), so sharing one
#: read-modify-write file would let a stale-nudge save clobber the reminder
#: engine's escalation latches.
NUDGE_SIDECAR_NAME = "nudges.yaml"

#: How long an UNCHANGED nudge stays suppressed before it is re-sent anyway.
#: Mirrors :data:`scitex_todo._reminder_enqueue.ENV_DIGEST_FLOOR_HOURS`: without
#: a floor, deliver-on-change would go silent forever on a frozen backlog.
ENV_NUDGE_FLOOR_HOURS = "SCITEX_TODO_NUDGE_FLOOR_HOURS"
DEFAULT_NUDGE_FLOOR_HOURS = 24.0

KIND_STALE_ACTIVE = "stale-active"
KIND_PENDING_BACKLOG = "pending-backlog"


def _floor_minutes() -> float:
    """Suppression floor for an unchanged nudge, in minutes (env-overridable)."""
    raw = os.environ.get(ENV_NUDGE_FLOOR_HOURS)
    try:
        hours = float(raw) if raw is not None else DEFAULT_NUDGE_FLOOR_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_NUDGE_FLOOR_HOURS
    return hours * 60.0


def _nudge_fingerprint(kind: str, cards) -> str:
    """Identity of one owner's nudge CONTENT — the kind + the stale card SET.

    Order-independent (the set is sorted) and deliberately free of any age /
    wall-clock component: an age ticks on its own, so including it would make
    every sweep look "changed" and defeat the suppression entirely.
    """
    parts = sorted(c.id for c in cards if c.id)
    return hashlib.sha256("|".join([kind, *parts]).encode("utf-8")).hexdigest()


def _floor_elapsed(delivered_at: str | None, now: _dt.datetime) -> bool:
    """True when the unchanged-nudge floor has elapsed (or is unknown)."""
    if not delivered_at:
        return True
    parsed = _parse_iso(delivered_at)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() / 60.0 >= _floor_minutes()


def _sidecar_path(store: str | Path | None) -> Path:
    """``nudges.yaml`` under the store's ``runtime/`` dir (scitex convention)."""
    from ._paths import runtime_dir

    return runtime_dir(store) / NUDGE_SIDECAR_NAME


def load_nudge_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the nudge sidecar → ``{kind: {owner: {fingerprint, delivered_at}}}``.

    Missing / unreadable / malformed sidecar → empty sections (fail-soft: a bad
    sidecar must never break a sweep — the worst case is one re-push).
    """
    import yaml

    from ._yaml import safe_load

    path = _sidecar_path(store)
    empty: dict[str, dict] = {KIND_STALE_ACTIVE: {}, KIND_PENDING_BACKLOG: {}}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("stale-nudge: cannot read %s: %s", path, exc)
        return empty
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("stale-nudge: malformed %s: %s", path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    for kind in empty:
        section = data.get(kind)
        if isinstance(section, dict):
            empty[kind] = section
    return empty


def save_nudge_state(
    state: dict[str, dict], store: str | Path | None = None
) -> None:
    """Atomically persist the nudge sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    payload = {kind: state.get(kind) or {} for kind in
               (KIND_STALE_ACTIVE, KIND_PENDING_BACKLOG)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("stale-nudge: cannot write %s: %s", path, exc)


def _deliver_per_owner(
    by_owner: dict,
    *,
    kind: str,
    label: str,
    body_fn,
    lines: list[str],
    state: dict[str, dict],
    now: _dt.datetime,
) -> int:
    """Deliver one per-owner nudge of ``kind`` for each owner bucket.

    Shared loop for both the stale-active and pending-backlog sweeps:
    fail-soft per owner (a raise is logged + surfaced, never aborts the
    batch), short timeout, ``(unassigned)`` surfaced but not pushed.
    Returns the count of successful pushes.

    ``body_fn(owner, cards)`` composes the nudge body; ``label`` is the
    short noun used in the log lines (e.g. "stale" / "pending").

    ``state`` is this KIND's slice of the sidecar (``{owner: {fingerprint,
    delivered_at}}``), mutated in place: the push is SKIPPED (but still
    logged) while the owner's stale card set is unchanged and the floor has
    not elapsed. Only the push is conditional — every owner is still visited,
    fingerprinted, and surfaced.
    """
    from ._push import NOTIFY_TIMEOUT_S, deliver

    pushed = 0
    for owner, cards in sorted(by_owner.items()):
        if owner == "(unassigned)":
            # No turn URL exists for the unassigned bucket; surface the
            # gap but don't attempt a push.
            lines.append(
                f"  -  {owner:30}  {len(cards)} {label} (no owner)"
            )
            continue
        entry = state.get(owner) or {}
        fingerprint = _nudge_fingerprint(kind, cards)
        unchanged = fingerprint == entry.get("fingerprint")
        if unchanged and not _floor_elapsed(entry.get("delivered_at"), now):
            lines.append(
                f"  ..  {owner:30}  {len(cards)} {label}  suppressed "
                f"(unchanged since {entry.get('delivered_at')}; "
                f"floor {_floor_minutes() / 60:g}h)"
            )
        else:
            body = body_fn(owner, cards)
            try:
                result = deliver(owner, body, kind=kind, timeout=NOTIFY_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 — fail-soft per owner.
                logger.warning(
                    "[scitex-todo._stale_active_nudge] %s push to %s raised: %s",
                    kind, owner, exc,
                )
                lines.append(f"  x  {owner:30}  {kind} push raised: {exc}")
                result = {}
            else:
                ok_label = "OK " if result.get("ok") else "ERR"
                lines.append(
                    f"  {ok_label}  {owner:30}  {len(cards)} {label}  "
                    f"wire={result.get('wire')}  reason={result.get('reason')}"
                )
            if result.get("ok"):
                # Only a DELIVERED nudge arms the suppression — a failed push
                # must be retried on the next sweep, not silently swallowed.
                pushed += 1
                entry["fingerprint"] = fingerprint
                entry["delivered_at"] = _iso(now)
        if entry:
            state[owner] = entry
        else:
            # Never delivered (failed / raised) — leave NO entry, so the next
            # sweep retries this owner instead of reading a half-written one.
            state.pop(owner, None)

    # Prune owners who no longer have cards of this kind: the nudge STOPS when
    # the work is closed/touched, and a future stall re-pushes immediately.
    for owner in list(state):
        if owner not in by_owner:
            del state[owner]
    return pushed


def sweep_and_nudge(
    tasks: list[dict],
    *,
    store: str | Path | None = None,
    now: _dt.datetime | None = None,
) -> list[str]:
    """Detect stale-active + pending-backlog cards; nudge each owner.

    Returns a list of human-readable log lines (per owner per kind plus
    per-kind summaries) so the caller (the CLI / cron) can echo them.
    Never raises: every per-owner delivery is guarded so the whole sweep
    is fail-soft — one bad owner (or one bad kind) never breaks the rest.

    DELIVER ON CHANGE: an owner whose stale card set is identical to the last
    DELIVERED one is skipped (and logged as suppressed) until
    :data:`ENV_NUDGE_FLOOR_HOURS` elapses. ``store`` selects the sidecar that
    carries that state across runs; ``now`` is a clock seam for tests.
    """
    cur = now or _now_utc()
    state = load_nudge_state(store)

    by_owner = detect_stale_active(tasks, now=cur)
    lines: list[str] = []
    pushed = _deliver_per_owner(
        by_owner,
        kind=KIND_STALE_ACTIVE,
        label="stale",
        body_fn=stale_active_nudge_line,
        lines=lines,
        state=state[KIND_STALE_ACTIVE],
        now=cur,
    )

    if os.environ.get(ENV_EMIT_HOOK) == "1":
        _emit_hook(by_owner, lines)

    lines.append(f"# {pushed} stale-active push(es) sent")

    # Pending-backlog sweep — distinct status set, threshold, and wording.
    by_owner_pending = detect_pending_backlog(tasks, now=cur)
    pushed_pending = _deliver_per_owner(
        by_owner_pending,
        kind=KIND_PENDING_BACKLOG,
        label="pending",
        body_fn=pending_backlog_nudge_line,
        lines=lines,
        state=state[KIND_PENDING_BACKLOG],
        now=cur,
    )
    lines.append(f"# {pushed_pending} pending-backlog push(es) sent")

    save_nudge_state(state, store)
    return lines


def _emit_hook(by_owner: dict, lines: list[str]) -> None:
    """Best-effort: emit a ``stale-active`` finding via the hook bus.

    Never raises into the sweep — a missing dispatcher or a plugin error
    is logged and ignored. Appends a one-line marker to ``lines``.
    """
    try:
        from ._hooks import dispatch_event

        owners = {k: len(v) for k, v in by_owner.items()}
        dispatch_event(
            {
                "kind": "stale-active",
                "source": "scitex-todo._stale_active_nudge",
                "owners": owners,
                "total": sum(owners.values()),
            }
        )
        lines.append(f"  hook  stale-active emitted ({len(owners)} owner(s))")
    except Exception as exc:  # noqa: BLE001 — best-effort.
        logger.debug(
            "[scitex-todo._stale_active_nudge] hook emit skipped: %s", exc,
        )


__all__ = [
    "ENV_EMIT_HOOK",
    "ENV_NUDGE_FLOOR_HOURS",
    "DEFAULT_NUDGE_FLOOR_HOURS",
    "NUDGE_SIDECAR_NAME",
    "KIND_STALE_ACTIVE",
    "KIND_PENDING_BACKLOG",
    "load_nudge_state",
    "save_nudge_state",
    "sweep_and_nudge",
]
