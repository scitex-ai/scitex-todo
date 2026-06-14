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
        out = next_task([], assignee="proj-x")
        assert out == NextPick(task=None, candidate_count=0)


class TestRunnableFilter:
    def test_skips_blocked_tasks(self):
        tasks = [{
            "id": "a", "title": "A", "status": "blocked",
            "blocker": "operator-decision", "agent": "proj-x",
        }]
        assert next_task(tasks, assignee="proj-x").task is None

    def test_skips_done_tasks(self):
        tasks = [{
            "id": "a", "title": "A", "status": "done", "agent": "proj-x",
        }]
        assert next_task(tasks, assignee="proj-x").task is None

    def test_accepts_pending(self):
        tasks = [{
            "id": "a", "title": "A", "status": "pending", "agent": "proj-x",
        }]
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_accepts_in_progress(self):
        tasks = [{
            "id": "a", "title": "A", "status": "in_progress", "agent": "proj-x",
        }]
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_skips_other_assignees(self):
        tasks = [{
            "id": "a", "title": "A", "status": "pending", "agent": "proj-y",
        }]
        assert next_task(tasks, assignee="proj-x").task is None

    def test_accepts_legacy_assignee_field(self):
        tasks = [{
            "id": "a", "title": "A", "status": "pending", "assignee": "proj-x",
        }]
        assert next_task(tasks, assignee="proj-x").task["id"] == "a"

    def test_no_assignee_arg_accepts_any(self):
        tasks = [{
            "id": "a", "title": "A", "status": "pending", "agent": "proj-x",
        }]
        # When `assignee` is None, the predicate accepts any agent — the
        # lead-side cron uses this mode to see the global runnable queue.
        assert next_task(tasks).task["id"] == "a"


class TestProjectFilter:
    def test_scopes_to_project(self):
        tasks = [
            {"id": "a", "title": "A", "status": "pending",
             "agent": "proj-x", "project": "alpha"},
            {"id": "b", "title": "B", "status": "pending",
             "agent": "proj-x", "project": "beta"},
        ]
        out = next_task(tasks, assignee="proj-x", project="beta")
        assert out.task["id"] == "b"


class TestPrioritySort:
    def test_lower_priority_picked_first(self):
        tasks = [
            {"id": "low", "title": "L", "status": "pending",
             "agent": "proj-x", "priority": 5},
            {"id": "high", "title": "H", "status": "pending",
             "agent": "proj-x", "priority": 1},
        ]
        out = next_task(tasks, assignee="proj-x")
        assert out.task["id"] == "high"

    def test_unrated_ranks_last(self):
        tasks = [
            {"id": "rated", "title": "R", "status": "pending",
             "agent": "proj-x", "priority": 99},
            {"id": "unrated", "title": "U", "status": "pending",
             "agent": "proj-x"},
        ]
        out = next_task(tasks, assignee="proj-x")
        assert out.task["id"] == "rated"


class TestActivitySort:
    def test_newer_activity_wins_at_equal_priority(self):
        tasks = [
            {"id": "old", "title": "O", "status": "pending",
             "agent": "proj-x", "priority": 1, "last_activity": "2026-01-01"},
            {"id": "new", "title": "N", "status": "pending",
             "agent": "proj-x", "priority": 1, "last_activity": "2026-06-12"},
        ]
        out = next_task(tasks, assignee="proj-x")
        assert out.task["id"] == "new"


class TestIdTiebreak:
    def test_deterministic_id_asc(self):
        tasks = [
            {"id": "b", "title": "B", "status": "pending",
             "agent": "proj-x", "priority": 1},
            {"id": "a", "title": "A", "status": "pending",
             "agent": "proj-x", "priority": 1},
        ]
        out = next_task(tasks, assignee="proj-x")
        assert out.task["id"] == "a"


class TestCandidateCount:
    def test_candidate_count_matches_filter_size(self):
        tasks = [
            {"id": "a", "title": "A", "status": "pending", "agent": "proj-x"},
            {"id": "b", "title": "B", "status": "pending", "agent": "proj-x"},
            {"id": "c", "title": "C", "status": "blocked",
             "blocker": "dep", "agent": "proj-x"},
        ]
        out = next_task(tasks, assignee="proj-x")
        assert out.candidate_count == 2


# EOF
