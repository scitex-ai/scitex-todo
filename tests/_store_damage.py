#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the real board still INTACT? The criterion behind conftest's session guard.

Lives in its own module, not inline in ``conftest.py``, so its own tests can
import it WITHOUT re-running that module's import-time side effects (which
re-point every store env var at a fresh, unbootstrapped scratch directory —
harmless at session start, hostile in the middle of a run).

See :func:`damage` for why intactness, rather than "did the file change", is
the criterion, and for exactly what this layer does and does not catch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Every table whose row count must never DECREASE.
#:
#: NOT just ``tasks``. Watching only the card table was the first version's
#: defect, and it is the shape of a REAL wipe that leaves cards untouched:
#: ``_db_mirror._sync_sections`` issues ``DELETE FROM user_names`` +
#: ``DELETE FROM users`` (and ``DELETE FROM notifications``) whenever a
#: section hash differs, then re-inserts from the incoming document's
#: ``users``/``inboxes`` sections — and ``_db_sections`` RETURNS EARLY without
#: raising when those keys are absent, so a document carrying every task id
#: but no ``users`` key deletes the fleet's whole identity registry, inserts
#: nothing, raises nothing, and commits. ``users`` holds each agent's
#: ``turn_url``/``a2a_port``/``notify_json``; ``messages`` holds every DM.
#: Card count, ``schema_meta`` and ``integrity_check`` all stay pristine.
#:
#: Monotonicity is safe for these the same way it is safe for ``tasks``: no
#: shipped code path deletes from them except an in-transaction per-card
#: rewrite, so a legitimate peer write never lowers a count.
MONOTONE_TABLES = (
    "tasks",
    "task_comments",
    "task_edges",
    "task_roles",
    "users",
    "user_names",
    "inbox_recipients",
    "notifications",
    "messages",
)


def content_or_none(path: Path) -> dict | None:
    """Snapshot a real store, or ``None`` when the file is ABSENT.

    Returns ``{"counts", "task_ids", "meta", "integrity"}``. A path that does
    not exist yields ``None`` (nothing to protect). A path that EXISTS but
    cannot be read yields ``{"unreadable": <reason>}`` rather than ``None`` —
    those are different facts and conflating them makes the gate fail OPEN,
    silently disarming itself for the whole session.

    Read-only (``mode=ro``, so a missing file is never created).
    """
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return {"unreadable": f"connect failed: {exc}"}
    try:
        counts = {}
        for table in MONOTONE_TABLES:
            try:
                counts[table] = conn.execute(
                    f"SELECT count(*) FROM {table}"  # noqa: S608 — fixed literals
                ).fetchone()[0]
            except sqlite3.Error:
                # Table absent in this schema version. Not damage; just not
                # observable. Recorded as None so a later snapshot showing a
                # real number is not mistaken for growth from zero.
                counts[table] = None
        return {
            "counts": counts,
            # The ID SET, not just the cardinality: delete-then-reinsert at
            # equal count is invisible to a count check, and "2,286 real cards
            # replaced by 2,286 fixture cards" is a total loss that reads as
            # unchanged.
            "task_ids": frozenset(r[0] for r in conn.execute("SELECT id FROM tasks")),
            "meta": dict(conn.execute("SELECT key, value FROM schema_meta")),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }
    except sqlite3.Error as exc:
        return {"unreadable": f"read failed: {exc}"}
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
    MEASURED 2026-07-22, the old criterion firing on a peer's write::

        before (mtime_ns, size) = (1784700394236217844, 34537472)
        after  (mtime_ns, size) = (1784701251174118319, 34537472)

    Identical size, mtime moved: a peer updating a card in place, reported as
    "REAL TASK STORE MUTATED".

    Each check below is chosen so a legitimate peer write cannot trip it:

    * NO WATCHED TABLE'S ROW COUNT MAY SHRINK — see :data:`MONOTONE_TABLES`
      for why the card table alone is not enough. Every wipe this suite has
      inflicted is this shape: 2136->21, 2138->1, 2138->3, 2170->18.
    * NO TASK ID MAY DISAPPEAR. Strictly stronger than the count, and the
      reason the count alone is insufficient: deletes are tombstones by
      operator ruling ("a written card never disappears"), so a vanished id
      is always damage, while peers only ever add ids.
    * ``schema_meta`` must be identical — the store's identity, its
      ``min_client_version`` floor and its DO-NOT-MIRROR sentinel. Peers never
      rewrite it, and the 2026-07-21 wipe #5 damaged precisely this.
    * ``integrity_check`` must not go from ``ok`` to broken.
    * A store readable at snapshot time must not become unreadable — and a
      store that was unreadable WHEN WE SNAPSHOTTED IT is reported too, since
      that means this gate was never armed.

    WHAT THIS STILL DOES NOT CATCH, stated plainly rather than implied:

    * IN-PLACE MUTATION OF EXISTING CARDS at constant ids — a leaked
      ``update_task``/``reassign_task``/triage sweep that rewrites bodies,
      statuses or ownership without adding or removing rows. This is not an
      oversight that can be closed at this layer: peers legitimately update
      cards continuously, so any digest over row CONTENT would fire on every
      run, which is the failure mode this whole change exists to remove.
    * PURE INSERTS by a leaking test — indistinguishable here from a peer
      adding a card.

    For both, the barrier is the env pinning in ``conftest.py``, not this
    detector. This layer bounds the damage classes that actually destroyed
    data; it does not replace the prevention.
    """
    if before is None:
        # Absent when we snapshotted. Creation is not damage.
        return None
    if isinstance(before, dict) and "unreadable" in before:
        return (
            f"store was UNREADABLE when this session started "
            f"({before['unreadable']}) — this gate was never armed for it"
        )
    if after is None:
        return "store DISAPPEARED during the session (it existed at start)"
    if "unreadable" in after:
        return f"store became UNREADABLE during the session ({after['unreadable']})"

    for table in MONOTONE_TABLES:
        b, a = before["counts"].get(table), after["counts"].get(table)
        if b is None or a is None:
            continue
        if a < b:
            return f"{table} row count SHRANK: {b} -> {a}"

    vanished = before["task_ids"] - after["task_ids"]
    if vanished:
        sample = sorted(vanished)[:5]
        return (
            f"{len(vanished)} task id(s) DISAPPEARED (deletes are tombstones; "
            f"a card never vanishes). e.g. {sample}"
        )

    if after["meta"] != before["meta"]:
        return f"schema_meta CHANGED: {before['meta']} -> {after['meta']}"

    if after["integrity"] != "ok":
        if before["integrity"] == "ok":
            return (
                f"integrity_check went 'ok' -> {after['integrity']!r} "
                f"DURING this session"
            )
        # Attribute honestly: we did not break this, but it is still broken.
        return (
            f"integrity_check was ALREADY {before['integrity']!r} before this "
            f"session started (NOT caused by this run — the board still needs "
            f"attention)"
        )
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
