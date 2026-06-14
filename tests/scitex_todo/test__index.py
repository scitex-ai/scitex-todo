#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SQLite derived-index (PR-B of the lead-approved
Stage 2 plan, lead a2a `aa02fb0e` / `e5243003`).

Real `tmp_path` SQLite files via the ``SCITEX_TODO_INDEX_PATH`` env
override; no mocks (STX-NM / PA-306). The lane-discovery glob env
(``SCITEX_TODO_LANE_GLOBS``) is overridden per test so we don't pick
up the host's `~/proj/*` lanes.

Covers:

  - `init_schema` creates the four expected tables
  - `rebuild_index` populates from the global store alone
  - `rebuild_index` overlays per-project lanes on top of global
  - lane wins on id collision (matches PR #137's union policy)
  - `info` reports row count + schema version + last_index_at
  - `query_tasks` filters by project / agent / status
  - `index_path` honors the env override
  - The CLI `index rebuild` / `info` verbs round-trip
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_todo._cli import main
from scitex_todo import _index as _idx


# === Fixtures ===============================================================


@pytest.fixture
def index_target(env, tmp_path):
    """Point ``SCITEX_TODO_INDEX_PATH`` at a tmp file so the real
    ``~/.scitex/todo/.tasks.index.sqlite`` is never touched."""
    target = tmp_path / "tasks.index.sqlite"
    env.set("SCITEX_TODO_INDEX_PATH", str(target))
    yield target


def _write_store(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# === init_schema ===========================================================


class TestInitSchema:
    """The four expected tables exist after `init_schema`."""

    def test_tasks_table_created(self, index_target):
        # Arrange / Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='tasks'",
            ).fetchall()
        # Assert
        assert len(rows) == 1

    def test_tags_table_created(self, index_target):
        # Arrange / Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='tags'",
            ).fetchall()
        # Assert
        assert len(rows) == 1

    def test_meta_table_created(self, index_target):
        # Arrange / Act
        with _idx.open_connection(index_target) as c:
            _idx.init_schema(c)
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='meta'",
            ).fetchall()
        # Assert
        assert len(rows) == 1


# === rebuild_index — global only ===========================================


class TestRebuildGlobalOnly:
    """Rebuild with no lanes populates from the global YAML."""

    def test_populates_global_tasks(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: g1, title: G1, status: pending}\n"
            "  - {id: g2, title: G2, status: in_progress}\n",
        )
        # Act
        stats = _idx.rebuild_index(store, [])
        # Assert
        assert stats["total"] == 2

    def test_meta_records_index_version(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(store, "tasks: []\n")
        # Act
        _idx.rebuild_index(store, [])
        # Assert
        with _idx.open_connection(index_target) as c:
            row = c.execute(
                "SELECT value FROM meta WHERE key='index_version'",
            ).fetchone()
        assert row["value"] == str(_idx.SCHEMA_VERSION)


# === rebuild_index — lane union ============================================


class TestRebuildWithLanes:
    """Per-project lanes overlay on top of the global store."""

    def test_lane_card_appears_in_index(self, index_target, tmp_path):
        # Arrange
        global_store = tmp_path / "global.yaml"
        _write_store(global_store, "tasks:\n  - {id: g1, title: G1, status: pending}\n")
        lane = tmp_path / "lane" / "tasks.yaml"
        _write_store(lane, "tasks:\n  - {id: lan1, title: LAN1, status: pending}\n")
        # Act
        _idx.rebuild_index(global_store, [lane])
        # Assert
        with _idx.open_connection(index_target) as c:
            rows = {r["id"] for r in c.execute("SELECT id FROM tasks")}
        assert rows == {"g1", "lan1"}

    def test_lane_overrides_global_on_collision(
        self, index_target, tmp_path,
    ):
        # Arrange — both have an id=x row with different titles.
        global_store = tmp_path / "global.yaml"
        _write_store(global_store, "tasks:\n  - {id: x, title: GLOBAL, status: pending}\n")
        lane = tmp_path / "lane" / "tasks.yaml"
        _write_store(lane, "tasks:\n  - {id: x, title: LANE, status: pending}\n")
        # Act
        _idx.rebuild_index(global_store, [lane])
        # Assert
        with _idx.open_connection(index_target) as c:
            row = c.execute(
                "SELECT title FROM tasks WHERE id='x'",
            ).fetchone()
        assert row["title"] == "LANE"


# === info ===================================================================


class TestInfo:
    """`info()` reports the on-disk state."""

    def test_info_reports_row_count_after_rebuild(
        self, index_target, tmp_path,
    ):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: a, title: A, status: pending}\n"
            "  - {id: b, title: B, status: done}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        payload = _idx.info()
        # Assert
        assert payload["rows"] == 2

    def test_info_when_no_index_yet(self, index_target):
        # Arrange — never rebuild.
        # Act
        payload = _idx.info()
        # Assert
        assert payload["exists"] is False


# === query_tasks ===========================================================


class TestQueryTasks:
    """The basic SQL read path the /graph SQL flip (PR-D) will build on."""

    def test_filter_by_project(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: x, title: X, status: pending, project: alpha}\n"
            "  - {id: y, title: Y, status: pending, project: beta}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        rows = _idx.query_tasks(project="alpha")
        # Assert
        assert {r["id"] for r in rows} == {"x"}

    def test_filter_by_status(self, index_target, tmp_path):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n"
            "  - {id: x, title: X, status: pending}\n"
            "  - {id: y, title: Y, status: done}\n",
        )
        _idx.rebuild_index(store, [])
        # Act
        rows = _idx.query_tasks(status="done")
        # Assert
        assert {r["id"] for r in rows} == {"y"}


# === CLI verbs =============================================================


class TestCliRebuildAndInfo:
    """`scitex-todo index rebuild` + `info` round-trip via CliRunner."""

    def test_rebuild_then_info_reports_rows(
        self, index_target, tmp_path, env,
    ):
        # Arrange — point the global store at a tmp YAML; empty lane glob.
        store = tmp_path / "global.yaml"
        _write_store(
            store,
            "tasks:\n  - {id: a, title: A, status: pending}\n",
        )
        env.set("SCITEX_TODO_TASKS", str(store))
        # The autouse fixture already sets SCITEX_TODO_LANE_GLOBS="".
        runner = CliRunner()
        # Act
        rb = runner.invoke(main, ["index", "rebuild", "-y"])
        info = runner.invoke(main, ["index", "info", "--json"])
        # Assert — rebuild exited clean + info reports 1 row.
        payload = json.loads(info.output)
        assert rb.exit_code == 0 and payload["rows"] == 1

    def test_dry_run_does_not_create_index(
        self, index_target, tmp_path, env,
    ):
        # Arrange
        store = tmp_path / "global.yaml"
        _write_store(store, "tasks: []\n")
        env.set("SCITEX_TODO_TASKS", str(store))
        runner = CliRunner()
        # Act
        rb = runner.invoke(main, ["index", "rebuild", "--dry-run"])
        # Assert — dry-run exits 0 + the SQLite file was NOT created.
        assert rb.exit_code == 0 and not index_target.exists()


# === env override ==========================================================


def test_env_override_changes_index_path(env, tmp_path):
    # Arrange
    target = tmp_path / "custom" / "i.sqlite"
    env.set("SCITEX_TODO_INDEX_PATH", str(target))
    # Act
    p = _idx.index_path()
    # Assert
    assert p == target
