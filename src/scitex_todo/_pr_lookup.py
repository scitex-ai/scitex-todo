#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card lookup by GitHub PR identity — receiver-side helper for /hooks/done.

Lead+dev schema lock 2026-06-14: dev's emitter does NOT include
``card_ids`` in the ``event:"pr_merged"`` payload. The receiver
discovers cards by scanning the task store for ones whose ``pr_url``
field matches the merged PR.

Lives in its own module (not in ``_store``) so the bookkeeping ring's
lookup path is reviewable in one self-contained file — the receiver
already imports half the store; adding the lookup function here keeps
``_store.py`` from growing further past its 512-line refactor limit.

The match function is **read-only + lock-free** (single-shot snapshot
via :func:`scitex_todo._model.load_tasks`). The receiver acquires the
tasks-yaml lock separately, around the WRITE; the lookup is just a
hint at which cards to write to.

## Match strategy

Most-specific first, so a more permissive variant cannot shadow a
canonical hit:

1. Exact match on the canonical github.com URL.
2. Exact match on the ``www.`` host variant.
3. Substring match on ``/{repo}/pull/{pr_number}`` with a boundary
   character check after the number — defensive against trailing
   slashes / query strings / fragment identifiers, and guards against
   ``#209`` accidentally matching ``#2099``.

Returns the LIST of all matching card ids (a single PR may close
multiple cards on a train PR). Empty list = no match (still a
successful POST; receiver records a ledger entry for audit).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _model
from ._store import _resolved_store

__all__ = ["find_cards_by_pr"]


def find_cards_by_pr(
    repo: str,
    pr_number: int,
    *,
    store: str | Path | None = None,
) -> list[str]:
    """Return the list of card ids whose ``pr_url`` points at ``(repo, pr_number)``.

    Parameters
    ----------
    repo
        ``owner/name`` repository identifier.
    pr_number
        Positive integer PR number.
    store
        Override the tasks store path; ``None`` uses the normal
        precedence chain via :func:`scitex_todo._paths.resolve_tasks_path`.

    Returns
    -------
    list[str]
        Zero or more card ids. The order is the order of the cards in
        the underlying store (stable across reads barring concurrent
        writes); callers MUST treat it as a set for dedup purposes.

    Raises
    ------
    ValueError
        On malformed inputs (empty repo, non-positive pr_number).
    """
    if not isinstance(repo, str) or not repo:
        raise ValueError(
            f"find_cards_by_pr: 'repo' must be a non-empty string (got {repo!r})"
        )
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError(
            f"find_cards_by_pr: 'pr_number' must be a positive int "
            f"(got {pr_number!r})"
        )
    canonical = f"https://github.com/{repo}/pull/{pr_number}"
    www = f"https://www.github.com/{repo}/pull/{pr_number}"
    substring = f"/{repo}/pull/{pr_number}"
    tasks_path = _resolved_store(store)
    tasks: list[Any] = _model.load_tasks(tasks_path)
    hits: list[str] = []
    for t in tasks:
        pr_url = t.get("pr_url") if isinstance(t, dict) else None
        if not isinstance(pr_url, str) or not pr_url:
            continue
        card_id = t.get("id") if isinstance(t, dict) else None
        if not isinstance(card_id, str) or not card_id:
            continue
        if pr_url == canonical or pr_url == www:
            hits.append(card_id)
            continue
        idx = pr_url.find(substring)
        if idx >= 0:
            end = idx + len(substring)
            # Boundary check — accept end-of-string OR url-path delimiter.
            # Prevents /209 from matching inside /2099.
            if end >= len(pr_url) or pr_url[end] in "/?#":
                hits.append(card_id)
    return hits
