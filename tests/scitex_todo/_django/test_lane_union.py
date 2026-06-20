#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the per-project lane UNION on the board's load path
(operator-validated requirement, lead a2a `1ceec0ef` + `40c0a42d`).

Real ``tmp_path`` fixtures, env override of the lane-glob, no mocks
(STX-NM / PA-306). Covers:

  - lanes discovered via the env glob
  - union: global tasks AND every lane appear in the board
  - collision policy: project-lane wins, override is logged
  - malformed lane: SKIPPED (logged at WARN), board still renders
    the rest (per-lane crash-loud, not whole-view)
  - mtime = MAX across global + all lanes
  - no lanes configured → behaves identically to the pre-PR loader
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_todo._django import services as _svc  # noqa: E402


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def isolated_env(env, tmp_path):
    """Point ENV_LANE_GLOBS at a tmp dir's lane layout + clear caches."""
    proj_root = tmp_path / "proj"
    proj_root.mkdir()
    env.set(
        _svc.ENV_LANE_GLOBS,
        f"{proj_root}/*/.scitex/todo/tasks.yaml",
    )
    _svc._reset_cache()
    yield proj_root
    _svc._reset_cache()


class TestUnion:
    """Global + per-project lanes appear together in the unioned board."""

    def test_lane_card_appears_on_the_board(self, tmp_path, isolated_env):
        # Arrange — global has one card; per-project lane has another.
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: g, title: G, status: pending}\n")
        lane = isolated_env / "alpha" / ".scitex" / "todo" / "tasks.yaml"
        _write(lane, "tasks:\n  - {id: a, title: A, status: pending}\n")
        # Act
        board = _svc.get_board(str(global_store))
        # Assert — both ids on the board.
        ids = {t["id"] for t in board.tasks}
        assert ids == {"g", "a"}

    def test_lane_paths_field_lists_consumed_lanes(self, tmp_path, isolated_env):
        # Arrange
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks: []\n")
        lane = isolated_env / "alpha" / ".scitex" / "todo" / "tasks.yaml"
        _write(lane, "tasks:\n  - {id: a, title: A, status: pending}\n")
        # Act
        board = _svc.get_board(str(global_store))
        # Assert
        assert lane in board.lane_paths


class TestCollisionPolicy:
    """Project-lane wins on duplicate id; the override is logged."""

    def test_project_lane_overrides_global_on_collision(self, tmp_path, isolated_env):
        # Arrange — both have an `id: x` row, with DIFFERENT titles so
        # we can assert which side won.
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: x, title: GLOBAL, status: pending}\n")
        lane = isolated_env / "alpha" / ".scitex" / "todo" / "tasks.yaml"
        _write(lane, "tasks:\n  - {id: x, title: LANE, status: pending}\n")
        # Act
        board = _svc.get_board(str(global_store))
        # Assert
        winner = next(t for t in board.tasks if t["id"] == "x")
        assert winner["title"] == "LANE"

    def test_collision_is_logged(self, tmp_path, isolated_env, caplog):
        # Arrange
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: x, title: GLOBAL, status: pending}\n")
        lane = isolated_env / "alpha" / ".scitex" / "todo" / "tasks.yaml"
        _write(lane, "tasks:\n  - {id: x, title: LANE, status: pending}\n")
        # Act
        with caplog.at_level("WARNING", logger="scitex_todo._django.services"):
            _svc.get_board(str(global_store))
        # Assert — a collision log entry naming the id is emitted.
        assert any(
            "collision" in r.message and "'x'" in r.message for r in caplog.records
        )


class TestMalformedLane:
    """A single bad YAML must NOT crash the whole board."""

    def test_malformed_lane_is_skipped(self, tmp_path, isolated_env, caplog):
        # Arrange — good global + good lane + ONE bad lane.
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: g, title: G, status: pending}\n")
        good = isolated_env / "ok" / ".scitex" / "todo" / "tasks.yaml"
        _write(good, "tasks:\n  - {id: a, title: A, status: pending}\n")
        bad = isolated_env / "broken" / ".scitex" / "todo" / "tasks.yaml"
        _write(bad, "tasks: [\n  not yaml at all\n")  # malformed
        # Act
        with caplog.at_level("WARNING", logger="scitex_todo._django.services"):
            board = _svc.get_board(str(global_store))
        # Assert — the good ids surface; the bad lane was skipped.
        ids = {t["id"] for t in board.tasks}
        assert ids == {"g", "a"}

    def test_malformed_lane_is_logged(self, tmp_path, isolated_env, caplog):
        # Arrange
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks: []\n")
        bad = isolated_env / "broken" / ".scitex" / "todo" / "tasks.yaml"
        _write(bad, "tasks: [\n  not yaml at all\n")
        # Act
        with caplog.at_level("WARNING", logger="scitex_todo._django.services"):
            _svc.get_board(str(global_store))
        # Assert
        assert any("skipping malformed" in r.message for r in caplog.records)


class TestMtime:
    """Effective mtime = MAX(global, *lanes) so any source change
    invalidates the cache."""

    def test_mtime_reflects_newest_lane(self, tmp_path, isolated_env):
        # Arrange — global is older than the lane.
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks: []\n")
        old_mtime = time.time() - 86400
        os.utime(global_store, (old_mtime, old_mtime))
        lane = isolated_env / "alpha" / ".scitex" / "todo" / "tasks.yaml"
        _write(lane, "tasks:\n  - {id: a, title: A, status: pending}\n")
        # Act
        board = _svc.get_board(str(global_store))
        # Assert — board.mtime tracks the lane (newer), not the global.
        assert board.mtime == lane.stat().st_mtime


class TestNoLanesConfigured:
    """When no lanes are discovered, behavior matches the pre-PR loader."""

    def test_empty_glob_yields_only_the_global_store_tasks(self, tmp_path, env):
        # Arrange — empty glob so nothing is discovered.
        env.set(_svc.ENV_LANE_GLOBS, "")
        _svc._reset_cache()
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: g, title: G, status: pending}\n")
        try:
            # Act
            board = _svc.get_board(str(global_store))
            # Assert
            assert {t["id"] for t in board.tasks} == {"g"}
        finally:
            _svc._reset_cache()

    def test_empty_glob_yields_no_lane_paths(self, tmp_path, env):
        # Arrange — empty glob so nothing is discovered.
        env.set(_svc.ENV_LANE_GLOBS, "")
        _svc._reset_cache()
        global_store = tmp_path / "global.yaml"
        _write(global_store, "tasks:\n  - {id: g, title: G, status: pending}\n")
        try:
            # Act
            board = _svc.get_board(str(global_store))
            # Assert
            assert board.lane_paths == []
        finally:
            _svc._reset_cache()
