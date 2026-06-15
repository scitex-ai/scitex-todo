#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dedup ledger for `/hooks/done` receiver — first-writer-wins per (repo, pr_number).

Lead a2a ``c4274787`` + dev a2a ``5636941a`` (2026-06-14): the bookkeeping
ring delivers the same PR-merge event possibly more than once (Action
retries on 5xx, parallel emitter rollout, manual replay). The receiver
side must be **exactly-once at the card-mutation layer** and **at-least-
once at the ledger layer** — every successful POST is logged so the
operator can audit "did this merge get recorded?" by ledger lookup
without re-touching ``tasks.yaml``.

## Wire contract — dedup key

``(repo, pr_number)``. GitHub merges a PR exactly once, so this 2-tuple
uniquely identifies a merge event for the dedup-ring's purposes. We
deliberately do NOT include ``merge_commit`` or ``conclusion`` in the
dedup key (lead-locked 2026-06-14): squash-vs-merge changes the SHA on
re-emit, and merges have no `conclusion` field.

## Ledger file

``~/.scitex/todo/.processed_done_events.json`` — sibling to
``tasks.yaml`` so the same per-user store-dir hosts both. Format::

    {
      "ywatanabe1989/scitex-todo#209": {
        "first_processed_at": "2026-06-15T01:23:46Z",
        "merge_commit": "abc1234...",        # nullable
        "matched_cards": ["card-1", ...],     # may be empty
        "author": "ywatanabe1989"             # nullable
      },
      ...
    }

## Lock model

The ledger uses the SAME ``fcntl.flock`` sentinel pattern as
``tasks.yaml`` (see :func:`_model._store_lock`). Sentinel file is
``.processed_done_events.json.lock`` sibling to the ledger. Acquire
order (when both are needed in one transaction):

    tasks.yaml.lock  →  .processed_done_events.json.lock

This order matches the receiver flow (mark card done first, then
record ledger entry inside the still-held tasks.yaml lock; ledger
lock is independent). Holding tasks.yaml.lock for the ledger write
costs little since the ledger is a small JSON file.

If the ledger write fails AFTER the card mutation succeeds we have a
duplicate-risk window (next replay would re-mutate). The per-card
state check in :func:`scitex_todo._hooks._handle_done` (already-done +
same pr_url → noop) covers that residual case, so the end-to-end
guarantee remains exactly-once at the card layer.

## Cleanup / TTL

Out of scope for PR1. Pruning belongs to a future ``hooks-done prune``
admin verb (see design doc). The ledger grows O(merged PRs across all
tracked repos) — manageable for the operator's actual workload
(hundreds, not millions).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator

from ._paths import resolve_tasks_path

#: Filename of the dedup ledger, kept as a sibling of ``tasks.yaml``.
LEDGER_FILENAME = ".processed_done_events.json"


def _ledger_path(store: str | Path | None = None) -> Path:
    """Resolve the ledger path next to the active tasks.yaml.

    The ledger lives in the SAME directory as the task store so per-user
    isolation, backup hygiene, and lock-domain locality are all preserved
    without a new env var.
    """
    tasks_path = Path(resolve_tasks_path(store))
    return tasks_path.parent / LEDGER_FILENAME


def _lock_path(ledger: Path) -> Path:
    """Sentinel lock file used by :func:`_ledger_lock`."""
    return ledger.parent / f"{ledger.name}.lock"


