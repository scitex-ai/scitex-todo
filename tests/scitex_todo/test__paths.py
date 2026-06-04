#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for task-store path resolution (no mocks; real env + tmp files)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_todo._paths import bundled_example, resolve_tasks_path
from scitex_todo._paths import ENV_TASKS


@pytest.fixture
def clean_tasks_env():
    """Save and restore $SCITEX_TODO_TASKS around a test."""
    saved = os.environ.get(ENV_TASKS)
    os.environ.pop(ENV_TASKS, None)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(ENV_TASKS, None)
        else:
            os.environ[ENV_TASKS] = saved


@pytest.fixture
def isolated_cwd(tmp_path):
    """Run from a fresh cwd with no .git and no reachable user-scope store.

    Real isolation (no mocks): chdir into a fresh tmp dir and point
    $SCITEX_DIR at an empty dir so neither project nor user scope exists.
    Restores both on teardown.
    """
    saved_cwd = Path.cwd()
    saved_scitex_dir = os.environ.get("SCITEX_DIR")
    work = tmp_path / "isolated"
    work.mkdir()
    os.chdir(work)
    os.environ["SCITEX_DIR"] = str(tmp_path / "empty-scitex")
    try:
        yield work
    finally:
        os.chdir(saved_cwd)
        if saved_scitex_dir is None:
            os.environ.pop("SCITEX_DIR", None)
        else:
            os.environ["SCITEX_DIR"] = saved_scitex_dir


def test_explicit_existing_path_wins_resolution(tmp_path, clean_tasks_env):
    # Arrange
    explicit = tmp_path / "mine.yaml"
    explicit.write_text("tasks: []\n", encoding="utf-8")
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_env_var_path_resolves_when_no_explicit(tmp_path, clean_tasks_env):
    # Arrange
    target = tmp_path / "fromenv.yaml"
    target.write_text("tasks: []\n", encoding="utf-8")
    os.environ[ENV_TASKS] = str(target)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert resolved == target


def test_falls_back_to_bundled_example(clean_tasks_env, isolated_cwd):
    # Arrange
    expected = bundled_example()
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert resolved == expected


def test_bundled_example_file_exists_and_loads():
    # Arrange
    example = bundled_example()
    # Act
    text = example.read_text(encoding="utf-8")
    # Assert
    assert "tasks:" in text


# EOF
