#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scitex_todo._next (P3d, lead-approved 2026-06-12).

The single canonical "what to pick up next" predicate shared by every
fleet agent. Tests cover the filter rules + the deterministic sort.
No mocks (STX-NM / PA-306); inputs are plain dicts.
"""

from __future__ import annotations

from scitex_todo._next import NextPick, next_task


class TestEmptyBacklog:
    def test_returns_none_when_no_tasks(self):
        # Arrange
        # Act
        out = next_task([], assignee="proj-x")
        # Assert
        assert out == NextPick(task=None, candidate_count=0)


class TestRunnableFilter:
    def test_skips_blocked_tasks(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "blocked",
                "blocker": "operator-decision",
                "agent": "proj-x",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task is None

    def test_skips_done_tasks(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "done",
                "agent": "proj-x",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task is None

    def test_accepts_deferred_runnable_task(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "agent": "proj-x",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_accepts_in_progress(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "in_progress",
                "agent": "proj-x",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_skips_other_assignees(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "agent": "proj-y",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task is None

    def test_accepts_legacy_assignee_field(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "assignee": "proj-x",
            }
        ]
        # Assert
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_no_assignee_arg_accepts_any(self):
        # Arrange
        # Act
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "agent": "proj-x",
            }
        ]
        # When `assignee` is None, the predicate accepts any agent — the
        # lead-side cron uses this mode to see the global runnable queue.
        # Assert
        assert next_task(tasks).task["id"] == "a"


class TestProjectFilter:
    def test_scopes_to_project(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "agent": "proj-x",
                "project": "alpha",
            },
            {
                "id": "b",
                "title": "B",
                "status": "deferred",
                "agent": "proj-x",
                "project": "beta",
            },
        ]
        # Act
        out = next_task(tasks, assignee="proj-x", project="beta")
        # Assert
        assert out.task["id"] == "b"


class TestPrioritySort:
    def test_lower_priority_picked_first(self):
        # Arrange
        tasks = [
            {
                "id": "low",
                "title": "L",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 5,
            },
            {
                "id": "high",
                "title": "H",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 1,
            },
        ]
        # Act
        out = next_task(tasks, assignee="proj-x")
        # Assert
        assert out.task["id"] == "high"

    def test_unrated_ranks_last(self):
        # Arrange
        tasks = [
            {
                "id": "rated",
                "title": "R",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 99,
            },
            {"id": "unrated", "title": "U", "status": "deferred", "agent": "proj-x"},
        ]
        # Act
        out = next_task(tasks, assignee="proj-x")
        # Assert
        assert out.task["id"] == "rated"


class TestActivitySort:
    def test_newer_activity_wins_at_equal_priority(self):
        # Arrange
        tasks = [
            {
                "id": "old",
                "title": "O",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 1,
                "last_activity": "2026-01-01",
            },
            {
                "id": "new",
                "title": "N",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 1,
                "last_activity": "2026-06-12",
            },
        ]
        # Act
        out = next_task(tasks, assignee="proj-x")
        # Assert
        assert out.task["id"] == "new"


class TestIdTiebreak:
    def test_deterministic_id_asc(self):
        # Arrange
        tasks = [
            {
                "id": "b",
                "title": "B",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 1,
            },
            {
                "id": "a",
                "title": "A",
                "status": "deferred",
                "agent": "proj-x",
                "priority": 1,
            },
        ]
        # Act
        out = next_task(tasks, assignee="proj-x")
        # Assert
        assert out.task["id"] == "a"


class TestCandidateCount:
    def test_candidate_count_matches_filter_size(self):
        # Arrange
        tasks = [
            {"id": "a", "title": "A", "status": "deferred", "agent": "proj-x"},
            {"id": "b", "title": "B", "status": "deferred", "agent": "proj-x"},
            {
                "id": "c",
                "title": "C",
                "status": "blocked",
                "blocker": "dep",
                "agent": "proj-x",
            },
        ]
        # Act
        out = next_task(tasks, assignee="proj-x")
        # Assert
        assert out.candidate_count == 2


# EOF
