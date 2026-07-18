#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board serves instantly across a store write, refreshing behind it.

WHY THIS EXISTS (operator, 2026-07-18): the board took 30+ seconds and showed
"loading…". Profiled on the live store: ``get_board`` costs **4.6 s** to
rebuild (0.01 s cached) and the cache is invalidated by EVERY store write —
so with a fleet writing every few seconds, nearly every board request paid a
full multi-MB parse. Measured end-to-end that was a 31 s board.

A board is a LIVE VIEW, not a decision read: it self-refreshes and shows a
LIVE badge, so data one refresh-cycle old is invisible to the viewer while a
31-second wait is not. So a stale board is served immediately and the rebuild
runs behind the response.

Pinned here: the stale hit is FAST, the refresh actually LANDS, a refresh
storm cannot start N rebuilds, a failing rebuild never blanks the board, and
the behaviour can be switched off. Real store files, no mocks.
"""

from __future__ import annotations

import os
import time

import pytest

from scitex_cards._django import services


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - id: a\n    title: a\n    status: in_progress\n"
        "  - id: b\n    title: b\n    status: done\n",
        encoding="utf-8",
    )
    services._board_cache.clear()
    services._refreshing.clear()
    # No per-project lanes in the fixture — keep the unit about the cache.
    monkeypatch.setenv("SCITEX_TODO_LANE_GLOBS", "")
    yield str(path)
    services._board_cache.clear()
    services._refreshing.clear()


def _settle(deadline_s: float = 10.0) -> None:
    """Wait for any background refresh to finish (bounded)."""
    end = time.time() + deadline_s
    while time.time() < end and services._refreshing:
        time.sleep(0.05)


def _bump_mtime(store) -> None:
    """Age the store forward by an UNAMBIGUOUS amount, standing in for a write.

    NOT ``os.utime(path, None)``, which sets the stamp to *now* and merely
    ASSUMES now is later than what the cache holds. The cache compares against
    ``effective_mtime``, and the refresh only kicks when that value MOVES.

    CONFIRMED: the flake is a missed invalidation. ``_load_global_tasks`` was
    never called (``assert 0 == 1``), which is only reachable when the board
    read judged the cache fresh — so ``utime(None)`` did not move
    ``effective_mtime`` on that run.

    NOT CONFIRMED — two mechanisms produce that and this test cannot tell them
    apart, so neither is claimed here:
      * coarse filesystem timestamps (set "now" twice inside one granule and
        the float comes back equal). Measured 0/200 collisions on the dev
        container's fs; CI's filesystem was never measured.
      * ``effective_mtime`` is a ``max()`` over the global store AND the lane
        files. Any lane file stamped later dominates the max, and bumping the
        store to "now" then changes nothing.

    Stepping the stamp explicitly past both is what makes this deterministic;
    it does not depend on which mechanism was at fault. The flake reached
    develop in #494 and turned two unrelated docs-only PRs red before it was
    traced back here.
    """
    bumped = os.stat(store).st_mtime + 5
    os.utime(store, (bumped, bumped))


def test_a_write_does_not_make_the_next_read_pay_a_rebuild(store):
    # Arrange: warm the cache.
    first = services.get_board(store, allow_stale=True)
    assert len(first.tasks) == 2

    # Act: a writer rolls the store's mtime, then the board is read again.
    _bump_mtime(store)
    t0 = time.time()
    served = services.get_board(store, allow_stale=True)
    elapsed = time.time() - t0

    # Assert: served from cache, not rebuilt — the point of the change.
    assert elapsed < 0.5
    assert len(served.tasks) == 2
    _settle()


def test_the_background_refresh_actually_lands(store):
    # Arrange
    services.get_board(store, allow_stale=True)

    # Act: append a card, then read (gets the stale board) and let it settle.
    with open(store, "a", encoding="utf-8") as fh:
        fh.write("  - id: c\n    title: c\n    status: deferred\n")
    services.get_board(store, allow_stale=True)
    _settle()

    # Assert: the refresh picked the new card up for the NEXT reader.
    assert {t["id"] for t in services.get_board(store).tasks} == {"a", "b", "c"}


def test_a_poll_storm_starts_only_one_refresh(store, monkeypatch):
    # Arrange: make the rebuild slow enough that polls overlap it.
    services.get_board(store, allow_stale=True)
    calls = {"n": 0}
    real = services._load_global_tasks

    def _slow(path):
        calls["n"] += 1
        time.sleep(0.4)
        return real(path)

    monkeypatch.setattr(services, "_load_global_tasks", _slow)

    # Act: ten rapid reads against a changed store (the operator's browser
    # polling while a rebuild is in flight).
    _bump_mtime(store)
    for _ in range(10):
        services.get_board(store, allow_stale=True)
    _settle()

    # Assert: one rebuild, not ten.
    assert calls["n"] == 1


def test_a_failing_refresh_keeps_serving_the_previous_board(store, monkeypatch):
    # Arrange
    good = services.get_board(store, allow_stale=True)

    def _boom(path):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(services, "_load_global_tasks", _boom)

    # Act: the store changes and the background rebuild blows up.
    _bump_mtime(store)
    served = services.get_board(store, allow_stale=True)
    _settle()

    # Assert: the operator still has a board — never blanked, and a further
    # stale read keeps serving it rather than surfacing the failure.
    # (A STRICT read would rightly raise here: the store really is unreadable,
    # and fail-loud is correct when the caller cannot tolerate staleness.)
    assert len(served.tasks) == len(good.tasks)
    assert len(services.get_board(store, allow_stale=True).tasks) == 2


def test_a_strict_caller_is_never_served_stale(store):
    """READ-YOUR-OWN-WRITES. The default must stay blocking.

    This pins a REAL regression: the first cut of stale-while-revalidate
    applied to every ``get_board`` caller, which broke the chat POST — it
    writes a message and reads it straight back through the board, so the
    operator's just-posted comment vanished until the next refresh. The
    endpoint that writes must see its write; only opted-in read-only views
    may lag.
    """
    # Arrange: warm the cache.
    services.get_board(store)

    # Act: a write lands, then a DEFAULT (strict) read.
    with open(store, "a", encoding="utf-8") as fh:
        fh.write("  - id: z\n    title: z\n    status: in_progress\n")
    served = services.get_board(store)

    # Assert: the new card is there — no staleness without opting in.
    assert {t["id"] for t in served.tasks} == {"a", "b", "z"}


def test_it_can_be_switched_off(store, monkeypatch):
    # Arrange
    services.get_board(store, allow_stale=True)
    monkeypatch.setenv("SCITEX_CARDS_BOARD_SWR", "0")

    # Act: with SWR off, a changed store rebuilds synchronously.
    with open(store, "a", encoding="utf-8") as fh:
        fh.write("  - id: d\n    title: d\n    status: deferred\n")
    served = services.get_board(store, allow_stale=True)

    # Assert: the caller sees the new card immediately (blocking rebuild).
    assert {t["id"] for t in served.tasks} == {"a", "b", "d"}


# EOF
