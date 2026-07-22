#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the real board still INTACT? The criterion behind conftest's session guard.

Lives in its own module, not inline in ``conftest.py``, so its own tests can
import it WITHOUT re-running that module's import-time side effects (which
re-point every store env var at a fresh, unbootstrapped scratch directory —
harmless at session start, hostile in the middle of a run).

See :func:`damage` for why intactness, rather than "did the file change", is
the criterion.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def content_or_none(path: Path) -> dict | None:
    """``{count, meta, integrity}`` for a real store, or ``None`` if unreadable.

    Read-only (``mode=ro``, so a missing file is NOT created) and total: any
    ``sqlite3.Error`` yields ``None``. A hiccup reading the board is not
    evidence of the thing this exists to detect, so it must not blow up
    collection or teardown.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        return {
            "count": conn.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "meta": dict(conn.execute("SELECT key, value FROM schema_meta")),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def damage(before: dict | None, after: dict | None) -> str | None:
    """Describe how ``before`` -> ``after`` is DAMAGE, or ``None`` if it isn't.

    THE CRITERION IS CONTENT, NOT THE FILE'S mtime/size. This board is shared
    and LIVE: other fleet agents write to it throughout any run longer than a
    few seconds, so "the file changed" fires on essentially every session and
    says nothing about whether THIS SUITE wrote. That is not a harmless false
    positive — a gate that cries wolf every run is one people learn to scroll
    past, and this gate is the one that caught three production wipes.
    Measured 2026-07-22: a 69s single-test run tripped the old criterion
    purely on peer writes (sac and scitex-ui cards, every timestamp and
    author accounted for), with the board's own content provably intact.

    So each check below is chosen to be IMPOSSIBLE for a legitimate peer
    write to trip:

    * ``count`` may grow but must never SHRINK. The store is append-only by
      operator ruling ("a written card never disappears"; deletes are
      tombstones), so a decrease is always a bug, whoever caused it. Every
      wipe this suite has inflicted is exactly this shape: 2136->21,
      2138->1, 2138->3, 2170->18.
    * ``schema_meta`` must be identical. This is the store's identity, its
      ``min_client_version`` floor and its DO-NOT-MIRROR sentinel — peers
      never rewrite it, and the 2026-07-21 wipe #5 damaged precisely this.
    * ``integrity_check`` must stay ``ok``.
    * The store must not become UNREADABLE when it was readable.

    HONEST LIMIT, stated rather than implied: this layer does not catch a leak
    that only INSERTS a card (count grows, meta intact) — indistinguishable
    here from a peer write. The primary barrier against that is the env
    pinning in ``conftest.py``, not this detector; this is the second line,
    and it bounds the damage class that actually destroyed data.
    """
    if before is None:
        return None  # not readable before — nothing to compare against
    if after is None:
        return "store became UNREADABLE during the session"
    if after["count"] < before["count"]:
        return f"card count SHRANK: {before['count']} -> {after['count']}"
    if after["meta"] != before["meta"]:
        return f"schema_meta CHANGED: {before['meta']} -> {after['meta']}"
    if after["integrity"] != "ok":
        return f"integrity_check is {after['integrity']!r} (was 'ok')"
    return None


def damaged_candidates(
    before: dict[Path, dict | None],
    candidates: tuple[Path, ...],
) -> list[tuple[Path, str]]:
    """Re-read every candidate NOW and return ``(path, why)`` for the damaged.

    The whole detection chain in one callable — snapshot lookup, live re-read,
    and the :func:`damage` verdict — so it can be proven end to end against
    real databases instead of leaving the wiring untested inside a
    session-scoped fixture that only runs at teardown.
    """
    found = [
        (path, damage(before.get(path), content_or_none(path))) for path in candidates
    ]
    return [(path, why) for path, why in found if why]


# EOF
