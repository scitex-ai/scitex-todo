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

Optional hook event: when ``SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK=1`` and
the package's hook dispatcher is importable, the sweep also emits a
``stale-active`` finding so scitex-dev's ecosystem reconcile can consume
it. The primary deliverable is the per-owner nudge; the hook emission is
best-effort and never affects the nudge result.
"""

from __future__ import annotations

import logging
import os

from ._stale_active import (
    detect_pending_backlog,
    detect_stale_active,
    pending_backlog_nudge_line,
    stale_active_nudge_line,
)

logger = logging.getLogger(__name__)

ENV_EMIT_HOOK = "SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK"


def _deliver_per_owner(
    by_owner: dict, *, kind: str, label: str, body_fn, lines: list[str]
) -> int:
    """Deliver one per-owner nudge of ``kind`` for each owner bucket.

    Shared loop for both the stale-active and pending-backlog sweeps:
    fail-soft per owner (a raise is logged + surfaced, never aborts the
    batch), short timeout, ``(unassigned)`` surfaced but not pushed.
    Returns the count of successful pushes.

    ``body_fn(owner, cards)`` composes the nudge body; ``label`` is the
    short noun used in the log lines (e.g. "stale" / "pending").
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
        body = body_fn(owner, cards)
        try:
            result = deliver(owner, body, kind=kind, timeout=NOTIFY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — fail-soft per owner.
            logger.warning(
                "[scitex-todo._stale_active_nudge] %s push to %s raised: %s",
                kind, owner, exc,
            )
            lines.append(f"  x  {owner:30}  {kind} push raised: {exc}")
            continue
        ok_label = "OK " if result.get("ok") else "ERR"
        lines.append(
            f"  {ok_label}  {owner:30}  {len(cards)} {label}  "
            f"wire={result.get('wire')}  reason={result.get('reason')}"
        )
        if result.get("ok"):
            pushed += 1
    return pushed


def sweep_and_nudge(tasks: list[dict]) -> list[str]:
    """Detect stale-active + pending-backlog cards; nudge each owner.

    Returns a list of human-readable log lines (per owner per kind plus
    per-kind summaries) so the caller (the CLI / cron) can echo them.
    Never raises: every per-owner delivery is guarded so the whole sweep
    is fail-soft — one bad owner (or one bad kind) never breaks the rest.
    """
    by_owner = detect_stale_active(tasks)
    lines: list[str] = []
    pushed = _deliver_per_owner(
        by_owner,
        kind="stale-active",
        label="stale",
        body_fn=stale_active_nudge_line,
        lines=lines,
    )

    if os.environ.get(ENV_EMIT_HOOK) == "1":
        _emit_hook(by_owner, lines)

    lines.append(f"# {pushed} stale-active push(es) sent")

    # Pending-backlog sweep — distinct status set, threshold, and wording.
    by_owner_pending = detect_pending_backlog(tasks)
    pushed_pending = _deliver_per_owner(
        by_owner_pending,
        kind="pending-backlog",
        label="pending",
        body_fn=pending_backlog_nudge_line,
        lines=lines,
    )
    lines.append(f"# {pushed_pending} pending-backlog push(es) sent")
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


__all__ = ["ENV_EMIT_HOOK", "sweep_and_nudge"]
