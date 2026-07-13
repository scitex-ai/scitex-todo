#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the delivery-channel registry (slice 1).

Real fake channels via the ``extra_providers=`` seam — NO mocks. Covers the
spec's required registry behaviour:
* dedup by channel name (first-wins),
* deterministic (sorted) order,
* a load-raising provider is SKIPPED and surfaced to stderr (fail-loud).

Also exercises the REAL installed ``log`` entry point so the pyproject
registration is verified end-to-end (the worktree is ``pip install -e .``).
"""

from __future__ import annotations

from scitex_cards._delivery._registry import discover_channels

from ._fakes import RecorderChannel


def test_extra_providers_dedup_and_sorted_order():
    a = RecorderChannel(name="alpha")
    z = RecorderChannel(name="zeta")
    m = RecorderChannel(name="mid")
    # Provided out of order; result must be name-sorted. Other channels (the
    # real installed `log` entry point) may also be present, so assert the
    # injected ones appear in sorted order among themselves.
    found = discover_channels(extra_providers=[z, a, m])
    injected = [k for k in found if k in {"alpha", "mid", "zeta"}]
    assert injected == ["alpha", "mid", "zeta"]


def test_extra_providers_first_wins_on_duplicate_name(capsys):
    first = RecorderChannel(name="dup")
    second = RecorderChannel(name="dup")
    found = discover_channels(extra_providers=[first, second])
    assert found["dup"] is first  # first registration wins
    err = capsys.readouterr().err
    assert "duplicate delivery channel name" in err
    assert "dup" in err


def test_unnamed_channel_skipped_with_warning(capsys):
    class _NoName:
        name = ""

        def deliver(self, *, recipient, address, notification):  # pragma: no cover
            raise AssertionError("should never be called")

    found = discover_channels(extra_providers=[_NoName()])
    # The real installed `log` entry point is still present.
    assert "" not in found
    err = capsys.readouterr().err
    assert "no usable .name" in err


def test_load_raising_entry_point_is_skipped(capsys):
    """A provider that raises on LOAD is skipped + surfaced to stderr.

    The registry loads entry points by calling ``ep.load()`` then invoking
    the factory. We drive the exact same skip-and-warn path through a fake
    entry point object (a real object with a ``load`` that raises — not a
    mock) by exercising the internal loader helper directly.
    """
    from scitex_cards._delivery import _registry

    class _BoomEP:
        name = "boom"

        def load(self):
            raise ImportError("provider import blew up")

    class _GoodEP:
        name = "good"

        def load(self):
            return lambda: RecorderChannel(name="good")

    original = _registry._iter_entry_points
    _registry._iter_entry_points = lambda group: [_BoomEP(), _GoodEP()]
    try:
        found = _registry.discover_channels()
    finally:
        _registry._iter_entry_points = original

    # The boom provider is skipped; the good one survives.
    assert "good" in found
    assert "boom" not in found
    err = capsys.readouterr().err
    assert "failed to load delivery channel entry point" in err
    assert "boom" in err


def test_real_log_entry_point_discovered():
    """The pyproject-registered ``log`` channel is discovered for real."""
    found = discover_channels()
    assert "log" in found
    assert found["log"].name == "log"


def test_real_telegram_entry_point_discovered():
    """The slice-3 ``telegram`` channel is discovered via its entry point.

    Verifies the pyproject registration end-to-end (the worktree is
    ``pip install -e .``): the registry instantiates the channel from the
    entry point WITHOUT a token (lazy resolution) and exposes it by name.
    """
    found = discover_channels()
    assert "telegram" in found
    assert found["telegram"].name == "telegram"


# EOF
