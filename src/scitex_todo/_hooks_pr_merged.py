#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``event:"pr_merged"`` payload validator — receiver-side for /hooks/done.

Lead+dev schema lock 2026-06-14: the GitHub-Action emitter sends this
wire shape. We keep the validator in a sibling module (not inside
``_hooks.py``) because the latter is already at the 512-line refactor
limit; adding the schema details inline would push it over.

Wire shape (locked)::

    {
      "event": "pr_merged",
      "repo": "owner/name",                # required, matches owner/name
      "pr_number": 209,                     # required, int > 0
      "merged_at": "2026-06-15T01:23:45Z",  # required, ISO-8601
      "pr_url": "https://.../pull/209",     # optional (reconstructed)
      "merge_commit": "abc1234...",         # optional
      "title": "...",                        # optional
      "author": "ywatanabe1989",            # optional
      "labels": ["...", ...],                # optional list[str]
      "base_ref": "develop",                # optional
      "head_ref": "feat/...",                # optional
    }

The validator NORMALISES the dict to the internal ``kind:"done"``
shape so downstream :func:`scitex_todo._hooks._handle_done` + entry-
point plugins keep working unchanged. ``card_ids`` is intentionally
empty here — the receiver (Django view) fills it from
:func:`scitex_todo._pr_lookup.find_cards_by_pr` AFTER validation, so
the lookup happens once per request and stays out of the validator's
pure-function shape.

The normalised dict carries ``_source: "pr_merged"`` so the receiver
can detect "this is the new payload" and route the dedup-ledger path
without re-parsing the original.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

#: Repo identifier — exactly two segments split by a single slash,
#: each non-empty, made of GitHub-legal characters.
_REPO_OWNER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


def _bad(field: str, detail: str) -> "Exception":
    """Build a HookEventError with consistent prefix."""
    # Imported here to avoid a circular import at module load time.
    from ._hooks import HookEventError

    return HookEventError(f"pr_merged event: {detail}")


def validate(event: dict) -> dict:
    """Validate + normalise a ``pr_merged`` payload.

    Returns the internal ``kind:"done"`` dict with ``_source: "pr_merged"``
    set. Raises :class:`scitex_todo._hooks.HookEventError` on any shape
    violation; the HTTP view turns that into a 400.
    """
    repo = event.get("repo")
    if not isinstance(repo, str) or not repo:
        raise _bad("repo", f"'repo' must be a non-empty string (got {repo!r})")
    if not _REPO_OWNER_NAME_PATTERN.match(repo):
        raise _bad("repo", f"'repo' must match 'owner/name' (got {repo!r})")

    pr_number = event.get("pr_number")
    # Reject bool explicitly — bool is a subclass of int in Python so a
    # naive isinstance check lets ``True``/``False`` sneak through.
    if isinstance(pr_number, bool) or not isinstance(pr_number, int):
        raise _bad(
            "pr_number", f"'pr_number' must be an int (got {pr_number!r})"
        )
    if pr_number <= 0:
        raise _bad(
            "pr_number", f"'pr_number' must be > 0 (got {pr_number!r})"
        )

    merged_at = event.get("merged_at")
    if not isinstance(merged_at, str) or not merged_at:
        raise _bad(
            "merged_at",
            f"'merged_at' must be a non-empty ISO-8601 string "
            f"(got {merged_at!r})",
        )
    try:
        _dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _bad(
            "merged_at",
            f"'merged_at' is not a parseable ISO-8601 string "
            f"(got {merged_at!r}): {exc}",
        )

    # Optional fields — type-check only when present.
    pr_url = event.get("pr_url")
    if pr_url is not None and (not isinstance(pr_url, str) or not pr_url):
        raise _bad(
            "pr_url",
            f"'pr_url' must be a non-empty string if present (got {pr_url!r})",
        )
    if not pr_url:
        pr_url = f"https://github.com/{repo}/pull/{pr_number}"

    merge_commit = event.get("merge_commit")
    if merge_commit is not None and not isinstance(merge_commit, str):
        raise _bad(
            "merge_commit",
            f"'merge_commit' must be a string if present (got {merge_commit!r})",
        )
    title = event.get("title")
    if title is not None and not isinstance(title, str):
        raise _bad(
            "title", f"'title' must be a string if present (got {title!r})"
        )
    author = event.get("author")
    if author is not None and not isinstance(author, str):
        raise _bad(
            "author",
            f"'author' must be a string if present (got {author!r})",
        )

    labels = event.get("labels")
    if labels is None:
        labels = []
    if not isinstance(labels, list):
        raise _bad(
            "labels",
            f"'labels' must be a list if present (got "
            f"{type(labels).__name__})",
        )
    norm_labels: list[str] = []
    for label in labels:
        if not isinstance(label, str) or not label:
            raise _bad(
                "labels",
                f"'labels' entry {label!r} is not a non-empty string",
            )
        norm_labels.append(label)

    base_ref = event.get("base_ref")
    if base_ref is not None and not isinstance(base_ref, str):
        raise _bad(
            "base_ref",
            f"'base_ref' must be a string if present (got {base_ref!r})",
        )
    head_ref = event.get("head_ref")
    if head_ref is not None and not isinstance(head_ref, str):
        raise _bad(
            "head_ref",
            f"'head_ref' must be a string if present (got {head_ref!r})",
        )

    return {
        "kind": "done",
        "_source": "pr_merged",
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "merged_at": merged_at,
        "merge_commit": merge_commit,
        "title": title,
        "author": author,
        "labels": norm_labels,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "card_ids": [],
    }
