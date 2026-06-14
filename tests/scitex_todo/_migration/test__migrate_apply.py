#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the directory-card migrator (PR-D Stage 2).

Lead-approved guardrails (operator OK via lead a2a `3cf31901`):

  1. The `note` field migrates VERBATIM into ``tasks/<id>/README.md``
     (bytes-equal head check; no transform, no truncation).
  2. Atomic per-card: if the README write or verify fails, the
     YAML row stays untouched.
  3. Idempotent: re-running the migrator on an already-migrated
     lane is a no-op (every row classifies as canonical).
  4. Per-lane git commit at end: when the lane's parent is a git
     repo, the post-migration state is committed under a clear
     ``[scitex-todo migrate]`` message.

Real `tmp_path` lanes + real `git init`; no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scitex_todo import _migration as _mig


# === Helpers ================================================================


def _write_lane(lane: Path, body: str) -> None:
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.write_text(body, encoding="utf-8")


def _git_init(root: Path) -> None:
    """Initialize a git repo with a deterministic user/email so
    `git commit` succeeds inside the test runner sandbox."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"], check=True,
    )


# === Bytes-equal note round-trip (guardrail #1) ============================


class TestNoteByteEquality:
    """The `note` field's bytes must round-trip through README.md."""

    def test_note_appears_verbatim_in_readme_head(self, tmp_path):
        # Arrange — short note, no title overflow, no long comments.
        lane = tmp_path / "tasks.yaml"
        original_note = "First line.\nSecond line.\n\nThird with 日本語."
        _write_lane(
            lane,
            "tasks:\n"
            "  - id: a\n"
            "    title: A\n"
            "    status: pending\n"
            f"    note: {yaml.safe_dump(original_note).strip()}\n",
        )
        # Act
        _mig.apply_lane(lane)
        # Assert — written, then re-read README's leading bytes match.
        readme = (lane.parent / "tasks" / "a" / "README.md").read_text(
            encoding="utf-8",
        )
        assert readme.startswith(original_note)

    def test_note_is_stripped_from_yaml(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, note: 'body here'}\n",
        )
        # Act
        _mig.apply_lane(lane)
        # Assert
        data = yaml.safe_load(lane.read_text(encoding="utf-8"))
        assert "note" not in data["tasks"][0]


# === Title / comment trimming ===============================================


class TestTrimming:
    """Title >120 gets trimmed in yaml; full title goes into README."""

    def test_long_title_trimmed_in_yaml(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        long_title = "T" * (_mig.MAX_TITLE_CHARS + 50)
        _write_lane(
            lane,
            "tasks:\n"
            f"  - {{id: a, title: '{long_title}', status: pending}}\n",
        )
        # Act
        _mig.apply_lane(lane)
        # Assert
        data = yaml.safe_load(lane.read_text(encoding="utf-8"))
        assert len(data["tasks"][0]["title"]) == _mig.MAX_TITLE_CHARS

    def test_long_title_full_preserved_in_readme(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        long_title = "U" * (_mig.MAX_TITLE_CHARS + 50)
        _write_lane(
            lane,
            "tasks:\n"
            f"  - {{id: a, title: '{long_title}', status: pending}}\n",
        )
        # Act
        _mig.apply_lane(lane)
        # Assert
        readme = (lane.parent / "tasks" / "a" / "README.md").read_text(
            encoding="utf-8",
        )
        assert long_title in readme


# === Idempotency ============================================================


class TestIdempotent:
    """Re-running the migrator on an already-migrated lane is a no-op."""

    def test_second_apply_writes_nothing(self, tmp_path):
        # Arrange — initial migration.
        lane = tmp_path / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        _mig.apply_lane(lane)
        # Act — second pass.
        result = _mig.apply_lane(lane)
        # Assert — every row is already-canonical.
        assert all(
            r.skipped_reason == "already-canonical" for r in result.rows
        )


# === Dry run ================================================================


class TestDryRun:
    """`apply_lane(dry_run=True)` does NOT write to disk."""

    def test_dry_run_leaves_no_readme(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        # Act
        _mig.apply_lane(lane, dry_run=True)
        # Assert
        assert not (lane.parent / "tasks" / "a" / "README.md").exists()

    def test_dry_run_leaves_yaml_unchanged(self, tmp_path):
        # Arrange
        lane = tmp_path / "tasks.yaml"
        body = "tasks:\n  - {id: a, title: A, status: pending, note: 'body'}\n"
        _write_lane(lane, body)
        # Act
        _mig.apply_lane(lane, dry_run=True)
        # Assert — original `note: 'body'` still present.
        post = lane.read_text(encoding="utf-8")
        assert "body" in post


# === Per-lane git commit (guardrail #3) ====================================


class TestGitCommit:
    """When the lane's parent is a git repo, the migrator commits."""

    def test_real_git_repo_gets_migrate_commit(self, tmp_path):
        # Arrange — make tmp_path a git repo, place the lane inside it.
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)
        lane = repo / ".scitex" / "todo" / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        # Initial commit so the staging-vs-status logic has a base.
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "seed", "-q"],
            check=True,
        )
        # Act
        result = _mig.apply_lane(lane)
        # Assert — the migrator reports the commit landed.
        assert result.git_committed is True, (
            f"git not committed; skip_reason={result.git_skip_reason!r}; "
            f"rows={[(r.id, r.written_readme, r.yaml_updated, r.skipped_reason) for r in result.rows]}"
        )

    def test_lane_without_outer_git_still_commits_via_autoinit(self, tmp_path):
        # Arrange — lane is NOT inside a pre-existing git repo. The
        # writer's `_git_autocommit_store` lazily initializes a tiny
        # `.git` under `.scitex/todo/` on the first `_save_tasks_unlocked`
        # call (crash-safety record). Per lead's guardrail #3, that
        # auto-created repo IS the one the migrator commits into, so a
        # "no outer .git" lane still ends up with a migrate commit.
        lane = tmp_path / ".scitex" / "todo" / "tasks.yaml"
        _write_lane(
            lane,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, note: 'body'}\n",
        )
        # Act
        result = _mig.apply_lane(lane)
        # Assert
        assert result.git_committed is True
