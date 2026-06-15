#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for `_pr_merged_backfill.backfill_merged_prs`.

Lead a2a `ba90ba35`, 2026-06-15 — pin the fallback bookkeeping
policy so any drift (e.g. someone adding a CI-color gate that lead
explicitly forbade) fails LOUD.

Coverage:
  - happy path: 1 merged PR not in ledger → card marked done + ledger
                entry written with source='poll'
  - multi-PR sweep: mix of already-in-ledger + newly-processed
  - no-card-match: PR has no matching card → ledger still written,
                   no_card_match counter incremented
  - since-days filter: PR older than cutoff is skipped (and ends the
                       scan because the listing is newest-first)
  - rate-limit: _RateLimited raised → sweep aborts, summary flagged
  - per-PR error: one bad PR doesn't break the sweep
  - race: ledger entry exists before backfill writes → mark_processed
          is a no-op (first-writer-wins)
  - CI-color independence: red/grey/unknown 'overall' is irrelevant;
                            merged=True is the only gate

No mocks of stdlib — only the _GhRunner test seam is stubbed
(STX-NM / PA-306 compatible).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pytest

from scitex_todo import _hooks_processed, _pr_merged_backfill
from scitex_todo._pr_merged_backfill import (
    _GhRunner,
    _RateLimited,
    backfill_merged_prs,
)
from scitex_todo._store import add_task, get_task, update_task


# === fixtures ==============================================================


@pytest.fixture()
def store(tmp_path: Path, monkeypatch) -> Path:
    """Isolated tasks.yaml so ledger + cards live in tmp_path."""
    p = tmp_path / "tasks.yaml"
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(p))
    return p


def _seed_card(store: Path, *, card_id: str, pr_number: int) -> None:
    add_task(store=store, id=card_id, title=f"work for #{pr_number}")
    update_task(
        store=store,
        task_id=card_id,
        pr_url=f"https://github.com/owner/repo/pull/{pr_number}",
    )


def _pr(
    number: int,
    *,
    merged_at: str | None = "2026-06-15T01:23:45Z",
    user_login: str = "merger",
    merge_commit: str | None = "deadbeef",
    html_url: str | None = None,
) -> dict:
    return {
        "number": number,
        "html_url": html_url
        or f"https://github.com/owner/repo/pull/{number}",
        "merged_at": merged_at,
        "merge_commit_sha": merge_commit,
        "user": {"login": user_login},
    }


class _StubRunner(_GhRunner):
    """Test-only runner — returns scripted JSON per-repo."""

    def __init__(self, by_repo: dict[str, Any]):
        self.by_repo = by_repo
        self.calls: list[list[str]] = []

    def json(self, argv: list[str]) -> Any:
        self.calls.append(list(argv))
        # argv = ['api', 'repos/{repo}/pulls?...&--paginate']  or similar
        for repo, payload in self.by_repo.items():
            if any(f"repos/{repo}/" in a for a in argv):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        return []


# === happy path ============================================================


def test_happy_path_one_merged_pr_marks_card_and_writes_ledger(store):
    _seed_card(store, card_id="card-x", pr_number=42)
    runner = _StubRunner({"owner/repo": [_pr(42)]})

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )

    assert summary["scanned"] == 1
    assert summary["newly_processed"] == 1
    assert summary["already_processed"] == 0
    assert summary["cards_marked"] == 1
    assert summary["no_card_match"] == 0
    assert summary["errors"] == 0
    assert summary["rate_limited"] is False

    # Card flipped.
    card = get_task(store=store, task_id="card-x")
    assert card["status"] == "done"
    assert card["pr_url"] == "https://github.com/owner/repo/pull/42"

    # Ledger has source='poll'.
    ledger = _hooks_processed.list_entries(store=store)
    assert "owner/repo#42" in ledger
    assert ledger["owner/repo#42"]["source"] == "poll"
    assert ledger["owner/repo#42"]["matched_cards"] == ["card-x"]
    assert ledger["owner/repo#42"]["merge_commit"] == "deadbeef"
    assert ledger["owner/repo#42"]["author"] == "merger"


# === replay (second sweep is a no-op) ======================================


def test_replay_sweep_counts_only_already_processed(store):
    _seed_card(store, card_id="card-y", pr_number=43)
    runner = _StubRunner({"owner/repo": [_pr(43)]})
    backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )

    # Re-run the sweep with the same scripted PR list.
    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["scanned"] == 1
    assert summary["newly_processed"] == 0
    assert summary["already_processed"] == 1
    assert summary["cards_marked"] == 0


# === no-card-match =========================================================


