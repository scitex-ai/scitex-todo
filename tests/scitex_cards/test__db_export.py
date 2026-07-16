#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB → YAML export is exact by construction (ADR-0010 backup rail).

The invariant under test: yaml → `db import` → `db export` → yaml reproduces
every record SEMANTICALLY IDENTICALLY — including keys the column mapping has
never heard of, and each record's own key order — because the export reads the
verbatim ``card_json`` / ``record_json`` payloads, never the typed columns.
A DB whose payloads are missing is REFUSED, not exported stripped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._db import connect, init_schema, resolve_db_path
from scitex_cards._db_bootstrap import import_from_yaml
from scitex_cards._db_export import ExportRefused, export_doc, export_yaml
from scitex_cards._yaml import safe_dump, safe_load


@pytest.fixture()
def seeded(tmp_path: Path) -> dict:
    """A yaml store + threads sidecar with UNKNOWN keys on every section."""
    doc = {
        "tasks": [
            {
                "id": "t-one",
                "title": "first",
                "status": "deferred",
                "agent": "a",
                "exotic_unknown_field": {"nested": [1, 2]},
            },
            {"id": "t-two", "title": "second", "status": "done", "agent": "b"},
        ],
        "users": [
            {
                "id": "u_000000000001",
                "kind": "agent",
                "names": ["a"],
                "unknown_user_key": "survives",
            }
        ],
        "inboxes": {
            "u_000000000001": [
                {
                    "id": "n_000000000001",
                    "event_type": "commented",
                    "ts": "2026-07-16T00:00:00Z",
                    "seen": False,
                    "unknown_notif_key": 42,
                }
            ]
        },
    }
    threads = {
        "dm:a::b": [
            {
                "id": "m_000000000001",
                "from": "a",
                "to": "b",
                "body": "hello",
                "ts": "2026-07-16T00:00:00Z",
                "read": False,
                "unknown_msg_key": "survives",
            }
        ]
    }
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(safe_dump(doc), encoding="utf-8")
    # The sidecar contract is a top-level `threads:` mapping.
    (tmp_path / "threads.yaml").write_text(
        safe_dump({"threads": threads}), encoding="utf-8"
    )
    db = tmp_path / "cards.db"
    import_from_yaml(tasks_path=tasks_yaml, db_path=db)
    return {"doc": doc, "threads": threads, "db": db, "tmp": tmp_path}


def test_export_reproduces_doc_including_unknown_keys(seeded):
    # Act
    doc, threads = export_doc(seeded["db"])
    # Assert — semantic identity, unknown keys included.
    assert doc == seeded["doc"]
    assert threads == seeded["threads"]


def test_export_preserves_each_records_own_key_order(seeded):
    # Act
    doc, _ = export_doc(seeded["db"])
    # Assert — the first card's key order is the order the yaml carried.
    assert list(doc["tasks"][0].keys()) == [
        "id",
        "title",
        "status",
        "agent",
        "exotic_unknown_field",
    ]


def test_export_yaml_round_trips_through_files(seeded):
    # Act
    report = export_yaml(
        db_path=seeded["db"],
        out=seeded["tmp"] / "export" / "tasks.yaml",
        threads_out=seeded["tmp"] / "export" / "threads.yaml",
    )
    # Assert — files reload to the original structures; counts are honest.
    assert safe_load((seeded["tmp"] / "export" / "tasks.yaml").read_text()) == seeded["doc"]
    assert safe_load((seeded["tmp"] / "export" / "threads.yaml").read_text()) == {
        "threads": seeded["threads"]
    }
    assert report["tasks"] == 2
    assert report["messages"] == 1


def test_export_refuses_rows_without_verbatim_payload(seeded):
    # Arrange — simulate a pre-v3 row: payload stripped after import.
    conn = connect(seeded["db"])
    conn.execute("UPDATE users SET record_json = NULL")
    conn.commit()
    conn.close()
    # Act / Assert — refusal, not a stripped export.
    with pytest.raises(ExportRefused):
        export_doc(seeded["db"])


def test_export_overlays_live_mutable_flags_over_payload(seeded):
    # Arrange — the seen flag mutates in the DB after import (poll ack).
    conn = connect(seeded["db"])
    conn.execute("UPDATE notifications SET seen = 1")
    conn.commit()
    conn.close()
    # Act
    doc, _ = export_doc(seeded["db"])
    # Assert — the export reflects live state, not the import-time snapshot.
    (record,) = doc["inboxes"]["u_000000000001"]
    assert record["seen"] is True
    assert record["unknown_notif_key"] == 42


def test_resolve_db_path_still_delegates(monkeypatch, tmp_path):
    """Guard: the exporter's default path rides the S4a resolution chain."""
    monkeypatch.setenv("SCITEX_CARDS_DB", str(tmp_path / "x.db"))
    assert resolve_db_path() == tmp_path / "x.db"


def test_export_preserves_drained_empty_inboxes(tmp_path):
    """A recipient whose inbox list is EMPTY survives the round-trip (v4).

    Regression: live-store rehearsal 2026-07-16 — 2 of 56 recipients had
    drained ([]) inboxes; zero notification rows carried no key, so the
    export silently dropped those recipients.
    """
    # Arrange — one populated inbox, one drained.
    doc = {
        "tasks": [{"id": "t", "title": "t", "status": "done"}],
        "inboxes": {
            "busy": [{"id": "n_1", "event_type": "x", "ts": "2026", "seen": True}],
            "drained": [],
        },
    }
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(safe_dump(doc), encoding="utf-8")
    db = tmp_path / "cards.db"
    import_from_yaml(tasks_path=tasks_yaml, db_path=db)
    # Act
    out, _threads = export_doc(db)
    # Assert — the drained key is present, as an empty list.
    assert out["inboxes"]["drained"] == []
    assert set(out["inboxes"]) == {"busy", "drained"}
