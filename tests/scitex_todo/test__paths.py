#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for task-store path resolution (no mocks; real env + tmp files)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_todo._paths import bundled_example, resolve_tasks_path
from scitex_todo._paths import ENV_TASKS, ENV_TASKS_DEPRECATED, _find_git_root


@pytest.fixture
def clean_tasks_env():
    """Save and restore the task-store env vars around a test.

    Clears BOTH the current ``$SCITEX_TODO_TASKS_YAML_SHARED`` and the
    deprecated ``$SCITEX_TODO_TASKS`` so a stale export in the ambient
    environment can't leak in and trip the fail-loud guard mid-test.
    """
    saved = {v: os.environ.get(v) for v in (ENV_TASKS, ENV_TASKS_DEPRECATED)}
    for v in (ENV_TASKS, ENV_TASKS_DEPRECATED):
        os.environ.pop(v, None)
    try:
        yield
    finally:
        for v, val in saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


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


def test_deprecated_env_var_fails_loud(monkeypatch, clean_tasks_env):
    """The renamed-away $SCITEX_TODO_TASKS must never be silently honoured: if
    it is still set, resolution fails LOUD pointing at the new name so a stale
    export can't quietly pin the wrong store."""
    # Arrange
    monkeypatch.setenv(ENV_TASKS_DEPRECATED, "/some/legacy/tasks.yaml")
    # Act / Assert
    with pytest.raises(RuntimeError, match=ENV_TASKS):
        resolve_tasks_path(None)


def test_bundled_example_file_exists_and_loads():
    # Arrange
    example = bundled_example()
    # Act
    text = example.read_text(encoding="utf-8")
    # Assert
    assert "tasks:" in text


# === Additional gap-fill coverage (proj-scitex-todo overnight) ==============


def test_explicit_missing_path_returned_as_is(tmp_path, clean_tasks_env):
    """Explicit-but-missing path: function returns the Path verbatim so the
    LOADER raises the FNF with the user-supplied filename in the message —
    the docstring contract."""
    # Arrange
    explicit = tmp_path / "nope.yaml"  # never created
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_explicit_path_string_is_expanded(tmp_path, clean_tasks_env):
    """Explicit path accepts a str, not just Path."""
    # Arrange
    target = tmp_path / "mine.yaml"
    target.write_text("tasks: []\n", encoding="utf-8")
    # Act
    resolved = resolve_tasks_path(str(target))
    # Assert
    assert resolved == target


def test_user_scope_wins_over_project_store(tmp_path, clean_tasks_env, env):
    """DATA store = USER-CANONICAL. Regression guard for the 2026-07-06 stale-
    store incident: even when cwd is inside a repo that HAS a
    ``<git-root>/.scitex/todo/tasks.yaml``, resolution must reach the canonical
    USER store — never the per-repo copy. The data store has DELIBERATELY no
    project-scope layer (only the CONFIG in _config.py keeps its project
    override)."""
    # Arrange — a fake project root with a real .git + a would-be shadow store.
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    proj_store_dir = project / ".scitex" / "todo"
    proj_store_dir.mkdir(parents=True)
    proj_store = proj_store_dir / "tasks.yaml"
    proj_store.write_text("tasks: []\n", encoding="utf-8")
    # The canonical user-scope store the resolver MUST prefer.
    user_root = tmp_path / "user-scitex"
    user_dir = user_root / "todo"
    user_dir.mkdir(parents=True)
    user_store = user_dir / "tasks.yaml"
    user_store.write_text("tasks: []\n", encoding="utf-8")
    env.set("SCITEX_DIR", str(user_root))
    env.chdir(project)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert — user store wins; the in-repo shadow is ignored.
    assert resolved == user_store
    assert resolved != proj_store


def test_user_scope_used_when_no_project_store(tmp_path, clean_tasks_env, env):
    """Resolution precedence 4 — user scope when no project scope."""
    # Arrange — no .git ancestor at cwd; SCITEX_DIR has the store.
    work = tmp_path / "work"
    work.mkdir()
    user_root = tmp_path / "user-scitex"
    user_dir = user_root / "todo"
    user_dir.mkdir(parents=True)
    user_store = user_dir / "tasks.yaml"
    user_store.write_text("tasks: []\n", encoding="utf-8")
    env.set("SCITEX_DIR", str(user_root))
    env.chdir(work)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert resolved == user_store


def test_find_git_root_walks_up(tmp_path):
    """`_find_git_root` ascends parents from a deep subdir to the repo root.

    The helper is retained (the CONFIG layer in _config.py still uses it for
    the reminders config's project override) even though the DATA store no
    longer consults it. Test it directly, not via resolve_tasks_path."""
    # Arrange — repo at tmp_path/repo, subdir at tmp_path/repo/a/b/c.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    # Act
    found = _find_git_root(deep)
    # Assert — ascends to the repo root; a dir with no .git ancestor yields None.
    assert found == repo.resolve()
    assert _find_git_root(tmp_path.parent) != repo.resolve()


# EOF
