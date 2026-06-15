#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fallback DONE backfill — bookkeeping recovery for merged PRs the hook missed.

Lead a2a ``c4274787`` + ``ba90ba35``, 2026-06-15 (task #9 / PR2). The
``/hooks/done`` receiver (PR1) records merges in the
``~/.scitex/todo/.processed_done_events.json`` ledger when dev's
GitHub Action POSTs to it. **But that emitter only fires from the
GitHub Action runs of the ywatanabe1989 account**, which has been
throttled — so today the bookkeeping rate is effectively 0%.

This module closes the loop **independently** from the dev side. It
runs on the non-throttled scitex-ai host, walks closed-and-merged
PRs across every configured repo, and reconciles:

* PR in ``_hooks_processed.py`` ledger → noop.
* PR not in ledger → look up cards via ``_pr_lookup.find_cards_by_pr``,
  call ``_handle_done`` (same code path as the hook), then write the
  ledger entry with ``source="poll"``.

The ledger's first-writer-wins semantics mean if the dev hook ever
beats the poller for a particular PR, the poller's
``mark_processed(..., source="poll")`` is a no-op. **Both lanes are
safe to run concurrently.**

## Policy

Lead-locked (a2a ``ba90ba35``, 2026-06-15):

  fallback DONE = ANY merged PR
                  (CI green / red is the pill surface, not bookkeeping gate)

So this module does NOT check CI conclusion. ``pull_request.merged ==
true`` is enough. The CI verdict surface lands in PR1 of #9 (per-PR
``ci_status`` sweep, separate concern).

## Rate-limit policy

Lead-locked (a2a ``ba90ba35``):

  GH rate-limit hit → backoff + resume, hammerしない

On a 403 / ``X-RateLimit-Remaining: 0`` we abort the rest of the
sweep cleanly (the still-unprocessed PRs are picked up by the next
cron tick). Per-PR errors don't take down the whole sweep — the
existing ci-watch isolation pattern is mirrored.

## Scope bound

``--since-days`` (default 7) bounds the closed-PR enumeration so
huge repos don't churn the historical tail every tick. Operators can
extend the window for a one-shot import via the CLI flag.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Iterable

from . import _hooks, _hooks_processed, _pr_lookup

logger = logging.getLogger(__name__)


__all__ = ["backfill_merged_prs", "BackfillSummary"]


class BackfillSummary(dict):
    """Bag-of-counters returned by :func:`backfill_merged_prs`.

    Subclasses ``dict`` for trivial JSON serialisation (the CLI prints
    it; tests inspect it). Keys:

    * ``scanned``            — total PRs examined across all repos.
    * ``already_processed``  — ledger hits (no-op for that PR).
    * ``newly_processed``    — PRs recorded by THIS sweep.
    * ``cards_marked``       — cards transitioned to ``done`` by THIS
                               sweep (≤ matched_cards because some may
                               have already been done at the per-card
                               state layer).
    * ``no_card_match``      — newly-processed PRs that matched zero
                               cards (still ledger-recorded for audit).
    * ``errors``             — per-PR exceptions caught.
    * ``rate_limited``       — True if the sweep aborted early due to a
                               GH rate-limit signal.
    """


# === public entry point ====================================================


def backfill_merged_prs(
    *,
    repos: Iterable[str],
    since_days: int = 7,
    store: str | Path | None = None,
    gh_runner: "_GhRunner | None" = None,
) -> BackfillSummary:
    """Reconcile the ``_hooks_processed.py`` ledger against the GH state.

    Parameters
    ----------
    repos
        Iterable of ``owner/name`` slugs to scan.
    since_days
        Lookback window for closed-PR enumeration.
    store
        Override the tasks-store path; the ledger lives next to it.
    gh_runner
        Test seam — defaults to a subprocess-backed ``gh api`` runner.
        Tests inject a stub so they don't hit the network.
    """
    summary = BackfillSummary(
        scanned=0,
        already_processed=0,
        newly_processed=0,
        cards_marked=0,
        no_card_match=0,
        errors=0,
        rate_limited=False,
    )
    cutoff = _utc_now() - _dt.timedelta(days=max(int(since_days), 1))
    runner = gh_runner or _default_gh_runner()

    for repo in repos:
        if not isinstance(repo, str) or "/" not in repo:
            logger.warning("backfill: skipping invalid repo slug %r", repo)
            continue
        try:
            prs = _list_merged_prs(repo, runner)
        except _RateLimited:
            summary["rate_limited"] = True
            logger.warning(
                "backfill: rate-limited while listing %s; aborting sweep",
                repo,
            )
            return summary
        except _GhApiError as exc:
            logger.warning("backfill: %s list-prs failed: %s", repo, exc)
            summary["errors"] += 1
            continue
        for pr in prs:
            summary["scanned"] += 1
            merged_at = _parse_dt(pr.get("merged_at"))
            if merged_at is None:
                # Closed but not merged — skip.
                continue
            if merged_at < cutoff:
                # Older than the window — and the listing is sorted
                # newest-first, so further PRs are also too old.
                break
            pr_number = pr.get("number")
            if not isinstance(pr_number, int) or pr_number <= 0:
                continue
            if _hooks_processed.is_processed(
                repo, pr_number, store=store
            ) is not None:
                summary["already_processed"] += 1
                continue
            try:
                _backfill_one(
                    repo=repo,
                    pr=pr,
                    pr_number=pr_number,
                    merged_at_str=str(pr.get("merged_at") or ""),
                    summary=summary,
                    store=store,
                )
            except _RateLimited:
                summary["rate_limited"] = True
                logger.warning(
                    "backfill: rate-limited mid-sweep at %s#%d; aborting",
                    repo,
                    pr_number,
                )
                return summary
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "backfill: %s#%d failed: %s", repo, pr_number, exc
                )
                summary["errors"] += 1
    return summary


