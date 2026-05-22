#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the task model + loader/validator (no mocks; real tmp files)."""

from __future__ import annotations

import pytest

from scitex_todo import TaskValidationError, load_tasks


def _write(tmp_path, text):
    """Write a tasks.yaml under tmp_path and return its path."""
    path = tmp_path / "tasks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_tasks_returns_validated_list_in_order(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: First, status: done}\n"
        "  - {id: b, title: Second, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert [t["id"] for t in tasks] == ["a", "b"]


def test_load_tasks_accepts_goal_status(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: north, title: Big Goal, status: goal}\n")
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "goal"


def test_load_tasks_raises_on_duplicate_id(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: dup, title: One, status: done}\n"
        "  - {id: dup, title: Two, status: done}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_bad_status(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: x, title: X, status: wibble}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_title(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: notitle, status: pending}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_id(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {title: No Id, status: pending}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_when_tasks_not_a_list(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks: not-a-list\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_file(tmp_path):
    # Arrange
    missing = tmp_path / "nope.yaml"
    # Act
    ctx = pytest.raises(FileNotFoundError)
    # Assert
    with ctx:
        load_tasks(missing)


# EOF
