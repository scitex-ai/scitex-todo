#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the S0 shadow-SQLite adapter + YAML bootstrap (RFC #348).

Real ``sqlite3`` + real ``tmp_path`` YAML fixtures — NO mocks. Every test
proves the S0 contract: the DB is a shadow bootstrapped from YAML, the schema
matches the RFC, and the import NEVER modifies the source YAML.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from scitex_cards import _db, _db_bootstrap, _model


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    """A tasks.yaml + threads.yaml pair exercising every child collection."""
    tasks_doc = {
        "tasks": [
            {
                "id": "c1",
                "title": "First card",
                "status": "in_progress",
                "task": "do the thing",
                "project": "scitex-cards",
                "repo": "scitex-cards",
                "agent": "agent:alice",
                "group": "core",
                "priority": 3,
                "depends_on": ["c2"],
                "blocks": ["c3"],
                "collaborators": ["bob"],
                "subscribers": ["carol", "bob"],
                "deadlines": ["2026-08-01", "2026-09-01 +1w"],
                "_log_meta": {"completed_by": "alice"},
                "comments": [
                    {"author": "alice", "ts": "2026-07-01T00:00:00Z", "text": "hi"},
                    {
                        "author": "bob",
                        "ts": "2026-07-02T00:00:00Z",
                        "text": "unblocked",
                        "kind": "unblock",
                    },
                ],
            },
            {"id": "c2", "title": "Second card", "status": "done"},
            {
                "id": "c3",
                "title": "Third card",
                "status": "blocked",
                "blocker": "dependency",
            },
        ],
        "users": [
            {
                "id": "u_aaaaaaaaaaaa",
                "kind": "agent",
                "names": ["alice", "proj-alice"],
                "host_at_name": "hostA@alice",
                "notify": {"telegram": True},
                "a2a_port": 7001,
                "created_at": "2026-06-01T00:00:00Z",
                "last_seen": "2026-07-01T00:00:00Z",
            },
            {"id": "u_bbbbbbbbbbbb", "kind": "human", "names": ["bob"]},
        ],
        "inboxes": {
            "u_aaaaaaaaaaaa": [
                {
                    "id": "n_111111111111",
                    "event_type": "reassigned",
                    "card_id": "c1",
                    "body": "Card c1 reassigned",
                    "actor": "bob",
                    "ts": "2026-07-03T00:00:00Z",
                    "seen": False,
                },
            ],
            "dave": [
                {
                    "id": "n_222222222222",
                    "event_type": "completed",
                    "card_id": "c2",
                    "body": "done",
                    "actor": "alice",
                    "ts": "2026-07-04T00:00:00Z",
                    "seen": True,
                },
            ],
        },
    }
    threads_doc = {
        "threads": {
            "dm:alice::bob": [
                {
                    "id": "m_111111111111",
                    "thread": "dm:alice::bob",
                    "from": "alice",
                    "to": "bob",
                    "body": "ping",
                    "ts": "2026-07-05T00:00:00Z",
                    "read": False,
                },
            ],
        },
    }
    tasks_path = tmp_path / "tasks.yaml"
    threads_path = tmp_path / "threads.yaml"
    _write_yaml(tasks_path, tasks_doc)
    _write_yaml(threads_path, threads_doc)
    return {
        "tasks_path": tasks_path,
        "threads_path": threads_path,
        "db_path": tmp_path / "todo.db",
    }


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #
def test_resolve_db_path_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(_db.ENV_DB, str(tmp_path / "env.db"))
    got = _db.resolve_db_path(tmp_path / "explicit.db")
    assert got == (tmp_path / "explicit.db")


def test_resolve_db_path_env_over_userpath(tmp_path, monkeypatch):
    monkeypatch.setenv(_db.ENV_DB, str(tmp_path / "env.db"))
    got = _db.resolve_db_path()
    assert got == (tmp_path / "env.db")


def test_resolve_db_path_delegates_to_user_path(tmp_path, monkeypatch):
    """Tier 3 DELEGATES to local_state.user_path — no re-rolled precedence."""
    monkeypatch.delenv(_db.ENV_DB, raising=False)
    from scitex_config._ecosystem import local_state

    calls = []
    sentinel = tmp_path / "delegated" / "todo.db"

    def fake_user_path(pkg_short, *parts):
        calls.append((pkg_short, parts))
        return sentinel

    monkeypatch.setattr(local_state, "user_path", fake_user_path)
    got = _db.resolve_db_path()
    assert got == sentinel
    assert calls == [("todo", ("todo.db",))]


