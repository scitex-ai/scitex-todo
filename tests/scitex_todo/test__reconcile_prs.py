#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the PR-merge card-freshness automation (`_reconcile_prs`).

No mocks (STX-NM / PA-306): the decision core is a pure function over
``(task, merge_state)``; the URL parser is pure; the orchestration uses a
real temp ``tasks.yaml`` and injects a FAKE merge-state callable via the
documented ``merge_state_fn=`` seam (NOT a network mock). AAA pattern.
"""

from __future__ import annotations

import textwrap

import pytest

from scitex_todo._model import load_tasks
from scitex_todo._reconcile_prs import (
    ACTION_CLOSE,
    ACTION_SKIP_DONE,
    ACTION_SKIP_NO_PR,
    ACTION_SKIP_NOT_MERGED,
    ACTION_SKIP_NOT_OPEN,
    ACTION_SKIP_UNKNOWN,
    MERGED,
    OPEN,
    UNKNOWN,
    PrRef,
    decide_reconcile_action,
    parse_pr_url,
    reconcile_merged_prs,
)


# === parse_pr_url (pure) ==================================================


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/foo/bar/pull/42", PrRef("foo", "bar", 42)),
        ("http://github.com/o/r/pull/7/files", PrRef("o", "r", 7)),
        ("https://github.com/a-b/c.d/pull/123", PrRef("a-b", "c.d", 123)),
        ("git@github.com:o/r/pull/9", PrRef("o", "r", 9)),
    ],
)
def test_parse_pr_url_extracts_owner_repo_number(url, expected):
    # Arrange / Act
    ref = parse_pr_url(url)
    # Assert
    assert ref == expected


@pytest.mark.parametrize(
    "url",
    ["", None, "https://github.com/foo/bar/issues/1", "not a url", "github.com/foo"],
)
def test_parse_pr_url_returns_none_for_unparseable(url):
    # Arrange / Act / Assert
    assert parse_pr_url(url) is None


# === decide_reconcile_action (pure) ======================================


def test_decide_close_when_open_card_with_merged_pr():
    # Arrange
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_CLOSE


def test_decide_skip_done_for_already_done_card():
    # Arrange
    task = {"status": "done", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_DONE


def test_decide_skip_no_pr_when_pr_url_missing():
    # Arrange
    task = {"status": "in_progress"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NO_PR


def test_decide_skip_no_pr_when_pr_url_unparseable():
    # Arrange
    task = {"status": "blocked", "pr_url": "not-a-pr-url"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NO_PR


def test_decide_skip_not_open_for_deferred_card():
    # Arrange
    task = {"status": "deferred", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NOT_OPEN


def test_decide_skip_not_merged_when_pr_open():
    # Arrange
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, OPEN)
    # Assert
    assert action == ACTION_SKIP_NOT_MERGED


def test_decide_skip_unknown_is_fail_soft():
    # Arrange — merge-state could not be determined; MUST NOT close.
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, UNKNOWN)
    # Assert
    assert action == ACTION_SKIP_UNKNOWN


# === reconcile_merged_prs (real temp store + injected seam) ===============


def _store(tmp_path):
    """Write a small fixture store and return its path."""
    path = tmp_path / "tasks.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            tasks:
              - id: merged-open-card
                title: work that merged
                status: in_progress
                pr_url: https://github.com/o/r/pull/1
              - id: open-pr-card
                title: still in review
                status: blocked
                pr_url: https://github.com/o/r/pull/2
              - id: already-done-card
                title: done long ago
                status: done
                pr_url: https://github.com/o/r/pull/3
              - id: no-pr-card
                title: no linked pr
                status: in_progress
              - id: unknown-pr-card
                title: state unknowable
                status: pending
                pr_url: https://github.com/o/r/pull/5
            """
        )
    )
    return path


def _fake_seam(mapping):
    """Return a merge_state_fn that looks merge-state up by pr_url."""

    def _fn(pr_url):
        return mapping.get(pr_url, UNKNOWN)

    return _fn


def test_dry_run_reports_candidates_but_does_not_mutate(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam(
        {
            "https://github.com/o/r/pull/1": MERGED,
            "https://github.com/o/r/pull/2": OPEN,
            "https://github.com/o/r/pull/5": UNKNOWN,
        }
    )
    # Act
    result = reconcile_merged_prs(path, apply=False, merge_state_fn=seam)
    # Assert — the merged-open card is a candidate, nothing closed.
    ids = [c["id"] for c in result.would_close]
    assert ids == ["merged-open-card"]
    assert result.closed == []
    # And the store is untouched.
    statuses = {t["id"]: t["status"] for t in load_tasks(path)}
    assert statuses["merged-open-card"] == "in_progress"


def test_apply_closes_merged_card_and_comments(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam(
        {
            "https://github.com/o/r/pull/1": MERGED,
            "https://github.com/o/r/pull/2": OPEN,
            "https://github.com/o/r/pull/5": UNKNOWN,
        }
    )
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert — only the merged card flipped to done.
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["merged-open-card"]["status"] == "done"


def test_apply_leaves_open_pr_card_untouched(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/2": OPEN})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["open-pr-card"]["status"] == "blocked"


def test_apply_appends_auto_close_comment(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    tasks = {t["id"]: t for t in load_tasks(path)}
    comments = tasks["merged-open-card"].get("comments") or []
    assert any("auto-closed" in (c.get("text") or "") for c in comments)


def test_already_done_card_is_skipped(tmp_path):
    # Arrange — even if its PR reads merged, a done card is never re-touched.
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/3": MERGED})
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    assert result.skipped.get(ACTION_SKIP_DONE, 0) >= 1


def test_missing_pr_url_card_is_skipped(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({})
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    assert result.skipped.get(ACTION_SKIP_NO_PR, 0) >= 1


def test_unknown_merge_state_never_closes(tmp_path):
    # Arrange — fail-soft: an unknown state must leave the card open.
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/5": UNKNOWN})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["unknown-pr-card"]["status"] == "pending"


def test_seam_exception_is_treated_as_unknown(tmp_path):
    # Arrange — a raising seam must NOT close (fail-soft over the whole call).
    path = _store(tmp_path)

    def _boom(pr_url):
        raise RuntimeError("network down")

    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=_boom)
    # Assert
    assert result.closed == []


def test_idempotent_second_run_closes_nothing_new(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Act — second pass: the card is now done.
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    assert result.closed == []


# EOF
