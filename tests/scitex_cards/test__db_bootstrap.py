#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``import_from_yaml`` — the bootstrap AND the recovery path.

These tests exist because the board was destroyed twice (2026-07-19, 2026-07-20)
and the documented recovery command could not put it back. We had tests proving
the ownership guards REFUSE correctly, and none proving that RECOVERY SUCCEEDS —
so the guards were verified against the case they were written for and against
nothing else. Both halves are pinned here.

The two states you are ever in when recovering are (a) a database stamped for
some other store and (b) no database at all. Both are covered, in both
directions: the accident is refused, the restore works.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards._db_bootstrap import import_from_yaml


def _write_store(path, ids):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "tasks:\n"
        + "".join(f"- id: {i}\n  title: card {i}\n  status: deferred\n" for i in ids),
        encoding="utf-8",
    )
    return path


def _card_ids(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in conn.execute("SELECT id FROM tasks"))
    finally:
        conn.close()


def _stamp(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='yaml_path'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A live store + the database that belongs to it.

    THE DATABASE IS AMBIENT, THE SOURCE IS EXPLICIT — which is exactly the shape
    of the accident under test. A caller that isolates its YAML (explicitly, or
    via its own env) while leaving ``db_path=None`` gets the source it asked for
    and a destination nobody chose.

    The source is passed explicitly rather than through the store-resolution env
    because this suite's conftest already points that resolution at a shared
    test store: relying on it here made the fixture bootstrap from the wrong
    file, and the test then asserted against cards it had never inserted.
    """
    store = _write_store(tmp_path / "live" / "tasks.yaml", ["real-1", "real-2"])
    db = tmp_path / "live.db"
    monkeypatch.setenv("SCITEX_CARDS_DB", str(db))
    import_from_yaml(tasks_path=str(store))
    return store, db


def test_importing_a_foreign_store_into_the_ambient_database_is_refused(
    board, tmp_path
):
    # Arrange — a fixture store that has nothing to do with the live database,
    # exactly as a test that isolates its YAML but forgets $SCITEX_CARDS_DB.
    store, db = board
    fixture = _write_store(tmp_path / "fixture" / "tasks.yaml", ["toy-1"])

    # Act / Assert — refused, and the message names the escape hatch.
    with pytest.raises(RuntimeError, match="REFUSING to rebuild"):
        import_from_yaml(tasks_path=str(fixture))

    # Assert — the board is untouched. This is the whole point: a refusal that
    # still clobbered would be no better than the bug.
    assert _card_ids(db) == ["real-1", "real-2"]
    assert _stamp(db) == str(store)


def test_importing_the_store_that_owns_this_database_succeeds(board):
    # The PAIR of the test above. Neutering the guard must break exactly one of
    # the two: without this, "refuse everything" would pass the suite.
    store, db = board

    import_from_yaml(tasks_path=str(store))

    assert _card_ids(db) == ["real-1", "real-2"]
    assert _stamp(db) == str(store)


def test_restoring_from_a_snapshot_restores_the_cards_and_keeps_the_identity(
    board, tmp_path
):
    # Arrange — the board is wiped down to a fixture, as it was on 2026-07-20,
    # and a snapshot holds the real cards.
    store, db = board
    snapshot = _write_store(
        tmp_path / "snapshots" / "tasks.yaml", ["real-1", "real-2", "real-3"]
    )

    # Act — the documented restore: data from the snapshot, identity unchanged.
    summary = import_from_yaml(tasks_path=str(snapshot), as_store=str(store))

    # Assert — the cards came back...
    assert _card_ids(db) == ["real-1", "real-2", "real-3"]
    assert summary["tasks"] == 3
    # ...and the database still belongs to the LIVE store. Stamping it as the
    # snapshot is what made the 2026-07-19 recovery need a hand-written UPDATE:
    # every ordinary write afterwards is then correctly-but-uselessly refused.
    assert _stamp(db) == str(store)


def test_import_reads_the_yaml_it_is_given_not_the_canonical_database(
    board, tmp_path, monkeypatch
):
    # The silent no-op. Under the DB-canonical backend the loader used to ROUTE
    # away from the path it was handed and return the destination's own rows —
    # so a restore read the DB, wrote it back into itself, and reported a
    # summary indistinguishable from success.
    store, db = board
    monkeypatch.setenv("SCITEX_CARDS_STORE_BACKEND", "sqlite")
    snapshot = _write_store(tmp_path / "snap" / "tasks.yaml", ["from-the-file"])

    summary = import_from_yaml(tasks_path=str(snapshot), as_store=str(store))

    assert _card_ids(db) == ["from-the-file"], "read the backend, not the file"
    assert summary["tasks"] == 1


def test_bootstrapping_succeeds_when_no_database_exists_yet(tmp_path, monkeypatch):
    # The other recovery state. A missing database is the one case where the
    # canonical reader is guaranteed to raise, and it is also the case the error
    # message told the operator to fix with THIS command.
    store = _write_store(tmp_path / "live" / "tasks.yaml", ["real-1"])
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("SCITEX_CARDS_DB", str(db))
    monkeypatch.setenv("SCITEX_CARDS_STORE_BACKEND", "sqlite")
    assert not db.exists()

    import_from_yaml(tasks_path=str(store))

    assert _card_ids(db) == ["real-1"]


# EOF
