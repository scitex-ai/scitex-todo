#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the P4 deadline + scheduled validator (no mocks; real tmp files).

Lead-approved 2026-06-12. Nullable ISO-8601 fields on Task; validator
rejects empty strings, unparseable dates, and ``deadline < scheduled``.
"""

from __future__ import annotations

import pytest

from scitex_cards import TaskValidationError
from scitex_cards._model import load_tasks


def _write(tmp_path, text):
    path = tmp_path / "tasks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestDeadlineAbsent:
    def test_no_deadline_loads_clean(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n  - {id: a, title: A, status: pending}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0].get("deadline") is None


class TestDeadlineBareDate:
    def test_accepts_yyyy_mm_dd(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, deadline: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == "2026-06-15"


class TestDeadlineDateTime:
    def test_accepts_iso_datetime_with_offset(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, "
            "deadline: '2026-06-15T18:00:00+09:00'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == "2026-06-15T18:00:00+09:00"


class TestScheduledFieldRoundTrip:
    def test_scheduled_alone_loads(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, scheduled: '2026-06-10'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["scheduled"] == "2026-06-10"


class TestDeadlineEmptyStringRejected:
    def test_raises_on_empty_deadline_string(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n" "  - {id: a, title: A, status: pending, deadline: ''}\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="invalid deadline"):
            load_tasks(store)


class TestDeadlineNonStringRejected:
    def test_raises_on_int(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n" "  - {id: a, title: A, status: pending, deadline: 20260615}\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="invalid deadline"):
            load_tasks(store)


class TestDeadlineUnparseableRejected:
    def test_raises_on_garbage(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, deadline: 'next Tuesday'}\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="unparseable deadline"):
            load_tasks(store)


class TestScheduledUnparseableRejected:
    def test_raises_on_garbage(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n" "  - {id: a, title: A, status: pending, scheduled: 'soon'}\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="unparseable scheduled"):
            load_tasks(store)


class TestDeadlineBeforeScheduledRejected:
    def test_raises_when_deadline_precedes_scheduled(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-10', scheduled: '2026-06-15'}\n",
        )
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="before scheduled"):
            load_tasks(store)

    def test_equal_dates_accepted(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-15', scheduled: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == tasks[0]["scheduled"]


class TestDeadlineAfterScheduledAccepted:
    def test_deadline_after_scheduled_is_valid(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-20', scheduled: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] > tasks[0]["scheduled"]


# EOF
