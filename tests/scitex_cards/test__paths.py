#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for store-path resolution (no mocks; real env + tmp files).

The store IDENTITY is the SQLite database ``$SCITEX_CARDS_DB``
(:func:`scitex_cards._db.resolve_db_path`). :func:`resolve_tasks_path` returns
the YAML CONTAINER beside that database (``<db_dir>/tasks.yaml``) that still
holds the non-task sections (users/groups/inboxes) — NOT the identity, and no
separate YAML-named store variable, no project-scope layer, no bundled fallback.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._db import (
    DEFAULT_DB_FILENAME,
    ENV_DB,
    ENV_DB_DEPRECATED,
    resolve_db_path,
)
from scitex_cards._paths import (
    PKG_SHORT,
    _find_git_root,
    _user_root,
    bundled_example,
    resolve_tasks_path,
)


@pytest.fixture
def clean_store_env():
    """Save and restore the store-identity env vars around a test."""
    saved = {v: os.environ.get(v) for v in (ENV_DB, ENV_DB_DEPRECATED)}
    for v in (ENV_DB, ENV_DB_DEPRECATED):
        os.environ.pop(v, None)
    try:
        yield
    finally:
        for v, val in saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


def test_explicit_existing_path_wins_resolution(tmp_path, clean_store_env):
    # Arrange
    explicit = tmp_path / "mine.db"
    explicit.write_text("", encoding="utf-8")
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_ambient_container_is_beside_the_database(tmp_path, clean_store_env):
    """The non-task YAML container sits next to the resolved database."""
    # Arrange
    target = tmp_path / "fromenv.db"
    os.environ[ENV_DB] = str(target)
    # Act
    resolved = resolve_tasks_path(None)
    # Assert — the container is `<db_dir>/tasks.yaml`, next to the identity DB.
    assert resolved == target.parent / "tasks.yaml"
    assert resolve_db_path(None) == target


def test_container_tracks_the_current_db_var_over_deprecated(tmp_path, clean_store_env):
    """``$SCITEX_CARDS_DB`` (the identity) wins over the pre-rename ``$SCITEX_TODO_DB``."""
    # Arrange
    current = tmp_path / "current.db"
    os.environ[ENV_DB] = str(current)
    os.environ[ENV_DB_DEPRECATED] = str(tmp_path / "legacy.db")
    # Act
    resolved = resolve_tasks_path(None)
    # Assert — the container tracks the winning database's directory.
    assert resolved == current.parent / "tasks.yaml"
    assert resolve_db_path(None) == current


def test_unresolvable_store_does_NOT_fall_back_to_a_packaged_fixture(clean_store_env):
    """There is no last resort — no packaged demo file can become the board."""
    # Act
    resolved = resolve_tasks_path(None)
    # Assert
    assert "examples" not in resolved.parts, (
        "resolution must never land on a packaged fixture — that is how demo "
        "data becomes production data"
    )


def test_canonical_default_identity_names_the_sqlite_database(clean_store_env):
    """The canonical store IDENTITY default names the SQLite database."""
    # Act / Assert — the identity is the DB; the container is its yaml sibling.
    assert resolve_db_path(None).name == DEFAULT_DB_FILENAME == "cards.db"
    assert resolve_tasks_path(None) == resolve_db_path(None).parent / "tasks.yaml"


def test_explicit_missing_path_returned_as_is(tmp_path, clean_store_env):
    """Explicit-but-missing path: returned verbatim so the LOADER raises with
    the user-supplied filename in the message."""
    # Arrange
    explicit = tmp_path / "nope.db"  # never created
    # Act
    resolved = resolve_tasks_path(explicit)
    # Assert
    assert resolved == explicit


def test_explicit_path_string_is_expanded(tmp_path, clean_store_env):
    """Explicit path accepts a str, not just Path."""
    # Arrange
    target = tmp_path / "mine.db"
    # Act
    resolved = resolve_tasks_path(str(target))
    # Assert
    assert resolved == target


def test_bundled_example_raises_because_no_store_ships_in_the_wheel():
    """Calling it is a stated error, not an AttributeError."""
    # Act / Assert
    with pytest.raises(RuntimeError, match="no bundled example"):
        bundled_example()


def test_pkg_short_names_the_cards_directory():
    """PKG_SHORT is "cards" — the ONE place the store directory is named."""
    assert PKG_SHORT == "cards"


def test_user_root_honours_scitex_dir(tmp_path, env):
    """The user-scope root is ``$SCITEX_DIR/cards`` when ``$SCITEX_DIR`` is set."""
    # Arrange
    env.set("SCITEX_DIR", str(tmp_path / "scratch-scitex"))
    # Act
    root = _user_root()
    # Assert
    assert root == tmp_path / "scratch-scitex" / PKG_SHORT


# --- _find_git_root: retained for the reminders CONFIG project override ------
@pytest.fixture()
def repo_with_a_deep_subdir(tmp_path):
    """A repo at tmp_path/repo with a subdir at tmp_path/repo/a/b/c."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    return {"repo": repo, "deep": deep, "outside": tmp_path.parent}


def test_find_git_root_walks_up(repo_with_a_deep_subdir):
    # Arrange
    scenario = repo_with_a_deep_subdir
    # Act
    found = _find_git_root(scenario["deep"])
    # Assert — ascends from the deep subdir to the repo root.
    assert found == scenario["repo"].resolve()


def test_find_git_root_does_not_claim_a_repo_from_outside(repo_with_a_deep_subdir):
    # Arrange
    scenario = repo_with_a_deep_subdir
    # Act
    found = _find_git_root(scenario["outside"])
    # Assert — a dir with no .git ancestor never yields this repo.
    assert found != scenario["repo"].resolve()


# EOF