def test_no_card_match_still_writes_ledger(store):
    # No card with this PR url.
    runner = _StubRunner({"owner/repo": [_pr(999)]})

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["scanned"] == 1
    assert summary["newly_processed"] == 1
    assert summary["no_card_match"] == 1
    assert summary["cards_marked"] == 0

    ledger = _hooks_processed.list_entries(store=store)
    assert "owner/repo#999" in ledger
    assert ledger["owner/repo#999"]["matched_cards"] == []
    assert ledger["owner/repo#999"]["source"] == "poll"


# === since-days filter =====================================================


def test_since_days_filter_skips_old_prs(store):
    _seed_card(store, card_id="card-old", pr_number=10)
    _seed_card(store, card_id="card-new", pr_number=11)
    # Newest-first listing (matches the GH endpoint sort order).
    runner = _StubRunner({
        "owner/repo": [
            _pr(11, merged_at="2026-06-15T00:00:00Z"),
            _pr(10, merged_at="2025-01-01T00:00:00Z"),
        ],
    })

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=1, gh_runner=runner,
    )
    # Only the new PR got processed; the old one breaks the loop.
    assert summary["newly_processed"] == 1
    assert get_task(store=store, task_id="card-new")["status"] == "done"
    assert get_task(store=store, task_id="card-old")["status"] != "done"


# === rate-limit ============================================================


def test_rate_limit_aborts_sweep_and_flags_summary(store):
    runner = _StubRunner({
        "owner/repo": _RateLimited("X-RateLimit-Remaining: 0"),
    })
    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["rate_limited"] is True
    assert summary["newly_processed"] == 0
    assert summary["scanned"] == 0


# === per-PR exception isolated =============================================


def test_per_pr_exception_continues_sweep(store, monkeypatch):
    _seed_card(store, card_id="card-a", pr_number=1)
    _seed_card(store, card_id="card-b", pr_number=2)
    runner = _StubRunner({"owner/repo": [_pr(1), _pr(2)]})

    # Force the FIRST PR processing to raise; second must still run.
    original = _pr_merged_backfill._backfill_one
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic failure")
        return original(**kwargs)

    monkeypatch.setattr(_pr_merged_backfill, "_backfill_one", flaky)

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["errors"] == 1
    assert summary["newly_processed"] == 1
    # The non-failing card is done; the failing one is not.
    assert get_task(store=store, task_id="card-b")["status"] == "done"
    assert get_task(store=store, task_id="card-a")["status"] != "done"


# === race vs hook ==========================================================


def test_race_when_hook_wrote_ledger_first(store):
    _seed_card(store, card_id="card-r", pr_number=77)
    # Pre-write a hook-source ledger entry.
    _hooks_processed.mark_processed(
        "owner/repo",
        77,
        merge_commit="hook-sha",
        matched_cards=["card-r"],
        author="hook-author",
        source="hook",
        store=store,
    )
    # Hook also flipped the card (simulate the real handler).
    from scitex_todo._store import complete_task

    complete_task(store=store, task_id="card-r", by="hook-author")

    # Now the poller arrives.
    runner = _StubRunner({"owner/repo": [_pr(77)]})
    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["already_processed"] == 1
    assert summary["newly_processed"] == 0

    # Ledger entry stayed as hook-source (first-writer-wins).
    ledger = _hooks_processed.list_entries(store=store)
    assert ledger["owner/repo#77"]["source"] == "hook"
    assert ledger["owner/repo#77"]["merge_commit"] == "hook-sha"


# === CI-color independence =================================================


def test_merged_pr_is_marked_done_regardless_of_ci_color(store):
    """Lead-locked policy 2026-06-15: ANY merged PR → DONE.

    The PR listing endpoint does not even carry CI conclusion data;
    we pass through merged_at as the only signal. This test pins
    the contract so a future PR cannot 'helpfully' add a CI gate.
    """
    _seed_card(store, card_id="card-z", pr_number=50)
    # PR with NO CI metadata at all — backfill must still record it.
    runner = _StubRunner({"owner/repo": [_pr(50, merge_commit=None)]})

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["newly_processed"] == 1
    assert get_task(store=store, task_id="card-z")["status"] == "done"


# === closed-but-not-merged ignored =========================================


def test_closed_unmerged_pr_is_skipped(store):
    _seed_card(store, card_id="card-q", pr_number=60)
    # merged_at = None → closed without merge → skip.
    runner = _StubRunner({"owner/repo": [_pr(60, merged_at=None)]})

    summary = backfill_merged_prs(
        repos=["owner/repo"], since_days=30, gh_runner=runner,
    )
    assert summary["scanned"] == 1
    assert summary["newly_processed"] == 0
    assert get_task(store=store, task_id="card-q")["status"] != "done"