# --------------------------------------------------------------------------- #
# Schema + PRAGMAs                                                            #
# --------------------------------------------------------------------------- #
def test_connect_sets_pragmas(tmp_path):
    conn = _db.connect(tmp_path / "p.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 300000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    finally:
        conn.close()


def test_open_db_creates_all_tables(tmp_path):
    conn = _db.open_db(tmp_path / "s.db")
    try:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for name in _db.SCHEMA_TABLES:
            assert name in present, name
    finally:
        conn.close()


def test_open_db_creates_expected_indexes(tmp_path):
    conn = _db.open_db(tmp_path / "s.db")
    try:
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        for name in (
            "idx_tasks_status",
            "idx_tasks_agent",
            "idx_tasks_assignee",
            "idx_tasks_scope",
            "idx_tasks_kind",
            "idx_tasks_blocker",
            "idx_tasks_project",
            "idx_tasks_deadline",
            "idx_tasks_parent",
            "idx_tasks_pr_url",
            "idx_comments_task",
            "idx_edges_dst",
            "idx_roles_who",
            "idx_notif_recipient_seen",
            "idx_messages_thread",
            "idx_user_names_uid",
        ):
            assert name in idx, name
    finally:
        conn.close()


def test_user_version_is_1(tmp_path):
    conn = _db.open_db(tmp_path / "s.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _db.SCHEMA_VERSION == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Bootstrap                                                                   #
# --------------------------------------------------------------------------- #
def test_import_populates_all_tables(store):
    summary = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    assert summary["tasks"] == 3
    assert summary["comments"] == 2
    assert summary["edges"] == 2          # c1 depends_on c2 + blocks c3
    assert summary["roles"] == 3          # 1 collaborator + 2 subscribers
    assert summary["users"] == 2
    assert summary["user_names"] == 3     # alice(2 aliases) + bob(1)
    assert summary["notifications"] == 2
    assert summary["messages"] == 1

    conn = sqlite3.connect(str(store["db_path"]))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id='c1'").fetchone()
        assert row["grp"] == "core"           # group -> grp remap
        assert row["repo"] == "scitex-cards"
        assert row["priority"] == 3
        assert '"2026-08-01"' in row["deadlines_json"]
        assert "completed_by" in row["log_meta_json"]
        assert row["row_order"] == 0

        edges = {
            (r["edge_type"], r["dst_task_id"])
            for r in conn.execute(
                "SELECT * FROM task_edges WHERE src_task_id='c1'"
            )
        }
        assert edges == {("depends_on", "c2"), ("blocks", "c3")}

        roles = {
            (r["role"], r["who"])
            for r in conn.execute("SELECT * FROM task_roles WHERE task_id='c1'")
        }
        assert roles == {
            ("collaborator", "bob"),
            ("subscriber", "carol"),
            ("subscriber", "bob"),
        }

        comments = conn.execute(
            "SELECT * FROM task_comments WHERE task_id='c1' ORDER BY seq"
        ).fetchall()
        assert [c["seq"] for c in comments] == [0, 1]
        assert comments[1]["kind"] == "unblock"

        names = {
            r["name"]: r["user_id"]
            for r in conn.execute("SELECT * FROM user_names")
        }
        assert names["alice"] == "u_aaaaaaaaaaaa"
        assert names["proj-alice"] == "u_aaaaaaaaaaaa"

        alice = conn.execute(
            "SELECT * FROM users WHERE id='u_aaaaaaaaaaaa'"
        ).fetchone()
        assert alice["a2a_port"] == 7001
        assert "telegram" in alice["notify_json"]

        notif = conn.execute(
            "SELECT * FROM notifications WHERE recipient_id='u_aaaaaaaaaaaa'"
        ).fetchone()
        assert notif["seen"] == 0
        assert notif["event_type"] == "reassigned"
        seen_notif = conn.execute(
            "SELECT * FROM notifications WHERE recipient_id='dave'"
        ).fetchone()
        assert seen_notif["seen"] == 1

        msg = conn.execute("SELECT * FROM messages").fetchone()
        assert msg["thread_key"] == "dm:alice::bob"
        assert msg["sender"] == "alice"
        assert msg["recipient"] == "bob"
        assert msg["read"] == 0
    finally:
        conn.close()


def test_import_is_idempotent(store):
    first = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    second = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    # Same per-table counts.
    for key in ("tasks", "comments", "edges", "roles", "users",
                "user_names", "notifications", "messages"):
        assert first[key] == second[key], key
    # And no row multiplication in the DB.
    conn = sqlite3.connect(str(store["db_path"]))
    try:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM task_comments"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM task_edges"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_verify_reports_ok_after_import(store):
    _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    report = _db.verify(store["db_path"])
    assert report["ok"] is True
    assert report["user_version"] == 1
    assert report["schema_version"] == "1"
    assert report["quick_check"] == "ok"
    assert report["source"] == "yaml-import"
    assert report["tables"]["tasks"] == 3
    assert set(report["tables"]) == set(_db.SCHEMA_TABLES)


def test_verify_absent_db_is_not_ok(tmp_path):
    report = _db.verify(tmp_path / "nope.db")
    assert report["exists"] is False
    assert report["ok"] is False


# --------------------------------------------------------------------------- #
# repo-field round-trip (dataclass + DB column)                              #
# --------------------------------------------------------------------------- #
def test_repo_field_round_trips_dataclass():
    t = _model.Task.from_dict({"id": "r1", "title": "t", "repo": "scitex-cards"})
    assert t.repo == "scitex-cards"
    assert t.to_dict()["repo"] == "scitex-cards"
    # Absent repo stays omitted (default None) so YAML stays compact.
    t2 = _model.Task.from_dict({"id": "r2", "title": "t"})
    assert t2.repo is None
    assert "repo" not in t2.to_dict()


def test_repo_field_round_trips_db_column(store):
    _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    conn = sqlite3.connect(str(store["db_path"]))
    try:
        val = conn.execute(
            "SELECT repo FROM tasks WHERE id='c1'"
        ).fetchone()[0]
        assert val == "scitex-cards"
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# SAFETY: import never modifies the source YAML                              #
# --------------------------------------------------------------------------- #
def test_import_does_not_modify_source_yaml(store):
    tasks_before = store["tasks_path"].read_bytes()
    threads_before = store["threads_path"].read_bytes()
    _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    # Re-run to be extra sure a second pass also leaves YAML untouched.
    _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    assert store["tasks_path"].read_bytes() == tasks_before
    assert store["threads_path"].read_bytes() == threads_before


# --------------------------------------------------------------------------- #
# PERF REGRESSION: the REBUILD must not pay for `OR REPLACE`                  #
# --------------------------------------------------------------------------- #
# `INSERT OR REPLACE INTO tasks` cost 4,592 us/row against 110 us/row for a plain
# INSERT — 42x, and 6.3 s of the rebuild's 7.3 s (live store, 1,370 cards).
# `tasks` is a parent with ON DELETE CASCADE children, so under
# `PRAGMA foreign_keys=ON` a REPLACE runs the full cascade/FK machinery on every
# row — to resolve a conflict `_rebuild_from_doc` has already made IMPOSSIBLE by
# deleting every row first. Asserts the MECHANISM, not a wall-clock number, so it
# cannot flake on a busy box.
def _sql_trace(conn, fn):
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return seen


def test_rebuild_inserts_tasks_without_or_replace(store):
    doc = _model.load_doc(store["tasks_path"], validate=False)
    conn = _db.connect(store["db_path"])
    _db.init_schema(conn)

    def _go():
        conn.execute("BEGIN IMMEDIATE")
        _db_bootstrap._rebuild_from_doc(conn, doc)
        conn.commit()

    seen = _sql_trace(conn, _go)
    conn.close()

    into_tasks = [s for s in seen if "INTO tasks" in s]
    assert into_tasks, "expected the rebuild to insert into `tasks`"
    offenders = [s for s in into_tasks if "OR REPLACE" in s.upper()]
    assert not offenders, (
        "the rebuild must use a plain INSERT — it deletes every row first, so "
        "`INSERT OR REPLACE` only buys per-row FK cascade checks (42x slower). "
        f"Offending SQL: {offenders[0]!r}"
    )


# ...but the DEFAULT must stay REPLACE, because the incremental mirror upserts a
# changed card WITHOUT dropping its `tasks` row first. A plain INSERT there would
# raise `UNIQUE constraint failed: tasks.id` on every card update.
def test_insert_tasks_defaults_to_upsert_over_a_live_row(store):
    conn = _db.connect(store["db_path"])
    _db.init_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v1", "status": "todo"}])
    # Same id again, row still present — this is the incremental mirror's shape.
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v2", "status": "done"}])
    conn.commit()

    rows = conn.execute("SELECT id, title, status FROM tasks WHERE id='c9'").fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [("c9", "v2", "done")]


# --------------------------------------------------------------------------- #
# ...and a plain INSERT must still tolerate a duplicate id, LAST-WINS          #
# --------------------------------------------------------------------------- #
# Last-wins is exactly what `OR REPLACE` used to give us; it is now done in
# Python (`_dedupe_last_wins`) so the SQL can stay plain. A duplicate id is a
# real data bug, so it is also logged LOUD rather than silently absorbed.
def test_duplicate_task_id_keeps_last_occurrence_and_logs(tmp_path, caplog):
    tasks_path = tmp_path / "tasks.yaml"
    _write_yaml(
        tasks_path,
        {
            "tasks": [
                {
                    "id": "dup",
                    "title": "FIRST",
                    "status": "todo",
                    "comments": [{"author": "a", "ts": "t", "text": "old"}],
                },
                {"id": "keep", "title": "Untouched", "status": "done"},
                {
                    "id": "dup",
                    "title": "SECOND",
                    "status": "done",
                    "comments": [{"author": "b", "ts": "t", "text": "new"}],
                },
            ]
        },
    )
    db_path = tmp_path / "todo.db"

    with caplog.at_level("ERROR"):
        summary = _db_bootstrap.import_from_yaml(
            tasks_path=tasks_path, db_path=db_path
        )

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, title, status FROM tasks ORDER BY id").fetchall()
    comments = conn.execute(
        "SELECT task_id, text FROM task_comments ORDER BY task_id"
    ).fetchall()
    conn.close()

    # The duplicate collapses to ONE row — the LAST one — and does not raise.
    assert rows == [("dup", "SECOND", "done"), ("keep", "Untouched", "done")]
    assert summary["tasks"] == 2
    # Only the winner's comments survive (`OR REPLACE` used to append BOTH cards').
    assert comments == [("dup", "new")]
    # And the data bug is surfaced, not swallowed.
    assert "dup" in caplog.text
    assert "DUPLICATE CARD ID" in caplog.text


# EOF
