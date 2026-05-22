#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the task model + loader/validator (no mocks; real tmp files)."""

from __future__ import annotations

import contextlib

import pytest

from scitex_todo import TaskValidationError, load_tasks, save_tasks


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


def test_load_tasks_accepts_integer_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: 3}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["priority"] == 3


def test_load_tasks_raises_on_non_integer_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: high}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_rejects_boolean_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: true}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_save_tasks_round_trip_preserves_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done}\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 7
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    assert reloaded[0]["priority"] == 7


def test_save_tasks_round_trip_preserves_comments(tmp_path):
    # Arrange
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "# top-of-file comment kept verbatim\n"
        "tasks:\n"
        "  - id: a  # inline task comment\n"
        "    title: First\n"
        "    status: done\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, path)
    rewritten = path.read_text(encoding="utf-8")
    # Assert
    assert "# top-of-file comment kept verbatim" in rewritten


def test_save_tasks_preserves_inline_comment(tmp_path):
    # Arrange
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - id: a  # inline task comment\n"
        "    title: First\n"
        "    status: done\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    tasks[0]["priority"] = 2
    # Act
    save_tasks(tasks, path)
    rewritten = path.read_text(encoding="utf-8")
    # Assert
    assert "# inline task comment" in rewritten


def test_save_tasks_raises_on_bad_priority_type(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: a, title: First, status: done}\n")
    tasks = load_tasks(store)
    tasks[0]["priority"] = "high"
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        save_tasks(tasks, store)


def test_save_tasks_does_not_write_when_validation_fails(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: a, title: First, status: done}\n")
    before = store.read_text(encoding="utf-8")
    bad = [{"id": "a", "title": "First", "status": "bogus"}]
    with contextlib.suppress(TaskValidationError):
        save_tasks(bad, store)
    # Act
    after = store.read_text(encoding="utf-8")
    # Assert
    assert after == before


def test_save_tasks_writes_fresh_store_when_absent(tmp_path):
    # Arrange
    target = tmp_path / "nested" / "new.yaml"
    tasks = [{"id": "a", "title": "First", "status": "pending", "priority": 1}]
    # Act
    save_tasks(tasks, target)
    reloaded = load_tasks(target)
    # Assert
    assert reloaded[0]["id"] == "a"


# EOF
