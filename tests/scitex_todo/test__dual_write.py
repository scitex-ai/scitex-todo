#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 dual-write: mirror every card write into SQLite, YAML still canonical.

THE NUMBER THAT JUSTIFIES ALL OF THIS (measured on the live store, 1,257 cards):

    full YAML rewrite  : 11,176 ms   <- the cost of EVERY card write, today
    full SQLite rebuild:  1,243 ms   <- what the mirror adds (+11%)
    ONE row update     :      4.71 ms  <- what S2 buys (2,375x)

Every card write takes ELEVEN SECONDS while holding a fleet-wide lock. That is the
convoy, entire. These tests pin the three rules that make the migration to fix it
safe to run under real traffic.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_todo import _dual_write, _store
from scitex_todo._db import ENV_DB, resolve_db_path


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Each test gets its own store + DB, and a clean failure counter.

    The env var is ``SCITEX_TODO_DB`` (``_db.ENV_DB``) — checked, not assumed.
    """
    _dual_write.reset_failures()
    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    yield
    _dual_write.reset_failures()


def _db_ids(db_path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM tasks")}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# RULE 1 — the mirror is OFF unless explicitly switched on.
# --------------------------------------------------------------------------


def test_mirror_is_off_by_default(monkeypatch, tmp_path):
    """The write path of the fleet's critical store does not get a flag day."""
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    assert _dual_write.enabled() is False


def test_a_card_write_with_the_mirror_off_touches_no_db(monkeypatch, tmp_path):
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)
    store = tmp_path / "tasks.yaml"

    _store.add_task(store, id="a", title="A", assignee="tester")

    assert not resolve_db_path().exists()


# --------------------------------------------------------------------------
# RULE 2 — with the mirror ON, the DB tracks the YAML.
# --------------------------------------------------------------------------


def test_mirror_writes_the_card_into_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    _store.add_task(store, id="a", title="A", assignee="tester")

    assert _db_ids(resolve_db_path()) == {"a"}


def test_mirror_tracks_a_delete(monkeypatch, tmp_path):
    """A rebuild-mirror must DROP rows that left the YAML, not just add new ones."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="tester")
    _store.add_task(store, id="b", title="B", assignee="tester")

    _store.delete_task(store, "a")

    assert _db_ids(resolve_db_path()) == {"b"}


def test_mirror_leaves_no_failures_on_the_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    _store.add_task(store, id="a", title="A", assignee="tester")

    assert _dual_write.failure_count() == 0


# --------------------------------------------------------------------------
# RULE 3 — a mirror failure NEVER costs the user their card, and NEVER hides.
#
# This is the load-bearing pair. The YAML is canonical: by the time the mirror
# runs, the card is already durably on disk. Raising there would turn a cosmetic
# problem into DATA LOSS. But swallowing it quietly would let the DB rot out of
# sync while every check reports green — and S2 would then cut the fleet over to
# a store that is confidently wrong.
# --------------------------------------------------------------------------


def test_a_mirror_failure_does_not_fail_the_card_write(monkeypatch, tmp_path):
    """The card MUST still be written. The user's data is not the mirror's hostage."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    def boom(doc, db_path=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("scitex_todo._db_mirror.mirror_doc_incremental", boom)

    _store.add_task(store, id="a", title="A", assignee="tester")  # must NOT raise

    assert [t["id"] for t in _store.list_tasks(store)] == ["a"]


def test_a_mirror_failure_is_counted_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    def boom(doc, db_path=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("scitex_todo._db_mirror.mirror_doc_incremental", boom)
    _store.add_task(store, id="a", title="A", assignee="tester")

    assert _dual_write.failure_count() == 1


def test_a_mirror_failure_is_logged_loud(monkeypatch, tmp_path, caplog):
    """Silence is the failure mode this whole codebase spent two days digging out of."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    def boom(doc, db_path=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("scitex_todo._db_mirror.mirror_doc_incremental", boom)

    with caplog.at_level("ERROR"):
        _store.add_task(store, id="a", title="A", assignee="tester")

    assert "DUAL-WRITE MIRROR FAILED" in caplog.text


def test_health_reports_a_diverged_mirror_as_UNHEALTHY(monkeypatch):
    """A single failure means the DB no longer matches the YAML.

    There is no partial credit for a store that is only mostly right — and a
    health check that shrugs at divergence is how S2 ends up cutting over to a
    store that is confidently wrong.
    """
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    _dual_write._failures.append("sqlite3.OperationalError: disk I/O error")

    res = _dual_write.check_mirror_healthy()

    assert res["ok"] is False
    assert "DIVERGED" in res["detail"]
    assert "db import" in (res["hint"] or "")  # names the actual repair


def test_health_is_ok_when_the_mirror_is_off(monkeypatch):
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    assert _dual_write.check_mirror_healthy()["ok"] is True


# --------------------------------------------------------------------------
# THE NEAR-MISS — pinned so it can never come back.
# --------------------------------------------------------------------------


def test_the_mirror_does_not_touch_the_messages_table(monkeypatch, tmp_path):
    """DM threads MUST survive a card write.

    My first design rebuilt EVERY table from the doc — including `messages`, which
    is derived from the threads.yaml SIDECAR and not from the doc at all. That
    would have DELETED EVERY DM THREAD ON EVERY CARD WRITE.

    Caught while writing it, and pinned here because it is exactly the kind of
    thing that gets "helpfully" reintroduced by someone tidying up the clear-list.
    A table must be owned by exactly the file that produces it.
    """
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="tester")

    db = resolve_db_path()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages(id, thread_key, sender, recipient, body, ts, read) "
        "VALUES('m1','dm:x::y','x','y','hello','2026-07-12T00:00:00Z',0)"
    )
    conn.commit()
    conn.close()

    # A card write now runs the mirror again.
    _store.add_task(store, id="b", title="B", assignee="tester")

    conn = sqlite3.connect(str(db))
    try:
        surviving = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()

    assert surviving == 1, "the mirror DELETED a DM thread on a card write"
