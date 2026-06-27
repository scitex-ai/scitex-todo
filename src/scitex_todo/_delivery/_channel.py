#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The delivery PORT — the contract every channel implements.

A :class:`DeliveryChannel` is a dumb synchronous transport: hand it a
notification + a recipient address and it tries to deliver, then reports
back a :class:`DeliveryResult`. It NEVER decides *whether* to deliver
(quiet-hours / consent / dedup live in the loop + recipients + ledger) and
it NEVER touches the user's inbox cursor.

Why ``sent`` (not ``delivered``)
--------------------------------
A SYNCHRONOUS transport can only confirm it HANDED OFF the payload to the
underlying channel (wrote a log line, POSTed to a bot API). It cannot
confirm the human RECEIVED + read it. So the terminal success status is
``sent`` — an honest "we handed it off", never the over-claim "delivered".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class Status(str, Enum):
    """The three possible outcomes of a single delivery attempt.

    * ``sent`` — the channel handed the payload off successfully
      (terminal success; the ledger records it and never retries).
    * ``failed`` — the attempt raised / the transport refused. RETRYABLE:
      the ledger schedules a backed-off retry up to ``MAX_ATTEMPTS``.
    * ``skipped`` — a policy gate (``should_deliver_now``) said "not now".
      NON-terminal: re-evaluated on the next run, never counted as failure.

    Deliberately NOT ``delivered`` — see the module docstring. ``str`` mix-in
    so the value serialises cleanly into the YAML ledger as a plain string.
    """

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DeliveryResult:
    """The immutable outcome a channel returns from one ``deliver`` call.

    Frozen (a value object, NOT a mutable dict) so a result can never be
    silently mutated after a channel returns it — the ledger records exactly
    what the channel reported.

    Parameters
    ----------
    status : Status
        ``sent`` / ``failed`` / ``skipped`` — see :class:`Status`.
    channel : str
        The channel ``name`` that produced this result (e.g. ``"log"``).
    detail : str | None
        Optional human-readable note (an error message on ``failed``, a
        short trace on ``sent``); ``None`` when there is nothing to add.
    """

    status: Status
    channel: str
    detail: str | None = None


@runtime_checkable
class DeliveryChannel(Protocol):
    """The transport contract — one notification out to one recipient.

    A concrete channel exposes a stable :attr:`name` (its registry key) and
    a synchronous :meth:`deliver`. Implementations MUST be side-effect-only
    on the wire — they read nothing from and write nothing to the task store
    or the inbox; all state lives in the ledger, owned by the loop.
    """

    #: Stable channel id — the dedup key in the registry + the ledger.
    name: str

    def deliver(
        self,
        *,
        recipient: str,
        address: str,
        notification: dict,
    ) -> DeliveryResult:
        """Attempt to deliver ``notification`` to ``recipient`` at ``address``.

        Parameters
        ----------
        recipient : str
            The user id (or raw-name fallback) the notification is for.
        address : str
            The channel-specific destination (a chat id, an email, …). May
            be empty for channels that need none (e.g. ``log``).
        notification : dict
            The inbox record (``id`` / ``event_type`` / ``card_id`` /
            ``body`` / ``actor`` / ``ts`` / …).

        Returns
        -------
        DeliveryResult
            ``sent`` on success, ``failed`` on a handled transport error.
            A channel MAY raise instead of returning ``failed`` — the loop
            catches that and treats it as a (retryable) ``failed``.
        """
        ...


__all__ = ["DeliveryChannel", "DeliveryResult", "Status"]

# EOF
