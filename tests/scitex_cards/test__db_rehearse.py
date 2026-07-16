#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`db rehearse` — the RFC-R4 equivalence gate as one repeatable command.

Frozen-copy semantics are the point: the rehearsal must be READ-ONLY on the
live store, judge the frozen copy (immune to mid-run writes), pass on a store
that round-trips (unknown keys, drained inboxes, threads included), and keep
its workdir as evidence exactly when it fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._db_rehearse import rehearse
from scitex_cards._yaml import safe_dump


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    doc = {
        "tasks": [
            {"id": "a", "title": "a", "status": "done", "extra_unknown": [1]},
            {"id": "b", "title": "b", "status": "deferred"},
        ],
        "users": [{"id": "u_000000000001", "kind": "agent", "names": ["a"]}],
        "inboxes": {
            "u_000000000001": [
                {"id": "n_1", "event_type": "x", "ts": "2026", "seen": False}
            ],
            "drained": [],
        },
    }
    threads = {"dm:a::b": [{"id": "m_1", "from": "a", "to": "b", "body": "hi", "ts": "2026", "read": False}]}
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump(doc), encoding="utf-8")
    (tmp_path / "threads.yaml").write_text(
        safe_dump({"threads": threads}), encoding="utf-8"
    )
    return path


def test_rehearse_passes_on_a_round_trippable_store(store, tmp_path):
    # Act
    report = rehearse(tasks_path=store, workdir=tmp_path / "wd")
    # Assert
    assert report["equal"] is True
    assert report["sections"] == {
        "tasks": True,
        "users": True,
        "inboxes": True,
        "threads": True,
    }
    assert report["tasks"] == 2
    assert report["inbox_recipients"] == 2  # the drained one included


def test_rehearse_never_touches_the_live_store(store, tmp_path):
    # Arrange
    before = store.read_bytes()
    # Act
    rehearse(tasks_path=store, workdir=tmp_path / "wd")
    # Assert — byte-identical afterwards.
    assert store.read_bytes() == before


def test_rehearse_passing_run_cleans_its_workdir_unless_kept(store, tmp_path):
    # Act
    cleaned = rehearse(tasks_path=store, workdir=tmp_path / "wd1")
    kept = rehearse(tasks_path=store, workdir=tmp_path / "wd2", keep=True)
    # Assert
    assert cleaned["workdir"] is None and not (tmp_path / "wd1").exists()
    assert kept["workdir"] == str(tmp_path / "wd2") and (tmp_path / "wd2").exists()
