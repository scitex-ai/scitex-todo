#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recovery must be DB-to-DB, and every refusal must fire BEFORE the damage.

The operator ruled on 2026-07-20 that the database is the single source of truth and
YAML is not used at all. Until this module, every recovery path ran through YAML —
which is how the board was restored three times that night. Removing YAML without a
DB-level restore would leave one store and no way back.

These tests pin the properties that make it a recovery tool rather than a hazard.
Each asserts on the DATABASE, never on "the command did not raise".
"""

from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from scitex_cards._cli._db_recovery import backup_cmd, restore_cmd


def _make_db(path, rows: int) -> None:
    """A minimal but STRUCTURALLY VALID cards database."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE schema_meta (key TEXT, value TEXT)")
    conn.executemany(
        "INSERT INTO tasks (id, title) VALUES (?, ?)",
        [(f"card-{i}", f"title {i}") for i in range(rows)],
    )
    conn.commit()
    conn.close()


def _count(path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT count(*) FROM tasks").fetchone()[0])


def test_backup_writes_a_database_that_reads_back_with_the_same_cards(tmp_path):
    # Arrange
    src = tmp_path / "cards.db"
    _make_db(src, 40)
    out = tmp_path / "backup.db"

    # Act
    result = CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(out)])

    # Assert — on the artefact
    assert result.exit_code == 0, result.output
    assert _count(out) == 40


def test_backup_refuses_to_silently_clobber_an_existing_backup(tmp_path):
    # Arrange
    src = tmp_path / "cards.db"
    _make_db(src, 10)
    out = tmp_path / "backup.db"
    CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(out)])
    before = out.read_bytes()

    # Act
    result = CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(out)])

    # Assert — refused, and the existing backup is byte-identical
    assert result.exit_code != 0
    assert "REFUSING to overwrite" in result.output
    assert out.read_bytes() == before


def test_restore_brings_a_destroyed_board_back(tmp_path):
    # Arrange — the drill: a full backup, then a board emptied by a rogue DELETE
    src = tmp_path / "cards.db"
    _make_db(src, 40)
    backup = tmp_path / "backup.db"
    CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(backup)])
    with sqlite3.connect(src) as conn:
        conn.execute("DELETE FROM tasks")
    assert _count(src) == 0

    # Act
    result = CliRunner().invoke(
        restore_cmd, ["--from", str(backup), "--db", str(src)]
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert _count(src) == 40


def test_restore_archives_what_it_replaces(tmp_path):
    # Arrange
    src = tmp_path / "cards.db"
    _make_db(src, 40)
    backup = tmp_path / "backup.db"
    CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(backup)])

    # Act
    CliRunner().invoke(restore_cmd, ["--from", str(backup), "--db", str(src)])

    # Assert — a restore that is itself irreversible is not a recovery tool
    archived = list((tmp_path / ".old").rglob("cards.db"))
    assert archived, "restore did not archive the database it replaced"


def test_a_collapsing_restore_is_refused_BEFORE_the_board_is_touched(tmp_path):
    # Arrange — a 3-card backup aimed at a 40-card board
    live = tmp_path / "cards.db"
    _make_db(live, 40)
    tiny = tmp_path / "tiny.db"
    _make_db(tiny, 3)

    # Act
    result = CliRunner().invoke(restore_cmd, ["--from", str(tiny), "--db", str(live)])

    # Assert — this is the ordering lesson: the snapshot rail's floor fires AFTER
    # its import, so a correct refusal still arrives too late. Here the board must
    # be untouched, not merely un-recorded.
    assert result.exit_code != 0
    assert "REFUSING to restore" in result.output
    assert _count(live) == 40


def test_allow_shrink_is_the_only_way_through_a_collapse(tmp_path):
    # Arrange
    live = tmp_path / "cards.db"
    _make_db(live, 40)
    tiny = tmp_path / "tiny.db"
    _make_db(tiny, 3)

    # Act
    result = CliRunner().invoke(
        restore_cmd, ["--from", str(tiny), "--db", str(live), "--allow-shrink"]
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert _count(live) == 3


def test_an_unrelated_sqlite_file_is_refused_as_a_restore_source(tmp_path):
    # Arrange — a valid sqlite file that is NOT a cards database
    live = tmp_path / "cards.db"
    _make_db(live, 40)
    alien = tmp_path / "alien.db"
    with sqlite3.connect(alien) as conn:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")

    # Act
    result = CliRunner().invoke(restore_cmd, ["--from", str(alien), "--db", str(live)])

    # Assert — restoring an unrelated db over the board would be silent and total
    assert result.exit_code != 0
    assert "not a cards database" in result.output
    assert _count(live) == 40


def test_a_missing_restore_source_is_refused_rather_than_treated_as_empty(tmp_path):
    # Arrange — "could not ask" must never collapse into "zero cards"
    live = tmp_path / "cards.db"
    _make_db(live, 40)

    # Act
    result = CliRunner().invoke(
        restore_cmd, ["--from", str(tmp_path / "nope.db"), "--db", str(live)]
    )

    # Assert
    assert result.exit_code != 0
    assert _count(live) == 40


@pytest.mark.parametrize("rows", [0, 1, 500])
def test_backup_round_trips_exactly_at_several_sizes(tmp_path, rows):
    # Arrange
    src = tmp_path / f"cards-{rows}.db"
    _make_db(src, rows)
    out = tmp_path / f"backup-{rows}.db"

    # Act
    CliRunner().invoke(backup_cmd, ["--db", str(src), "--out", str(out)])

    # Assert
    assert _count(out) == rows
