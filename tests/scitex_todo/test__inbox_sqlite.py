#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SQLite inbox backend (Phase 1 of the store migration).

Real sqlite temp DBs, NO mocks (STX-NM / PA-306): every test writes to a real
DB under ``tmp_path``'s runtime dir (resolved from an explicit ``store=``).
Covers the semantics the YAML path guarantees, re-implemented against SQLite:

* round-trip enqueue -> poll -> ack / mark_seen advances the cursor;
* dedup on ``(event_type, card_id, ts, actor)`` — a re-emit yields one record;
* ``supersede=True`` drops UNSEEN predecessors matching ``(event_type, card_id)``
  while leaving SEEN history untouched;
* ``unseen_only`` filter + full-history read;
* the migration verb copies YAML ``inboxes:`` records into SQLite, idempotently,
  without deleting the YAML section;
* the backend switch (``SCITEX_TODO_INBOX_BACKEND=sqlite``) routes the public
  ``_inbox`` API onto the SQLite implementation.
"""

from __future__ import annotations

from scitex_todo import _inbox_sqlite as sq


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


# --------------------------------------------------------------------------- #
# db path + schema                                                            #
# --------------------------------------------------------------------------- #
def test_db_path_lives_under_runtime_dir(tmp_path):
    store = _store(tmp_path)
    p = sq.inbox_db_path(store)
    # <store_dir>/runtime/todo.db per the SciTeX runtime-DB convention.
    assert p.parent.name == "runtime"
    assert p.parent.parent == tmp_path
    assert p.name == "todo.db"


def test_schema_has_recipient_seen_index(tmp_path):
    store = _store(tmp_path)
    # Trigger creation via an enqueue, then inspect the schema.
    sq.enqueue(
        "u_abc", event_type="completed", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    with sq.open_connection(sq.inbox_db_path(store)) as conn:
        idx = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        # An index specifically on (recipient, seen) exists.
        assert "idx_inbox_recipient_seen" in idx
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"


# --------------------------------------------------------------------------- #
# enqueue -> poll -> ack / mark_seen                                          #
# --------------------------------------------------------------------------- #
def test_enqueue_then_poll_returns_unseen_record(tmp_path):
    store = _store(tmp_path)
    rec = sq.enqueue(
        "u_abc", event_type="reassigned", card_id="c1",
        body="Card c1 reassigned to you", actor="bob",
        ts="2026-06-26T00:00:00Z", store=store,
    )
    assert rec is not None
    assert rec["seen"] is False
    assert rec["card_id"] == "c1"
    assert rec["id"].startswith("n_")

    got = sq.poll_inbox("u_abc", store=store)
    assert len(got) == 1
    assert got[0]["card_id"] == "c1"
    assert got[0]["body"] == "Card c1 reassigned to you"
    assert got[0]["seen"] is False


def test_mark_seen_advances_cursor(tmp_path):
    store = _store(tmp_path)
    sq.enqueue(
        "u_abc", event_type="completed", card_id="c1", body="done",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    first = sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    assert [r["card_id"] for r in first] == ["c1"]
    assert first[0]["seen"] is True
    # A second unseen-only poll returns nothing new.
    assert sq.poll_inbox("u_abc", unseen_only=True, store=store) == []
    # ...but the full history still has it (seen=True).
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    assert [r["card_id"] for r in history] == ["c1"]
    assert history[0]["seen"] is True


def test_ack_marks_specific_ids_seen(tmp_path):
    store = _store(tmp_path)
    r1 = sq.enqueue(
        "u_abc", event_type="completed", card_id="c1", body="a",
        actor="bob", ts="2026-06-26T00:00:01Z", store=store,
    )
    r2 = sq.enqueue(
        "u_abc", event_type="completed", card_id="c2", body="b",
        actor="bob", ts="2026-06-26T00:00:02Z", store=store,
    )
    flipped = sq.ack("u_abc", [r1["id"]], store=store)
    assert flipped == [r1["id"]]
    unseen = sq.poll_inbox("u_abc", unseen_only=True, store=store)
    assert [r["id"] for r in unseen] == [r2["id"]]
    # Acking again is a no-op (already seen); unknown id is a no-op.
    assert sq.ack("u_abc", [r1["id"]], store=store) == []
    assert sq.ack("u_abc", ["n_nope"], store=store) == []


def test_poll_preserves_append_order(tmp_path):
    store = _store(tmp_path)
    for i in range(3):
        sq.enqueue(
            "u_abc", event_type="completed", card_id=f"c{i}", body="x",
            actor="bob", ts=f"2026-06-26T00:00:0{i}Z", store=store,
        )
    got = sq.poll_inbox("u_abc", store=store)
    assert [r["card_id"] for r in got] == ["c0", "c1", "c2"]


# --------------------------------------------------------------------------- #
# dedup + supersede                                                           #
# --------------------------------------------------------------------------- #
def test_enqueue_dedups_same_event_key(tmp_path):
    store = _store(tmp_path)
    kwargs = dict(
        event_type="reassigned", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    first = sq.enqueue("u_abc", **kwargs)
    second = sq.enqueue("u_abc", **kwargs)  # exact re-emit
    assert first is not None
    assert second is None
    assert len(sq.poll_inbox("u_abc", unseen_only=False, store=store)) == 1
    # A DIFFERENT ts (a genuine second event) is kept separately.
    third = sq.enqueue("u_abc", **{**kwargs, "ts": "2026-06-26T00:00:05Z"})
    assert third is not None
    assert len(sq.poll_inbox("u_abc", unseen_only=False, store=store)) == 2


def test_enqueue_dedup_with_none_actor(tmp_path):
    # actor=None must be handled by the dedup (NULL-safe), not double-enqueued.
    store = _store(tmp_path)
    kwargs = dict(
        event_type="completed", card_id="c1", body="x",
        actor=None, ts="2026-06-26T00:00:00Z", store=store,
    )
    assert sq.enqueue("u_abc", **kwargs) is not None
    assert sq.enqueue("u_abc", **kwargs) is None
    assert len(sq.poll_inbox("u_abc", unseen_only=False, store=store)) == 1


def test_supersede_replaces_unseen_predecessors(tmp_path):
    store = _store(tmp_path)
    # Two digests for the same (event_type, card_id) at different ts.
    sq.enqueue(
        "u_abc", event_type="digest", card_id="__digest__", body="old",
        actor=None, ts="2026-06-26T00:00:00Z", supersede=True, store=store,
    )
    sq.enqueue(
        "u_abc", event_type="digest", card_id="__digest__", body="new",
        actor=None, ts="2026-06-26T00:00:05Z", supersede=True, store=store,
    )
    unseen = sq.poll_inbox("u_abc", unseen_only=True, store=store)
    # Only the newest unseen digest survives.
    assert [r["body"] for r in unseen] == ["new"]


def test_supersede_keeps_seen_history(tmp_path):
    store = _store(tmp_path)
    sq.enqueue(
        "u_abc", event_type="digest", card_id="__digest__", body="old",
        actor=None, ts="2026-06-26T00:00:00Z", supersede=True, store=store,
    )
    # Mark the first digest seen (history), then supersede.
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    sq.enqueue(
        "u_abc", event_type="digest", card_id="__digest__", body="new",
        actor=None, ts="2026-06-26T00:00:05Z", supersede=True, store=store,
    )
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # SEEN predecessor is preserved; the new unseen digest is appended.
    assert [r["body"] for r in history] == ["old", "new"]
    assert [r["seen"] for r in history] == [True, False]


def test_falsy_recipient_and_empty_inbox_are_safe(tmp_path):
    store = _store(tmp_path)
    assert sq.enqueue("", event_type="completed", card_id="c1", body="x",
                      actor=None, store=store) is None
    assert sq.poll_inbox("", store=store) == []
    assert sq.poll_inbox("u_nobody", store=store) == []
    assert sq.ack("u_nobody", ["n_x"], store=store) == []


# --------------------------------------------------------------------------- #
# migration YAML -> SQLite                                                    #
# --------------------------------------------------------------------------- #
def test_migrate_copies_yaml_records(tmp_path):
    from scitex_todo import _inbox as yaml_inbox

    store = _store(tmp_path)
    # Seed the YAML inbox section (default backend).
    a = yaml_inbox.enqueue(
        "u_abc", event_type="reassigned", card_id="c1", body="hi",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    b = yaml_inbox.enqueue(
        "dave", event_type="completed", card_id="c2", body="bye",
        actor="carol", ts="2026-06-26T00:00:01Z", store=store,
    )
    # Mark one seen so the seen flag carries across.
    yaml_inbox.ack("dave", [b["id"]], store=store)

    stats = sq.migrate_to_sqlite(store=store)
    assert stats["records"] == 2
    assert stats["inserted"] == 2

    # Records readable from SQLite with the right recipients + seen flags.
    got_abc = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    got_dave = sq.poll_inbox("dave", unseen_only=False, store=store)
    assert [r["id"] for r in got_abc] == [a["id"]]
    assert got_abc[0]["seen"] is False
    assert [r["id"] for r in got_dave] == [b["id"]]
    assert got_dave[0]["seen"] is True

    # The YAML section is NOT deleted (reversible).
    import yaml as _yaml
    doc = _yaml.safe_load(store.read_text(encoding="utf-8"))
    assert set(doc["inboxes"].keys()) == {"u_abc", "dave"}


def test_migrate_is_idempotent(tmp_path):
    from scitex_todo import _inbox as yaml_inbox

    store = _store(tmp_path)
    yaml_inbox.enqueue(
        "u_abc", event_type="reassigned", card_id="c1", body="hi",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    first = sq.migrate_to_sqlite(store=store)
    second = sq.migrate_to_sqlite(store=store)
    assert first["inserted"] == 1
    assert second["inserted"] == 0  # dedup on notification id
    assert second["skipped"] == 1
    assert len(sq.poll_inbox("u_abc", unseen_only=False, store=store)) == 1


# --------------------------------------------------------------------------- #
# backend switch through the public _inbox API                                #
# --------------------------------------------------------------------------- #
def test_backend_switch_routes_to_sqlite(tmp_path, env):
    import yaml as _yaml

    from scitex_todo import _inbox

    store = _store(tmp_path)
    env.set("SCITEX_TODO_INBOX_BACKEND", "sqlite")

    rec = _inbox.enqueue(
        "u_abc", event_type="completed", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    assert rec is not None
    # Read back through the public API (still routed to sqlite).
    got = _inbox.poll_inbox("u_abc", store=store)
    assert [r["card_id"] for r in got] == ["c1"]

    # The write went to SQLite, NOT the YAML store (no inboxes: section on disk,
    # since the sqlite path never touched tasks.yaml).
    if store.exists():
        doc = _yaml.safe_load(store.read_text(encoding="utf-8")) or {}
        assert not doc.get("inboxes")
    # The SQLite DB file exists and holds the row.
    assert sq.inbox_db_path(store).exists()


def test_default_backend_is_yaml(tmp_path, env):
    import yaml as _yaml

    from scitex_todo import _inbox

    store = _store(tmp_path)
    env.delete("SCITEX_TODO_INBOX_BACKEND")
    _inbox.enqueue(
        "u_abc", event_type="completed", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    # Default path writes the YAML inboxes: section; no sqlite DB created.
    doc = _yaml.safe_load(store.read_text(encoding="utf-8"))
    assert doc["inboxes"]["u_abc"][0]["card_id"] == "c1"
    assert not sq.inbox_db_path(store).exists()


# --------------------------------------------------------------------------- #
# CLI verb                                                                     #
# --------------------------------------------------------------------------- #
def test_cli_migrate_to_sqlite(tmp_path):
    from click.testing import CliRunner

    from scitex_todo import _inbox as yaml_inbox
    from scitex_todo._cli import main

    store = _store(tmp_path)
    yaml_inbox.enqueue(
        "u_abc", event_type="reassigned", card_id="c1", body="hi",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )

    runner = CliRunner()
    result = runner.invoke(
        main, ["inbox", "migrate-to-sqlite", "--tasks", str(store), "-y"]
    )
    assert result.exit_code == 0, result.output
    assert sq.inbox_db_path(store).exists()
    assert len(sq.poll_inbox("u_abc", unseen_only=False, store=store)) == 1


def test_cli_migrate_dry_run_writes_nothing(tmp_path):
    from click.testing import CliRunner

    from scitex_todo import _inbox as yaml_inbox
    from scitex_todo._cli import main

    store = _store(tmp_path)
    yaml_inbox.enqueue(
        "u_abc", event_type="reassigned", card_id="c1", body="hi",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )

    runner = CliRunner()
    result = runner.invoke(
        main, ["inbox", "migrate-to-sqlite", "--tasks", str(store), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    # Dry-run creates no DB file.
    assert not sq.inbox_db_path(store).exists()


# EOF