# === internals =============================================================


def _backfill_one(
    *,
    repo: str,
    pr: dict,
    pr_number: int,
    merged_at_str: str,
    summary: BackfillSummary,
    store: str | Path | None,
) -> None:
    """Process exactly one PR — lookup, dispatch, ledger-write."""
    matched_cards = _pr_lookup.find_cards_by_pr(
        repo, pr_number, store=store
    )
    pr_url = (
        pr.get("html_url")
        or f"https://github.com/{repo}/pull/{pr_number}"
    )
    author_obj = pr.get("user") or {}
    author = (
        author_obj.get("login")
        if isinstance(author_obj, dict)
        else None
    )
    event = {
        "kind": "done",
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "author": author,
        "merged_at": merged_at_str,
        "card_ids": list(matched_cards),
    }
    if matched_cards:
        writes = _hooks._handle_done(event, store=store)  # noqa: SLF001
        # Count only the cards we actually flipped this sweep (not
        # the per-card already-done idempotency noops).
        for w in writes:
            if w.get("action") == "completed":
                summary["cards_marked"] += 1
    else:
        summary["no_card_match"] += 1
    _hooks_processed.mark_processed(
        repo,
        pr_number,
        merge_commit=pr.get("merge_commit_sha"),
        matched_cards=matched_cards,
        author=author,
        source="poll",
        store=store,
    )
    summary["newly_processed"] += 1


def _list_merged_prs(repo: str, runner: "_GhRunner") -> list[dict]:
    """Enumerate closed PRs for ``repo``, newest first.

    Uses the GH REST endpoint ``/repos/{slug}/pulls?state=closed`` via
    ``gh api --paginate``. Returns the raw items as a list of dicts;
    the caller filters by ``merged_at`` and by ``cutoff``. Pagination
    is bounded by GitHub (defaults to 30/page, ``--paginate`` chains
    until exhausted or rate-limit).
    """
    path = (
        f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
        f"&per_page=50"
    )
    raw = runner.json(["api", path, "--paginate"])
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    # ``--paginate`` returns a flat array when each page is an array,
    # but some adapters wrap it in {"items": [...]} — be defensive.
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [p for p in raw["items"] if isinstance(p, dict)]
    return []


def _parse_dt(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# --- subprocess runner for gh api ------------------------------------------


class _GhApiError(RuntimeError):
    """A ``gh`` call failed (non-rate-limit)."""


class _RateLimited(RuntimeError):
    """A ``gh`` call hit a GitHub rate limit. Caller aborts the sweep."""


class _GhRunner:
    """Test seam — anyone with ``.json(argv) -> parsed JSON`` works."""

    def json(self, argv: list[str]) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


class _SubprocessGhRunner(_GhRunner):
    """Default runner — shells out to ``gh`` with rate-limit detection."""

    def json(self, argv: list[str]) -> Any:
        try:
            result = subprocess.run(
                ["gh", *argv],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise _GhApiError(f"gh subprocess failed: {exc}") from exc
        stderr = (result.stderr or "").lower()
        if result.returncode != 0:
            if "rate limit" in stderr or "x-ratelimit-remaining: 0" in stderr:
                raise _RateLimited(stderr.strip())
            raise _GhApiError(
                f"gh {' '.join(argv)} exited {result.returncode}: "
                f"{stderr.strip()}"
            )
        if not result.stdout:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise _GhApiError(f"gh returned invalid JSON: {exc}") from exc


def _default_gh_runner() -> _GhRunner:
    return _SubprocessGhRunner()
