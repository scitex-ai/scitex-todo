#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the delivery PORT + the slice-1 LogChannel.

No mocks: the LogChannel writes via the real stdlib logger (captured with
pytest's ``caplog``) and returns a real frozen ``DeliveryResult``.
"""

from __future__ import annotations

import logging

import pytest

from scitex_todo._delivery._channel import DeliveryChannel, DeliveryResult, Status
from scitex_todo._delivery._channels.log import LogChannel


def test_status_values_are_exactly_three():
    assert {s.value for s in Status} == {"sent", "failed", "skipped"}


def test_delivery_result_is_frozen():
    r = DeliveryResult(status=Status.SENT, channel="log")
    assert r.detail is None
    with pytest.raises(Exception):
        r.status = Status.FAILED  # frozen dataclass → cannot reassign


def test_log_channel_satisfies_protocol():
    ch = LogChannel()
    assert isinstance(ch, DeliveryChannel)
    assert ch.name == "log"


def test_log_channel_deliver_writes_line_and_returns_sent(caplog):
    ch = LogChannel()
    note = {
        "id": "n_abc",
        "event_type": "reassigned",
        "card_id": "c1",
        "body": "Card c1 reassigned to you",
    }
    with caplog.at_level(logging.INFO, logger="scitex_todo._delivery._channels.log"):
        result = ch.deliver(recipient="u_alice", address="", notification=note)
    assert result.status == Status.SENT
    assert result.channel == "log"
    # The human-readable line carries the key fields.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "u_alice" in joined
    assert "n_abc" in joined
    assert "reassigned" in joined


# EOF
