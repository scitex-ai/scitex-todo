#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_todo._model.is_overdue``.

todo-p6-overdue-ui — backend half (the fleet liveness payload now
exposes a per-agent `overdue_count`, and the CLI can use this helper
for a `--overdue` filter). No mocks — real dicts + frozen ``now``
arg per STX-NM / PA-306.
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo._model import is_overdue


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


class TestIsOverdueAbsentDeadline:
    """A task with no deadline is NEVER overdue."""

    def test_no_deadline_returns_false(self):
        # Arrange
        task = {"id": "a", "title": "A", "status": "pending"}
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverduePastDeadline:
    """A task with a deadline before today is overdue."""

    def test_yesterday_yields_overdue(self):
        # Arrange — deadline yesterday, now today.
        task = {
            "id": "a", "title": "A", "status": "pending",
            "deadline": "2026-06-11",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_today_is_not_yet_overdue(self):
        # Arrange — deadline TODAY (boundary; "overdue" is strict <).
        task = {
            "id": "a", "title": "A", "status": "pending",
            "deadline": "2026-06-12",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False

    def test_tomorrow_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a", "title": "A", "status": "pending",
            "deadline": "2026-06-13",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueTerminalStatuses:
    """A task in a terminal lifecycle (done/deferred/failed/goal) is NOT
    overdue regardless of its deadline. Closed = no longer actionable."""

    def test_done_with_past_deadline_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a", "title": "A", "status": "done",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False

    def test_deferred_with_past_deadline_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a", "title": "A", "status": "deferred",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False

    def test_failed_with_past_deadline_is_not_overdue(self):
        # Arrange
        task = {
            "id": "a", "title": "A", "status": "failed",
            "deadline": "2026-06-01",
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False


class TestIsOverdueMultipleDeadlines:
    """Per :func:`next_deadline_for_task`, multiple deadlines pick the
    soonest. is_overdue follows that — if the soonest is past, overdue."""

    def test_soonest_in_past_yields_overdue(self):
        # Arrange — two deadlines, the earlier is in the past.
        task = {
            "id": "a", "title": "A", "status": "pending",
            "deadlines": ["2026-06-10", "2026-06-20"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is True

    def test_all_future_deadlines_not_overdue(self):
        # Arrange
        task = {
            "id": "a", "title": "A", "status": "pending",
            "deadlines": ["2026-06-13", "2026-06-20"],
        }
        # Act
        result = is_overdue(task, now=_utc(2026, 6, 12, 12, 0, 0))
        # Assert
        assert result is False
