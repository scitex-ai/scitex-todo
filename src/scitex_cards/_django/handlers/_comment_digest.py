#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derived comment scalars for the ``/graph`` list payload.

Why this module exists
----------------------
``/graph`` used to ship every card's full ``comments[]`` thread. Measured on
2026-07-17 over 1,784 cards that was ~4.06 MB of a ~6 MB payload — re-fetched
whole on every store change and rebuilt on the client's main thread. gzip
(enabled in ``settings.py``) cuts the wire cost but not the parse or the
render, which is what a phone actually feels. The scitex-cards GUI is read on
a phone daily, so the thread is replaced here by four small scalars.

The full thread is NOT lost: consumers that need it fetch it per card from
``/chat/<card_id>`` (see ``handlers/chat.py``), which passes comment dicts
through as-is and therefore preserves each comment's ``kind`` — the field the
route-trace timeline renders.

The scalars are chosen to cover every *list-surface* consumer found in the
frontend, so that no card-level affordance needs the thread:

- ``comment_count``       — the badge glyphs and the sortable table column.
- ``last_comment``        — rendered on Time-view cards (author + text).
- ``first_comment_ts``    — recency-sort fallback for legacy cards that
                            predate ``created_at``.
- ``first_comment_author``— creator fallback for those same legacy cards.
"""

# Length budget for ``last_comment.text``.
#
# Mirrors the ``_truncate(s, 160)`` contract in
# ``static/scitex_cards/board_v3/timeline.js``: when over budget keep 159
# characters and append the ellipsis, so the result is exactly 160. Truncating
# server-side is the point — the untruncated thread is what made the payload
# heavy. Re-truncating client-side stays a no-op, so the rendered result is
# byte-identical to the pre-slim GUI.
LAST_COMMENT_CHARS = 160


def truncate_comment(text: str) -> str:
    """Truncate ``text`` to ``LAST_COMMENT_CHARS``, matching ``_truncate``."""
    if len(text) > LAST_COMMENT_CHARS:
        return text[: LAST_COMMENT_CHARS - 1] + "…"
    return text


def comment_digest(task: dict) -> dict:
    """Return the derived comment scalars for one task.

    Always returns all four keys so the frontend can read them without
    null-checks, mirroring the old contract where ``comments`` was always a
    list. A task with no comments yields a zero count and ``None`` elsewhere.
    """
    comments = task.get("comments") or []
    if not comments:
        return {
            "comment_count": 0,
            "last_comment": None,
            "first_comment_ts": None,
            "first_comment_author": None,
        }
    first = comments[0] or {}
    last = comments[-1] or {}
    return {
        "comment_count": len(comments),
        "last_comment": {
            "author": last.get("author"),
            "text": truncate_comment(str(last.get("text") or "")),
        },
        "first_comment_ts": first.get("ts"),
        "first_comment_author": first.get("author"),
    }
