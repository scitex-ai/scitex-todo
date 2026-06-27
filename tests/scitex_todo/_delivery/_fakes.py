#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REAL fake channels for the delivery tests — NO mocks, no monkeypatch.

These are concrete classes implementing the
:class:`scitex_todo._delivery._channel.DeliveryChannel` Protocol, injected
through the ``extra_providers=`` / ``channels=`` seams. They exercise the
real loop + ledger code paths without touching any wire or installed entry
point (STX-NM / PA-306 forbid mocks).
"""

from __future__ import annotations

from scitex_todo._delivery._channel import DeliveryResult, Status


class RecorderChannel:
    """Records every ``deliver`` call to a list and reports ``sent``."""

    def __init__(self, name: str = "log"):
        self.name = name
        self.calls: list[dict] = []

    def deliver(self, *, recipient, address, notification) -> DeliveryResult:
        self.calls.append(
            {
                "recipient": recipient,
                "address": address,
                "notification": dict(notification),
            }
        )
        return DeliveryResult(status=Status.SENT, channel=self.name)


class FlakyChannel:
    """Raises until ``fail_times`` attempts have happened, then succeeds.

    A real transport that is down then recovers — used to prove the ledger
    records a failure, backs off, and a later run re-attempts + can succeed.
    """

    def __init__(self, name: str = "log", fail_times: int = 1):
        self.name = name
        self.fail_times = fail_times
        self.attempts = 0
        self.calls: list[dict] = []

    def deliver(self, *, recipient, address, notification) -> DeliveryResult:
        self.attempts += 1
        self.calls.append(
            {"recipient": recipient, "notification": dict(notification)}
        )
        if self.attempts <= self.fail_times:
            raise RuntimeError(f"transport down (attempt {self.attempts})")
        return DeliveryResult(status=Status.SENT, channel=self.name)


class AlwaysFailChannel:
    """Raises on EVERY attempt — a permanently-down transport.

    Used to prove the retry budget exhausts to a TERMINAL (surfaced, never
    silently dropped) comm-miss rather than retrying forever.
    """

    def __init__(self, name: str = "log"):
        self.name = name
        self.attempts = 0

    def deliver(self, *, recipient, address, notification) -> DeliveryResult:
        self.attempts += 1
        raise RuntimeError(f"transport permanently down (attempt {self.attempts})")


# EOF
