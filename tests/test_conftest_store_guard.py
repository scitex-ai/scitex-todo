#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The live-store damage detector must still bite after being made precise.

``tests/conftest.py``'s session guard was changed on 2026-07-22 from "did the
real store file change (mtime/size)" to "is the real store still intact
(count/schema_meta/integrity)". The old criterion fired on every run longer
than a few seconds, because peer fleet agents write to this shared board
continuously — noise that trains people to ignore the one gate that caught
three production wipes.

Making a gate quieter is exactly how a gate gets accidentally disabled, so
this file pins the trade explicitly: every damage shape this suite has
actually inflicted on production must still be detected, and the specific
thing that made it noisy must not be.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _store_damage import content_or_none, damage, damaged_candidates

#: A healthy "before" snapshot, shaped like the real board.
BEFORE = {
    "count": 2261,
    "meta": {
        "schema_version": "4",
        "store_path": "/home/agent/.scitex/cards/cards.db",
        "min_client_version": "0.17.5",
        "yaml_path": "/nonexistent/scitex-cards-canonical-db-DO-NOT-MIRROR/tasks.yaml",
    },
    "integrity": "ok",
}


def _after(**overrides):
    """A copy of :data:`BEFORE` with ``overrides`` applied."""
    return {**BEFORE, **overrides}


# --------------------------------------------------------------------------- #
# MUST DETECT — every shape that has actually destroyed data.                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "after_count"),
    [
        ("2026-07-19 mirror write path", 21),
        ("2026-07-19 canonical write path", 1),
        ("2026-07-19 canonical read path", 3),
        ("2026-07-21 third wipe", 18),
        ("off-by-one — one card silently dropped", 2260),
    ],
)
def test_detects_every_historical_wipe_shape(label, after_count):
    """A count DECREASE is always damage — the store is append-only."""
    # Arrange / Act
    why = damage(BEFORE, _after(count=after_count))
    # Assert
    assert why is not None, f"{label} went undetected"
    assert "SHRANK" in why


def test_detects_schema_metadamage():
    """wipe #5's shape: the store's identity stamps rewritten."""
    # Arrange
    broken = dict(BEFORE["meta"])
    broken["yaml_path"] = "/home/agent/.scitex/cards/tasks.yaml"  # re-armed
    # Act
    why = damage(BEFORE, _after(meta=broken))
    # Assert
    assert why is not None and "schema_meta CHANGED" in why


def test_detects_dropped_version_floor():
    """Losing ``min_client_version`` re-opens the board to outdated clients."""
    # Arrange
    without_floor = {
        k: v for k, v in BEFORE["meta"].items() if k != "min_client_version"
    }
    # Act
    why = damage(BEFORE, _after(meta=without_floor))
    # Assert
    assert why is not None and "schema_meta CHANGED" in why


def test_detects_corruption():
    """A structurally broken database is damage even at the same card count."""
    # Arrange / Act
    why = damage(BEFORE, _after(integrity="row 42 missing from index"))
    # Assert
    assert why is not None and "integrity_check" in why


def test_detects_store_becoming_unreadable():
    """Readable before, unreadable after — deletion/truncation included."""
    # Arrange / Act
    why = damage(BEFORE, None)
    # Assert
    assert why is not None and "UNREADABLE" in why


# --------------------------------------------------------------------------- #
# MUST NOT FIRE — the noise the change exists to remove.                       #
# --------------------------------------------------------------------------- #


def test_peer_writes_do_not_fire():
    """The measured false positive: peers add cards to this shared live board.

    2026-07-22: a 69s run tripped the old mtime criterion purely on writes by
    sac and scitex-ui, with the board provably intact.
    """
    # Arrange / Act — peers added 25 cards during the session
    why = damage(BEFORE, _after(count=2286))
    # Assert
    assert why is None


def test_unchanged_store_does_not_fire():
    """The common case."""
    # Arrange / Act / Assert
    assert damage(BEFORE, _after()) is None


def test_unreadable_before_is_not_damage():
    """A candidate path that never existed cannot have been damaged by us."""
    # Arrange / Act / Assert
    assert damage(None, None) is None
    assert damage(None, _after()) is None


# --------------------------------------------------------------------------- #
# END TO END, against real databases — the wiring, not just the predicate.     #
# --------------------------------------------------------------------------- #


def _make_store(path: Path, *, count: int, meta: dict | None = None) -> None:
    """Write a minimal store-shaped database at ``path``."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO tasks (id) VALUES (?)",
            [(f"card-{i}",) for i in range(count)],
        )
        conn.executemany(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            sorted((meta or {"schema_version": "4"}).items()),
        )
        conn.commit()
    finally:
        conn.close()


def test_end_to_end_detects_a_real_wipe(tmp_path):
    """Snapshot a real DB, really delete its rows, and catch it."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, count=2170)
    before = {db: content_or_none(db)}
    assert before[db]["count"] == 2170, "fixture did not read back"
    # Act — the 2026-07-21 wipe, for real: 2170 -> 18
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM tasks WHERE id NOT IN (SELECT id FROM tasks LIMIT 18)")
    conn.commit()
    conn.close()
    # Assert
    damaged = damaged_candidates(before, (db,))
    assert len(damaged) == 1
    assert "SHRANK: 2170 -> 18" in damaged[0][1]


def test_end_to_end_ignores_a_real_peer_insert(tmp_path):
    """The false positive, for real: a peer adds cards and the file changes."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, count=2261)
    before = {db: content_or_none(db)}
    # Act — a peer writes 25 new cards, exactly as sac/scitex-ui do
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO tasks (id) VALUES (?)", [(f"peer-{i}",) for i in range(25)]
    )
    conn.commit()
    conn.close()
    # Assert
    assert damaged_candidates(before, (db,)) == []


def test_end_to_end_detects_a_real_stamp_rewrite(tmp_path):
    """wipe #5's shape, for real: the DO-NOT-MIRROR sentinel re-armed."""
    # Arrange
    db = tmp_path / "cards.db"
    sentinel = "/nonexistent/scitex-cards-canonical-db-DO-NOT-MIRROR/tasks.yaml"
    _make_store(db, count=10, meta={"yaml_path": sentinel})
    before = {db: content_or_none(db)}
    # Act
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'yaml_path'",
        ("/home/agent/.scitex/cards/tasks.yaml",),
    )
    conn.commit()
    conn.close()
    # Assert
    damaged = damaged_candidates(before, (db,))
    assert len(damaged) == 1 and "schema_meta CHANGED" in damaged[0][1]


def test_end_to_end_detects_a_deleted_store(tmp_path):
    """Readable at snapshot time, gone at teardown."""
    # Arrange
    db = tmp_path / "cards.db"
    _make_store(db, count=5)
    before = {db: content_or_none(db)}
    # Act
    db.unlink()
    # Assert
    damaged = damaged_candidates(before, (db,))
    assert len(damaged) == 1 and "UNREADABLE" in damaged[0][1]


def test_absent_candidate_is_silent_and_creates_nothing(tmp_path):
    """A candidate path that does not exist must not be reported, or created."""
    # Arrange
    missing = tmp_path / "never-existed.db"
    before = {missing: content_or_none(missing)}
    # Act / Assert
    assert damaged_candidates(before, (missing,)) == []
    assert not missing.exists(), "read-only probe must never create the store"


# EOF
