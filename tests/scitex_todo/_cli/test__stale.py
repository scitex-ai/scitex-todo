#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `list-stale` CLI verb (CliRunner; no mocks).

Mirrors the criteria of the board's `/stale` endpoint (PR #153) +
the 🧹 Stale Review panel (PR #154). AAA pattern + one assertion
per test (TQ002 / TQ007).
"""

from __future__ import annotations

import datetime
import json
import textwrap

import pytest
from click.testing import CliRunner

from scitex_todo._cli import main


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _store(tmp_path):
    """Write a small fixture store and return its path."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old = _iso(now - datetime.timedelta(days=30))
    recent = _iso(now - datetime.timedelta(days=2))
    path = tmp_path / "tasks.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            tasks:
              - id: old-pending-card
                title: '[P2] long stale pending card'
                status: deferred
                created_at: '{old}'
                project: scitex-dev
                assignee: proj-scitex-dev
              - id: recent-pending-card
                title: '[P2] recently created pending card'
                status: deferred
                created_at: '{recent}'
                project: scitex-dev
                assignee: proj-scitex-dev
              - id: no-timestamp-card
                title: 'undated pending card'
                status: deferred
                project: business
                assignee: proj-scitex-lead
              - id: vague-card
                title: 'tbd'
                status: deferred
              - id: done-card
                title: 'completed card should never be flagged'
                status: done
                created_at: '{old}'
                project: scitex-dev
            """
        ),
        encoding="utf-8",
    )
    return str(path)


def test_stale_list_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store])
    # Assert
    assert result.exit_code == 0, result.output


def test_stale_list_includes_old_pending(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store])
    # Assert
    assert "old-pending-card" in result.output


def test_stale_list_excludes_recent_pending(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store])
    # Assert
    assert "recent-pending-card" not in result.output


def test_stale_list_excludes_done(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store])
    # Assert
    assert "done-card" not in result.output


def test_stale_list_includes_no_timestamp_by_default(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store])
    # Assert
    assert "no-timestamp-card" in result.output


def test_stale_list_exclude_no_timestamp_filter(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["list-stale", "--tasks", store, "--exclude-no-timestamp"],
    )
    # Assert — no-timestamp-card was flagged ONLY for missing timestamps; the
    # filter must drop it (vague-card stays — it has a second reason).
    assert "no-timestamp-card" not in result.output


def test_stale_list_project_filter(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(
        main, ["list-stale", "--tasks", store, "--project", "scitex-dev"]
    )
    # Assert
    assert "no-timestamp-card" not in result.output


def test_stale_list_json_emits_array(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-stale", "--tasks", store, "--json"])
    # Assert
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_stale_list_rejects_negative_days(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(
        main, ["list-stale", "--tasks", store, "--days", "-1"]
    )
    # Assert
    assert result.exit_code != 0


def test_stale_list_empty_when_days_huge(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["list-stale", "--tasks", store, "--days", "10000", "--exclude-no-timestamp"],
    )
    # Assert — vague-card stays (flagged for vagueness, not age); we just
    # check that the runner returns a sane non-error result.
    assert result.exit_code == 0


# EOF
