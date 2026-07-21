#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the CURRENCY gate (operator directive: stale/broken installs ERROR).

``check_currency()`` delegates to ``scitex_dev.staleness.ensure_current`` when
scitex-dev is installed, and is a no-op otherwise (decoupling rule — see
``_currency.py``). Every case here fakes the optional dependency via
``sys.modules`` rather than requiring a real scitex-dev>=0.34.0 install or
touching the network, so these tests are deterministic regardless of what is
actually installed in the environment.
"""

from __future__ import annotations

import sys
import types

import pytest

from scitex_cards._currency import check_currency


def _install_fake_staleness_module(monkeypatch, ensure_current):
    """Register a fake `scitex_dev.staleness` module in `sys.modules` so
    `check_currency()`'s `from scitex_dev.staleness import ensure_current`
    resolves to `ensure_current` — no real scitex-dev>=0.34.0 required."""
    fake_package = types.ModuleType("scitex_dev")
    fake_module = types.ModuleType("scitex_dev.staleness")
    fake_module.ensure_current = ensure_current
    monkeypatch.setitem(sys.modules, "scitex_dev", fake_package)
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", fake_module)


# --------------------------------------------------------------------------- #
# (a) scitex-dev absent -> no-op                                              #
# --------------------------------------------------------------------------- #
def test_check_currency_no_ops_when_scitex_dev_lacks_the_staleness_module(
    monkeypatch,
):
    # Arrange — force the optional import to fail, regardless of whether
    # scitex-dev happens to be installed in this environment (`None` in
    # `sys.modules` makes the import system raise ImportError for that name).
    monkeypatch.setitem(sys.modules, "scitex_dev.staleness", None)

    # Act / Assert — no exception; scitex-cards stays standalone.
    check_currency()


# --------------------------------------------------------------------------- #
# (b) scitex-dev present + current -> passes through                         #
# --------------------------------------------------------------------------- #
def test_check_currency_passes_through_when_the_install_is_current(monkeypatch):
    # Arrange — a fake `ensure_current` that behaves like a fresh, intact install.
    calls = []
    _install_fake_staleness_module(monkeypatch, calls.append)

    # Act
    check_currency()

    # Assert — the gate delegates to scitex-dev, naming THIS distribution.
    assert calls == ["scitex-cards"]


# --------------------------------------------------------------------------- #
# (c) scitex-dev present + stale -> raises, message carries the remedy       #
# --------------------------------------------------------------------------- #
def test_check_currency_raises_when_the_install_is_stale(monkeypatch):
    # Arrange — a fake `ensure_current` that raises like a stale install.
    class _FakeStalenessError(RuntimeError):
        pass

    def _fake_ensure_current(dist_name):
        raise _FakeStalenessError(f"{dist_name} is stale")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act / Assert
    with pytest.raises(RuntimeError):
        check_currency()


def test_check_currency_stale_error_message_carries_the_remedy_command(monkeypatch):
    # Arrange — a fake `ensure_current` that raises with the exact upgrade
    # remedy scitex-dev would give a real caller.
    remedy = "pip install -U scitex-cards"

    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} is stale — run: {remedy}")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act
    with pytest.raises(RuntimeError) as exc_info:
        check_currency()

    # Assert — the remedy text is not swallowed; it propagates verbatim.
    assert remedy in str(exc_info.value)


def test_check_currency_broken_payload_error_also_propagates(monkeypatch):
    """The gate also covers the broken-payload incident class (ambiguous
    dist-info / missing RECORD files) — any `ensure_current` raise must
    propagate, not just a plain version-staleness one."""

    # Arrange
    def _fake_ensure_current(dist_name):
        raise RuntimeError(f"{dist_name} has an ambiguous dist-info install")

    _install_fake_staleness_module(monkeypatch, _fake_ensure_current)

    # Act / Assert
    with pytest.raises(RuntimeError, match="ambiguous dist-info"):
        check_currency()


# EOF
