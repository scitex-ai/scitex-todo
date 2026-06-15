#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP endpoint tests for `event:"pr_merged"` flow on /hooks/done.

Lead+dev schema lock 2026-06-14. Pins the contract reached with
proj-scitex-dev (a2a msg `5636941a...` / `721888e3...`) so a future
schema drift fails LOUD in CI rather than silently dropping
bookkeeping.

Coverage:
  - happy path: new payload → card matched + marked done + ledger entry
  - replay: same (repo, pr_number) twice → already_processed
  - no-card-match: pr_url unset on every card → 200 + empty matched_cards
                   + ledger entry with empty matched_cards
  - dry-run: ?dry=1 → no store mutation, no ledger entry, dry_run=true
  - multi-card: one PR closes 2 cards (train PR) → both marked done
  - schema violations: 400 for missing/wrong-type required fields
  - legacy path: kind:"done" + card_ids still works (regression guard)

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_todo._django.handlers.hooks import hook_done_view
from scitex_todo._hooks_processed import list_entries, _ledger_path
from scitex_todo._store import add_task, get_task, update_task


# === fixtures ==============================================================


@pytest.fixture()
def store_with_pr_card(tmp_path: Path, monkeypatch) -> Path:
    """A store with a single card whose pr_url points at PR #209."""
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="card-1", title="extract PR1")
    update_task(
        store=store,
        task_id="card-1",
        pr_url="https://github.com/ywatanabe1989/scitex-todo/pull/209",
    )
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(store))
    return store


@pytest.fixture()
def store_with_two_pr_cards(tmp_path: Path, monkeypatch) -> Path:
    """Two cards pointing at the same PR — covers train-PR multi-close."""
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="card-a", title="part A")
    add_task(store=store, id="card-b", title="part B")
    update_task(
        store=store,
        task_id="card-a",
        pr_url="https://github.com/ywatanabe1989/scitex-todo/pull/209",
    )
    update_task(
        store=store,
        task_id="card-b",
        pr_url="https://github.com/ywatanabe1989/scitex-todo/pull/209",
    )
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(store))
    return store


@pytest.fixture()
def store_no_match(tmp_path: Path, monkeypatch) -> Path:
    """A store with a card whose pr_url points at a DIFFERENT PR."""
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="card-other", title="unrelated")
    update_task(
        store=store,
        task_id="card-other",
        pr_url="https://github.com/ywatanabe1989/scitex-todo/pull/999",
    )
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(store))
    return store


def _pr_merged_payload(
    *, repo="ywatanabe1989/scitex-todo", pr_number=209, **overrides
) -> dict:
    base = {
        "event": "pr_merged",
        "repo": repo,
        "pr_number": pr_number,
        "merged_at": "2026-06-15T01:23:45Z",
        "merge_commit": "abc1234deadbeef",
        "title": "refactor(board_v3): PR1 of extract train",
        "author": "ywatanabe1989",
        "labels": ["refactor"],
        "base_ref": "develop",
        "head_ref": "feat/extract",
    }
    base.update(overrides)
    return base


def _post(rf: RequestFactory, payload: dict, *, query: str = "") -> object:
    return rf.post(
        f"/hooks/done{query}",
        data=json.dumps(payload),
        content_type="application/json",
    )


# === happy path ============================================================


def test_pr_merged_marks_card_done_and_records_ledger(store_with_pr_card):
    rf = RequestFactory()
    response = hook_done_view(_post(rf, _pr_merged_payload()))
    body = json.loads(response.content)

    assert response.status_code == 200
    assert body["kind"] == "done"
    assert body["matched_cards"] == ["card-1"]
    assert body["ledger_key"] == "ywatanabe1989/scitex-todo#209"
    assert body["merge_commit"] == "abc1234deadbeef"
    assert body["first_processed_at"]
    assert body["note"] is None

    # Store mutation
    card = get_task(store=store_with_pr_card, task_id="card-1")
    assert card["status"] == "done"
    assert card["pr_url"] == "https://github.com/ywatanabe1989/scitex-todo/pull/209"

    # Ledger mutation
    entries = list_entries(store=store_with_pr_card)
    assert "ywatanabe1989/scitex-todo#209" in entries
    assert entries["ywatanabe1989/scitex-todo#209"]["matched_cards"] == ["card-1"]


def test_pr_merged_multi_card_train_pr_marks_all_done(store_with_two_pr_cards):
    rf = RequestFactory()
    response = hook_done_view(_post(rf, _pr_merged_payload()))
    body = json.loads(response.content)

    assert response.status_code == 200
    assert set(body["matched_cards"]) == {"card-a", "card-b"}
    for cid in ("card-a", "card-b"):
        assert get_task(store=store_with_two_pr_cards, task_id=cid)["status"] == "done"


# === idempotent replay =====================================================


