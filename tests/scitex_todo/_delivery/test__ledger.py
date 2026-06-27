#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the keyed-dedup delivery ledger (slice 1).

Real YAML round-trips against a real ``tmp_path`` store — NO mocks. Verifies
the ledger is a KEYED MAP (not an append-log), persists at
``<store_dir>/delivery_ledger.yaml``, and that ``already_done`` /
``retry_eligible`` / ``record`` behave per spec (exponential backoff, capped
attempts).
"""

from __future__ import annotations

import datetime as _dt

import yaml

from scitex_todo._delivery._channel import DeliveryResult, Status
from scitex_todo._delivery._ledger import (
    BASE_BACKOFF_SEC,
    MAX_ATTEMPTS,
    TERMINAL_STATUS,
    Ledger,
    ledger_path,
)


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def test_ledger_path_is_sibling_of_store(tmp_path):
    store = _store(tmp_path)
    assert ledger_path(store) == tmp_path / "delivery_ledger.yaml"


def test_record_sent_is_terminal_and_already_done(tmp_path):
    store = _store(tmp_path)
    led = Ledger.load(store)
    now = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)
    led.record(
        "u_a", "n_1", "log",
        DeliveryResult(status=Status.SENT, channel="log"), now,
    )
    assert led.already_done("u_a", "n_1", "log") is True
    # Reload from disk → state persisted as a keyed map.
    reloaded = Ledger.load(store)
    assert reloaded.already_done("u_a", "n_1", "log") is True

    raw = yaml.safe_load((tmp_path / "delivery_ledger.yaml").read_text())
    assert isinstance(raw, dict)
    assert len(raw) == 1  # ONE keyed entry, not an append list.
    (entry,) = raw.values()
    assert entry["status"] == "sent"
    assert entry["attempts"] == 1


def test_failure_backoff_grows_and_caps_attempts(tmp_path):
    store = _store(tmp_path)
    led = Ledger.load(store)
    now = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)

    fail = DeliveryResult(status=Status.FAILED, channel="log")
    led.record("u_a", "n_1", "log", fail, now)
    assert led.has_failure("u_a", "n_1", "log") is True
    # Within backoff → not eligible; after BASE backoff → eligible.
    assert led.retry_eligible("u_a", "n_1", "log", now) is False
    later = now + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    assert led.retry_eligible("u_a", "n_1", "log", later) is True

    # Second failure → backoff doubles (2*BASE).
    led.record("u_a", "n_1", "log", fail, later)
    not_yet = later + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    assert led.retry_eligible("u_a", "n_1", "log", not_yet) is False
    much_later = later + _dt.timedelta(seconds=2 * BASE_BACKOFF_SEC + 1)
    assert led.retry_eligible("u_a", "n_1", "log", much_later) is True

    # Exhaust attempts → no longer retry-eligible even far in the future.
    t = much_later
    while True:
        entry = led._get("u_a", "n_1", "log")
        if entry["attempts"] >= MAX_ATTEMPTS:
            break
        t = t + _dt.timedelta(hours=1)
        led.record("u_a", "n_1", "log", fail, t)
    far = t + _dt.timedelta(days=1)
    assert led.retry_eligible("u_a", "n_1", "log", far) is False


def test_exhausting_attempts_becomes_terminal_and_round_trips(tmp_path):
    """A failure that exhausts MAX_ATTEMPTS becomes a TERMINAL comm-miss.

    Also exercises the full persist→reload→query round-trip of the 0x1f
    composite key + the terminal status through PyYAML (minor note #2).
    """
    store = _store(tmp_path)
    led = Ledger.load(store)
    fail = DeliveryResult(status=Status.FAILED, channel="log", detail="boom")
    now = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)

    # Record MAX_ATTEMPTS failures (far enough apart each is its own attempt).
    for i in range(MAX_ATTEMPTS):
        led.record("u_a", "n_1", "log", fail, now + _dt.timedelta(hours=i))

    # Last record promoted it to terminal: not a plain failure, not retryable.
    assert led.is_terminal("u_a", "n_1", "log") is True
    assert led.has_failure("u_a", "n_1", "log") is False
    assert led.retry_eligible(
        "u_a", "n_1", "log", now + _dt.timedelta(days=365)
    ) is False
    assert led.already_done("u_a", "n_1", "log") is False

    # Round-trips through disk: composite key + terminal status survive reload.
    raw = yaml.safe_load((tmp_path / "delivery_ledger.yaml").read_text())
    assert len(raw) == 1
    (entry,) = raw.values()
    assert entry["status"] == TERMINAL_STATUS
    assert entry["attempts"] == MAX_ATTEMPTS

    reloaded = Ledger.load(store)
    assert reloaded.is_terminal("u_a", "n_1", "log") is True


def test_skipped_does_not_consume_attempts(tmp_path):
    store = _store(tmp_path)
    led = Ledger.load(store)
    now = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)
    led.record(
        "u_a", "n_1", "log",
        DeliveryResult(status=Status.SKIPPED, channel="log"), now,
    )
    entry = led._get("u_a", "n_1", "log")
    assert entry["status"] == "skipped"
    assert entry["attempts"] == 0  # skipped is non-terminal, no budget spent
    assert led.already_done("u_a", "n_1", "log") is False
    assert led.has_failure("u_a", "n_1", "log") is False


# EOF
