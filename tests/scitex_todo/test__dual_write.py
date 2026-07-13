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


# --------------------------------------------------------------------------- #
# VERSION GUARD: the flag must REFUSE code that cannot honour it              #
# --------------------------------------------------------------------------- #
# This guard exists because the flag alone cost 135 SECONDS PER CARD WRITE.
#
# MEASURED on the live 1,449-card board, in the configuration the fleet was really
# running (2026-07-13):
#     scitex-todo 0.9.4, dual-write ON  : add_task()    = 135.2 s
#     scitex-todo 0.9.4, dual-write OFF : delete_task() =   3.8 s      -> 35x
#
# The flag was switched on because the incremental mirror had shipped — and it had, on
# PyPI. But the fleet runs a wheel BAKED INTO A CONTAINER IMAGE, still on 0.9.4. So the
# flag did not enable the incremental mirror; it enabled the FULL REBUILD that the
# incremental mirror had replaced.
#
# The precondition was real, agreed, and written down — in a MESSAGE BETWEEN TWO AGENTS.
# A precondition that lives only in a message is not a precondition; it is a hope. It
# lives in the code now, and these tests are what keep it there.
def test_flag_is_REFUSED_when_the_code_has_no_incremental_mirror(monkeypatch, caplog):
    """Env var ON + no incremental mirror => enabled() is False, and it says so LOUDLY."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: False)
    monkeypatch.setattr(_dual_write, "_refusal_logged", False)

    with caplog.at_level("ERROR"):
        assert _dual_write.enabled() is False, (
            "dual-write MUST refuse to run on code without the incremental mirror — "
            "honouring it there falls back to the full rebuild: 135 s per card write"
        )

    # It must not fail in silence: a refusal nobody sees is a 35x slowdown nobody debugs.
    assert "REFUSING TO DUAL-WRITE" in caplog.text
    assert "db import" in caplog.text, "the hint must say how to recover the mirror"


def test_flag_is_HONOURED_when_the_code_does_have_the_incremental_mirror(monkeypatch):
    """The guard must not be a blanket off-switch — real code must still dual-write."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: True)
    assert _dual_write.enabled() is True


def test_the_guard_asks_for_a_SYMBOL_not_a_version_string():
    """The probe imports the FUNCTION. It must never trust a version string.

    A version string is metadata, and metadata lies — an orphaned .dist-info, a stale
    wheel, a container image baked months ago all report a version that outlived the code
    beside them. This repo has been bitten by exactly that. The only honest question is
    "is the function here?", so the probe answers it by importing it.
    """
    assert _dual_write._has_incremental_mirror() is True, (
        "the shipped code HAS _db_mirror.mirror_doc_incremental, so the probe must find it"
    )

    import scitex_todo._db_mirror as m

    assert callable(m.mirror_doc_incremental)


def test_the_refusal_is_logged_ONCE_not_once_per_write(monkeypatch, caplog):
    """A 135 s bug deserves a loud message. The same message on every write is noise.

    An alert that fires constantly on something the reader cannot act on teaches them to
    ignore the channel — which is how the next real failure goes unread.
    """
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: False)
    monkeypatch.setattr(_dual_write, "_refusal_logged", False)

    with caplog.at_level("ERROR"):
        for _ in range(5):
            assert _dual_write.enabled() is False

    assert caplog.text.count("REFUSING TO DUAL-WRITE") == 1
