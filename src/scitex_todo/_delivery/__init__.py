#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone notification-DELIVERY loop for scitex-todo (no sac).

scitex-todo already EMITS card-events and ENQUEUEs per-recipient
notifications into the YAML pull-inbox (:mod:`scitex_todo._inbox`), but
nothing pushes those records OUT to recipients — they sit unread. This
package adds scitex-todo's OWN standalone delivery rail: a loop that reads
each user's pending notifications and hands them to the channels configured
for that user (log, telegram, …), tracking what was delivered in a keyed
dedup ledger so nothing is double-sent.

Modeled on scitex-agent-container (sac) BUT fully independent — this package
has ZERO dependency on ``scitex_agent_container`` in either direction. The
first-class recipient here is a "user" (not an "agent"): policy + addressing
live in :mod:`scitex_todo._delivery._recipients`, never inside a channel.

Hard separation of concerns
---------------------------
* The user's inbox ``seen`` cursor is the USER's READ state — the delivery
  loop NEVER flips it (read-only ``poll_inbox(mark_seen=False)``).
* The :class:`~scitex_todo._delivery._ledger.Ledger` is the SOLE source of
  delivery truth (what was sent / failed / is retry-eligible).
* Channels are dumb transports: they hand off bytes and report
  ``sent | failed | skipped`` — they never decide WHETHER to deliver.

Slice 1 ships the port + a stdlib-logging channel + registry + ledger +
recipients + the loop + a ``scitex-todo deliver`` one-shot CLI command. The
long-running daemon + systemd unit are a LATER slice.
"""

from __future__ import annotations

from ._channel import DeliveryChannel, DeliveryResult, Status
from ._loop import deliver_pending
from ._recipients import load_recipients, should_deliver_now
from ._registry import discover_channels

__all__ = [
    "DeliveryChannel",
    "DeliveryResult",
    "Status",
    "deliver_pending",
    "discover_channels",
    "load_recipients",
    "should_deliver_now",
]

# EOF