@contextlib.contextmanager
def _ledger_lock(ledger: Path) -> Iterator[None]:
    """Acquire an exclusive fcntl.flock on the ledger sentinel.

    Mirrors :func:`_model._store_lock` so the receiver can use the same
    waiting / cleanup semantics. Held across read-check-write so the
    first-writer-wins invariant survives concurrent POSTs.
    """
    lock = _lock_path(ledger)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # ``open("a+")`` so we don't fight the file's pointer state across
    # processes — works on FS lacking O_EXLOCK (WSL2 ext4 friendly).
    fd = lock.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def _load(ledger: Path) -> dict[str, dict[str, Any]]:
    """Read + parse the ledger file. Missing file = empty dict.

    Round-trip-safe: a malformed file is treated as empty (logged at
    callsite) so a stray edit can't take the receiver down. The next
    successful write overwrites cleanly.
    """
    if not ledger.exists():
        return {}
    try:
        raw = ledger.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _atomic_write(ledger: Path, payload: dict[str, dict[str, Any]]) -> None:
    """Dump JSON + fsync + os.replace — atomic across crash boundaries.

    Same crash-safe pattern as :func:`_model._save_tasks_unlocked` for
    the tasks store: ensure parent dir, write to ``.tmp`` sibling,
    fsync that file, then ``os.replace`` (POSIX atomic). The ledger is
    a leaf consumer of this pattern; no post-dump validation hook (the
    JSON either parses or the next reader gets the previous version).
    """
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_suffix(ledger.suffix + ".tmp")
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    with tmp.open("w", encoding="utf-8") as fp:
        fp.write(body)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, ledger)


def make_key(repo: str, pr_number: int) -> str:
    """Build the ledger key for ``(repo, pr_number)``.

    Format: ``{repo}#{pr_number}``. The ``#`` separator is intentional —
    ``/`` already appears inside ``repo`` (``owner/name``), so a delimiter
    that's URL-illegal-but-JSON-key-safe keeps the key readable in
    grep output without further escaping.
    """
    if not isinstance(repo, str) or not repo:
        raise ValueError(f"make_key: repo must be a non-empty string (got {repo!r})")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError(
            f"make_key: pr_number must be a positive int (got {pr_number!r})"
        )
    return f"{repo}#{pr_number}"


def is_processed(
    repo: str,
    pr_number: int,
    *,
    store: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the ledger entry for ``(repo, pr_number)`` if already seen.

    Returns ``None`` when this is the first time we see the merge.
    Acquires the ledger lock for the read so a concurrent
    :func:`mark_processed` cannot race us into seeing a partial write.
    """
    key = make_key(repo, pr_number)
    ledger = _ledger_path(store=store)
    with _ledger_lock(ledger):
        return _load(ledger).get(key)


def mark_processed(
    repo: str,
    pr_number: int,
    *,
    merge_commit: str | None = None,
    matched_cards: list[str] | None = None,
    author: str | None = None,
    processed_at: str | None = None,
    store: str | Path | None = None,
) -> dict[str, Any]:
    """First-writer-wins record of a processed merge event.

    Returns the ledger entry actually persisted. If a prior entry exists
    for the same key, the prior entry is returned UNCHANGED — the caller
    can detect the dedup race by comparing ``processed_at`` (the
    returned entry's value will be earlier than the value we passed in).

    Parameters
    ----------
    repo, pr_number
        Identify the merge.
    merge_commit, author
        Optional metadata captured for audit.
    matched_cards
        The list of card_ids the receiver actually marked done. May be
        empty (no-card-match case is still a successful POST).
    processed_at
        ISO-8601 UTC string; defaults to ``utcnow().isoformat() + 'Z'``.
    store
        Override the tasks-store path; the ledger lives next to it.
    """
    key = make_key(repo, pr_number)
    if processed_at is None:
        processed_at = (
            _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
        )
    new_entry: dict[str, Any] = {
        "first_processed_at": processed_at,
        "merge_commit": merge_commit,
        "matched_cards": list(matched_cards or []),
        "author": author,
    }
    ledger = _ledger_path(store=store)
    with _ledger_lock(ledger):
        data = _load(ledger)
        existing = data.get(key)
        if existing is not None:
            # First writer wins — caller is racing; report the
            # canonical entry without mutating.
            return dict(existing)
        data[key] = new_entry
        _atomic_write(ledger, data)
        return dict(new_entry)


def list_entries(
    *,
    store: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a snapshot copy of the ledger. Read-only.

    Held under the ledger lock so the returned dict is consistent
    against any concurrent :func:`mark_processed`.
    """
    ledger = _ledger_path(store=store)
    with _ledger_lock(ledger):
        snapshot = _load(ledger)
    return {k: dict(v) for k, v in snapshot.items()}
