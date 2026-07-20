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

from scitex_cards import _dual_write, _store, _store_backend
from scitex_cards._db import ENV_DB


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
# RULE 1 (residual) — the mirror flag is OFF unless explicitly switched on.
# --------------------------------------------------------------------------


def test_mirror_is_off_by_default(monkeypatch, tmp_path):
    """The write path of the fleet's critical store does not get a flag day."""
    # Arrange
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    # Act
    on = _dual_write.enabled()

    # Assert
    assert on is False


# --------------------------------------------------------------------------
# RULE 3 (residual) — mirror-DIVERGENCE HEALTH still reports on `_failures`.
#
# The flag-gated mirror-on-write path (`mirror_after_save`) is gone with the
# YAML->SQLite cutover: SQLite is now canonical and a write RAISES rather than
# being counted-and-swallowed (see `_store_backend.write_doc_to_db`). What
# SURVIVES is `check_mirror_healthy`, still wired into `scitex-cards health`, so
# the divergence-health tests below keep their subject and stay.
# --------------------------------------------------------------------------


# A single failure means the DB no longer matches the YAML. There is no partial
# credit for a store that is only mostly right — and a health check that shrugs at
# divergence is how S2 ends up cutting over to a store that is confidently wrong.
def _health_after_one_recorded_failure(monkeypatch):
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    _dual_write._failures.append("sqlite3.OperationalError: disk I/O error")
    return _dual_write.check_mirror_healthy()