def test_pr_merged_replay_returns_already_processed(store_with_pr_card):
    rf = RequestFactory()
    payload = _pr_merged_payload()
    first = hook_done_view(_post(rf, payload))
    assert first.status_code == 200

    # Manually un-do the card to prove the second POST does NOT re-mutate.
    update_task(store=store_with_pr_card, task_id="card-1", status="pending")
    assert get_task(store=store_with_pr_card, task_id="card-1")["status"] == "pending"

    second = hook_done_view(_post(rf, payload))
    body = json.loads(second.content)
    assert second.status_code == 200
    assert body["already_processed"] is True
    assert body["ledger_key"] == "ywatanabe1989/scitex-todo#209"

    # Card stayed pending — second POST was a true no-op at the store layer.
    assert get_task(store=store_with_pr_card, task_id="card-1")["status"] == "pending"


# === no-card-match =========================================================


def test_pr_merged_no_card_match_is_200_with_empty_matched_cards(store_no_match):
    rf = RequestFactory()
    response = hook_done_view(_post(rf, _pr_merged_payload()))
    body = json.loads(response.content)

    assert response.status_code == 200
    assert body["matched_cards"] == []
    assert body["ledger_key"] == "ywatanabe1989/scitex-todo#209"
    assert "no card matched" in body["note"]

    # Ledger still records the merge for audit.
    entries = list_entries(store=store_no_match)
    assert "ywatanabe1989/scitex-todo#209" in entries
    assert entries["ywatanabe1989/scitex-todo#209"]["matched_cards"] == []

    # And the unrelated card was NOT touched.
    other = get_task(store=store_no_match, task_id="card-other")
    assert other["status"] != "done"


# === dry-run ===============================================================


def test_pr_merged_dry_run_skips_mutation(store_with_pr_card):
    rf = RequestFactory()
    response = hook_done_view(_post(rf, _pr_merged_payload(), query="?dry=1"))
    body = json.loads(response.content)

    assert response.status_code == 200
    assert body["dry_run"] is True
    assert body["would_mutate"] == ["card-1"]
    assert body["matched_cards"] == ["card-1"]

    # Card NOT mutated.
    card = get_task(store=store_with_pr_card, task_id="card-1")
    assert card["status"] != "done"

    # Ledger NOT mutated.
    ledger = _ledger_path(store=store_with_pr_card)
    assert not ledger.exists()


# === schema violations -> 400 ==============================================


@pytest.mark.parametrize(
    "field,bad_value,fragment",
    [
        ("repo", "", "'repo' must"),
        ("repo", "notvalidformat", "'repo' must match"),
        ("pr_number", "209", "must be an int"),
        ("pr_number", -1, "must be > 0"),
        ("pr_number", True, "must be an int"),
        ("merged_at", "", "must be a non-empty"),
        ("merged_at", "not-an-iso-date", "not a parseable ISO-8601"),
        ("labels", "single-string", "'labels' must be a list"),
    ],
)
def test_pr_merged_schema_violation_returns_400(
    store_with_pr_card, field, bad_value, fragment
):
    rf = RequestFactory()
    payload = _pr_merged_payload(**{field: bad_value})
    response = hook_done_view(_post(rf, payload))
    body = json.loads(response.content)

    assert response.status_code == 400
    assert body["error"] == "invalid-event"
    assert fragment in body["detail"], (fragment, body["detail"])


def test_pr_merged_unknown_event_kind_returns_400(store_with_pr_card):
    rf = RequestFactory()
    payload = _pr_merged_payload(event="pr_closed_without_merge")
    response = hook_done_view(_post(rf, payload))
    body = json.loads(response.content)

    assert response.status_code == 400
    assert body["error"] == "invalid-event"
    assert "unknown event" in body["detail"]


# === legacy regression =====================================================


def test_legacy_kind_done_payload_still_works(store_with_pr_card):
    """The PR #187 wire shape (kind:'done' + card_ids) must keep working."""
    rf = RequestFactory()
    legacy_payload = {
        "kind": "done",
        "repo": "ywatanabe1989/scitex-todo",
        "pr_number": 999,
        "pr_url": "https://github.com/ywatanabe1989/scitex-todo/pull/999",
        "author": "tester",
        "merged_at": "2026-06-15T01:23:45Z",
        "card_ids": ["card-1"],
    }
    response = hook_done_view(_post(rf, legacy_payload))
    body = json.loads(response.content)

    assert response.status_code == 200
    # Legacy path returns the dispatcher summary directly — no
    # 'matched_cards' / 'ledger_key' decoration.
    assert body["kind"] == "done"
    assert any(
        cw.get("card_id") == "card-1" and cw.get("action") == "completed"
        for cw in body.get("card_writes", [])
    )

    # And the legacy path does NOT touch the new dedup ledger.
    ledger = _ledger_path(store=store_with_pr_card)
    assert not ledger.exists()