def test_health_reports_a_diverged_mirror_as_not_ok(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert
    assert res["ok"] is False


def test_health_names_the_divergence_in_its_detail(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert
    assert "DIVERGED" in res["detail"]


def test_health_hints_the_actual_repair_command(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert — names the actual repair, not a vague "check the logs".
    assert "db import" in (res["hint"] or "")


def test_health_is_ok_when_the_mirror_is_off(monkeypatch):
    # Arrange
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    # Act
    res = _dual_write.check_mirror_healthy()

    # Assert
    assert res["ok"] is True


# --------------------------------------------------------------------------
# THE NEAR-MISS — pinned so it can never come back.
# --------------------------------------------------------------------------


def test_a_card_write_does_not_touch_the_messages_table(monkeypatch, tmp_path):
    """DM threads MUST survive a card write.

    `mirror_doc_incremental` is now the SOLE canonical write primitive, and the
    near-miss it guards is unchanged by the cutover: the doc carries `tasks`
    (and the doc-owned sections), never `messages`, which is derived from the
    threads.yaml SIDECAR. A write that rebuilt `messages` from the doc would
    DELETE EVERY DM THREAD ON EVERY CARD WRITE — exactly the kind of thing that
    gets "helpfully" reintroduced by someone tidying up the clear-list. A table
    must be owned by exactly the file that produces it.
    """
    # Arrange — one canonical write builds the DB, then a DM lands in `messages`.
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {"id": "a", "title": "A", "status": "deferred", "assignee": "tester"}
            ]
        },
        store,
    )
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages(id, thread_key, sender, recipient, body, ts, read) "
        "VALUES('m1','dm:x::y','x','y','hello','2026-07-12T00:00:00Z',0)"
    )
    conn.commit()
    conn.close()

    # Act — a second canonical write runs the same mirror primitive again.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {"id": "a", "title": "A", "status": "deferred", "assignee": "tester"},
                {"id": "b", "title": "B", "status": "deferred", "assignee": "tester"},
            ]
        },
        store,
    )

    # Assert
    conn = sqlite3.connect(str(db))
    try:
        surviving = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    assert surviving == 1, "the write DELETED a DM thread on a card write"


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
def _flag_on_without_an_incremental_mirror(monkeypatch) -> None:
    """Env var ON, but the code cannot honour it."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: False)
    monkeypatch.setattr(_dual_write, "_refusal_logged", False)


def test_flag_is_REFUSED_when_the_code_has_no_incremental_mirror(monkeypatch, caplog):
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        on = _dual_write.enabled()

    # Assert
    assert on is False, (
        "dual-write MUST refuse to run on code without the incremental mirror — "
        "honouring it there falls back to the full rebuild: 135 s per card write"
    )


def test_the_refusal_names_itself_in_the_log(monkeypatch, caplog):
    """It must not fail in silence: a refusal nobody sees is a 35x slowdown nobody debugs."""
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        _dual_write.enabled()

    # Assert
    assert "REFUSING TO DUAL-WRITE" in caplog.text


def test_the_refusal_log_hints_how_to_recover_the_mirror(monkeypatch, caplog):
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        _dual_write.enabled()

    # Assert
    assert "db import" in caplog.text, "the hint must say how to recover the mirror"


def test_flag_is_HONOURED_when_the_code_does_have_the_incremental_mirror(monkeypatch):
    """The guard must not be a blanket off-switch — real code must still dual-write."""
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: True)

    # Act
    on = _dual_write.enabled()

    # Assert
    assert on is True


# The probe imports the FUNCTION. It must never trust a version string: a version
# string is metadata, and metadata lies — an orphaned .dist-info, a stale wheel, a
# container image baked months ago all report a version that outlived the code beside
# them. This repo has been bitten by exactly that. The only honest question is "is the
# function here?", so the probe answers it by importing it.
def test_the_guard_probe_finds_the_shipped_incremental_mirror():
    # Arrange
    # Act
    found = _dual_write._has_incremental_mirror()

    # Assert
    assert found is True, (
        "the shipped code HAS _db_mirror.mirror_doc_incremental, so the probe must find it"
    )


def test_the_guard_asks_for_a_SYMBOL_not_a_version_string():
    # Arrange
    import scitex_cards._db_mirror as m

    # Act
    symbol = m.mirror_doc_incremental

    # Assert — a real, importable callable, not a version claim about one.
    assert callable(symbol)


def test_the_refusal_is_logged_ONCE_not_once_per_write(monkeypatch, caplog):
    """A 135 s bug deserves a loud message. The same message on every write is noise.

    An alert that fires constantly on something the reader cannot act on teaches them to
    ignore the channel — which is how the next real failure goes unread.
    """
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        for _ in range(5):
            _dual_write.enabled()

    # Assert
    assert caplog.text.count("REFUSING TO DUAL-WRITE") == 1


def test_every_repeated_call_still_refuses_the_flag(monkeypatch, caplog):
    """Logging once must not mean RELENTING once — every call still refuses."""
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        results = [_dual_write.enabled() for _ in range(5)]

    # Assert
    assert results == [False] * 5


# --------------------------------------------------------------------------- #
# The mirror belongs to ONE store (2026-07-19 incident)                        #
# --------------------------------------------------------------------------- #
#
# The package's own concurrency test copies os.environ into writer subprocesses,
# so they inherited SCITEX_CARDS_DUAL_WRITE=1, wrote to a pytest tmp store, and
# rebuilt the LIVE fleet DB from a 21-card fixture — replacing 2,136 real cards.
# The three tests below pin the rule that makes that unrepresentable, and the two
# controls guard the opposite error of refusing legitimate mirrors.


def _mirror_of_store_a(monkeypatch, tmp_path):
    """A DB holding A's card, STAMPED for a FOREIGN identity (not its own path).

    Single-identity model: the store IS the database, so "a database that
    belongs to a different store" is one whose provenance stamp names a
    DIFFERENT database path than ``$SCITEX_CARDS_DB`` resolves. The ownership
    guard must refuse a write or read against it. Built by seeding the rows then
    stamping the provenance for a foreign path by hand (the production write
    door stamps the database's OWN path, so it cannot manufacture this).
    """
    from conftest import seed_db_from_doc

    from scitex_cards._db import connect
    from scitex_cards._db_freshness import stamp_store_provenance

    db = tmp_path / "mirror-of-a.db"
    foreign = tmp_path / "a" / "cards.db"  # a DIFFERENT database path
    monkeypatch.setenv(ENV_DB, str(db))
    doc = {
        "tasks": [
            {
                "id": "a-only",
                "title": "A",
                "status": "deferred",
                "assignee": "agent:test-suite",
            }
        ]
    }
    seed_db_from_doc(doc, str(db))
    conn = connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_provenance(conn, foreign)
        conn.commit()
    finally:
        conn.close()
    return db


def test_the_mirror_of_store_a_really_holds_store_as_card(monkeypatch, tmp_path):
    # Arrange
    # Act
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Assert — the precondition every foreign-write test below rests on.
    assert "a-only" in _db_ids(db), "precondition: the DB mirrors store A"


def test_a_write_to_a_foreign_store_does_not_clobber_another_stores_mirror(
    monkeypatch, tmp_path
):
    """The incident, in miniature: store B must not overwrite store A's mirror.

    The public card verb is protected, not only the low-level primitive: store B
    is the resolved store, but the DB it resolves is stamped for store A, so the
    read door of `add_task` RAISES before any row is touched.
    """
    # Arrange — a DB stamped for a FOREIGN identity, holding A's card.
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Act — the resolved database is foreign-stamped, so the read door of
    # add_task RAISES before any row is touched (the `store` arg is cosmetic —
    # identity is the database path).
    store_b = tmp_path / "b" / "cards.db"
    with pytest.raises(RuntimeError):
        _store.add_task(store_b, id="b-only", title="B", assignee="agent:test-suite")

    # Assert — A's mirror is untouched; B never entered it.
    assert _db_ids(db) == {"a-only"}, (
        "a write to store B must not reach the DB that mirrors store A — "
        "mirroring is a REPLACE, so this is how a fixture destroys a live board"
    )


def test_an_unstamped_db_is_adoptable_so_a_fresh_mirror_still_bootstraps(
    monkeypatch, tmp_path
):
    """Control: refusing must not break the FIRST write to a brand-new mirror."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "fresh.db"
    monkeypatch.setenv(ENV_DB, str(db))

    # Act — the first canonical write to an un-adopted DB must claim it.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "first",
                    "title": "first",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        store,
    )

    # Assert
    assert "first" in _db_ids(db), (
        "an unstamped DB has no store to protect, so the first write claims it"
    )


def test_a_store_writing_to_its_own_mirror_is_not_refused(monkeypatch, tmp_path):
    """Control: the normal case — repeated writes to one's own mirror keep working."""
    # Arrange — first write adopts the DB and stamps it for `store`.
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "one",
                    "title": "one",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        store,
    )

    # Act — a second write to that same store's own mirror must NOT be refused.
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "one",
                    "title": "one",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                },
                {
                    "id": "two",
                    "title": "two",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                },
            ]
        },
        store,
    )

    # Assert
    assert _db_ids(db) == {"one", "two"}, (
        "a store writing to the DB stamped for that same store must mirror normally"
    )


# The second door. mirror_after_save declines; the CANONICAL path must RAISE.
#
# Guarding only the mirror path left this one open, and the same pytest run that
# first rebuilt the live DB from a fixture (2,136 -> 21) did it again through here,
# harder (2,138 -> 1). Declining quietly is right when YAML still holds the card and
# wrong when the DB is the only copy: it would report success for a card that was
# never stored.
def test_canonical_write_to_a_foreign_db_RAISES_rather_than_clobbering(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "cards.db"

    # Act
    # Assert — a canonical write to a foreign-stamped database must refuse LOUDLY.
    with pytest.raises(RuntimeError, match="DIFFERENT"):
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)


def test_a_refused_canonical_write_leaves_the_foreign_rows_untouched(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "cards.db"

    # Act — the refused write. (Captured, not `pytest.raises`: the refusal
    # itself is pinned by the test above; here it is only the setup for the
    # single question this test asks — did any row change?)
    try:
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)
    except RuntimeError:
        pass

    # Assert — store A's rows are untouched. (Had the write been honoured
    # instead of refused, `b-only` would be here and this would fail.)
    assert _db_ids(db) == {"a-only"}


#: WHY THE NEXT TWO EXIST, and why they are a PAIR. On 2026-07-19 the WRITE
#: door refused foreign stores correctly all day while the READ door happily
#: returned them. That asymmetry is how a packaged fixture came to be read AS
#: THE BOARD for hours: nothing objected until someone tried to write, by which
#: point the wrong rows were already being treated as authoritative.
#: `_read_canonical_db_or_raise` is a read-MODIFY-write helper, so what the
#: write door would refuse has to fail at the read door too.
#: They are split because one alone is not evidence. A refusal test alone
#: passes for a guard wired to refuse EVERYTHING — which is the same
#: always-red uselessness as an always-green gate, and this codebase has
#: shipped that shape more than once. The healthy-read test is what proves the
#: guard discriminates rather than merely fires.
def test_reading_a_foreign_stamped_db_RAISES_rather_than_returning_its_rows(
    monkeypatch, tmp_path
):
    # Arrange — a database whose provenance names a DIFFERENT database path.
    from scitex_cards._store import _read_canonical_db_or_raise

    _mirror_of_store_a(monkeypatch, tmp_path)

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="REFUSING TO READ"):
        _read_canonical_db_or_raise()


def test_reading_the_db_that_owns_this_store_returns_its_cards(monkeypatch, tmp_path):
    # Arrange — a database stamped for its OWN path (the normal case): the first
    # canonical write adopts the fresh database and stamps its own identity.
    from scitex_cards._store import _read_canonical_db_or_raise

    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": "a-only",
                    "title": "A",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
            ]
        },
        tmp_path / "cosmetic.db",
    )

    # Act
    doc = _read_canonical_db_or_raise()

    # Assert — the guard lets the OWNING store through.
    assert [t["id"] for t in doc["tasks"]] == ["a-only"]


def test_a_missing_canonical_db_RAISES_instead_of_reading_an_empty_store(
    monkeypatch, tmp_path
):
    """A failed READ must never become a write of nothing.

    In canonical mode the value returned here is written back as the WHOLE
    store, so `export_doc` answering a missing database with a well-formed
    {"tasks": []} is not "no cards" but "delete every card". That is not
    theoretical: one comment_task call took the live board from 2,138 cards to
    3 this way. Type-checking the result cannot catch it — the empty document
    is indistinguishable from a real empty board — so the check asks the file
    system instead.
    """
    # Arrange — point at a database that does not exist.
    from scitex_cards._store import _read_canonical_db_or_raise

    monkeypatch.setenv(ENV_DB, str(tmp_path / "not-here.db"))

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="does not exist"):
        _read_canonical_db_or_raise()
