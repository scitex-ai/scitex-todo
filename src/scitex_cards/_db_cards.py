#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The CARD pipeline of the shadow DB: tasks + their comments / edges / roles.

The other half of the split :mod:`scitex_cards._db_sections` describes and was
missing. That module owns the NON-CARD sections (``users`` / ``notifications``
/ ``messages``), which are rebuilt only when their section hash moves; this one
owns the CARD rows, which every hot path touches on every write.

They are separated because they have different costs and different owners: a
card write re-inserts ONE card here and touches nothing next door, and the
sections are keyed to files this module never reads (``messages`` comes from
the ``threads.yaml`` sidecar, not the doc).

Left behind in :mod:`scitex_cards._db_bootstrap` is the orchestration proper —
which doc gets read, which tables get cleared, what the DB is stamped as.
"""

from __future__ import annotations

import logging
import sqlite3

from ._db_payload import CARD_JSON_COL, card_payload_json
from ._db_payload import json_or_none as _json_or_none

logger = logging.getLogger(__name__)


#: (column, yaml-key) pairs for the scalar ``tasks`` columns. ``group`` maps to
#: the ``grp`` column (SQL reserved word); ``deadlines`` / ``_log_meta`` /
#: ``row_order`` / ``card_json`` are handled separately (JSON / positional).
_TASK_SCALAR_COLS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("title", "title"),
    ("status", "status"),
    ("kind", "kind"),
    ("blocker", "blocker"),
    ("task", "task"),
    ("note", "note"),
    ("goal", "goal"),
    ("project", "project"),
    ("repo", "repo"),
    ("host", "host"),
    ("agent", "agent"),
    ("assignee", "assignee"),
    ("scope", "scope"),
    ("grp", "group"),
    ("priority", "priority"),
    ("parent", "parent"),
    ("pr_url", "pr_url"),
    ("issue_url", "issue_url"),
    ("deadline", "deadline"),
    ("scheduled", "scheduled"),
    ("created_at", "created_at"),
    ("last_activity", "last_activity"),
    ("started_at", "started_at"),
    ("finished_at", "finished_at"),
    ("created_by", "created_by"),
    ("job_id", "job_id"),
    ("command", "command"),
)

#: The FULL ordered column list every ``tasks`` INSERT writes: the scalars, the two
#: JSON side-cars, the positional ``row_order``, and the verbatim ``card_json``.
#:
#: PUBLIC ON PURPOSE. The S2 read guard probes THIS TUPLE for ``card_json`` to answer
#: "can the code running in THIS process actually populate the payload column?" — a
#: SYMBOL check against the imported object, never a version string. A version string
#: is metadata, and metadata lies: a stale wheel, an orphaned ``.dist-info`` and a SIF
#: baked months ago all report a version that outlived the code beside them. This repo
#: paid 135 SECONDS PER CARD WRITE for exactly that mistake on 2026-07-13.
TASK_INSERT_COLS: tuple[str, ...] = tuple(
    [col for col, _ in _TASK_SCALAR_COLS]
    + ["deadlines_json", "log_meta_json", "row_order", CARD_JSON_COL]
)

def _dedupe_last_wins(tasks: list) -> list[tuple[int, dict]]:
    """``(row_order, card)`` pairs, duplicate ids collapsed — LAST occurrence wins.

    The semantics ``INSERT OR REPLACE`` gave us for free, hoisted into Python so the
    SQL can be a plain ``INSERT`` (see :func:`_insert_tasks` — that word cost 42x).
    A duplicate card id is a DATA BUG, not routine: REPLACE absorbed it silently
    (and still appended BOTH copies' comments). Same winner, said out loud.
    """
    by_id: dict[str, tuple[int, dict]] = {}
    ordered: list[tuple[int, dict]] = []
    dupes: list[str] = []
    for order, row in enumerate(tasks):
        if not isinstance(row, dict):
            continue
        tid = row.get("id")
        if isinstance(tid, str) and tid:
            if tid in by_id:
                dupes.append(tid)
            by_id[tid] = (order, row)
        else:
            ordered.append((order, row))
    if dupes:
        logger.error(
            "!! DUPLICATE CARD ID(S) IN THE CANONICAL STORE: %s. The mirror keeps "
            "the LAST occurrence of each (the same row `INSERT OR REPLACE` would "
            "have kept), but the YAML itself is inconsistent and should be "
            "repaired — two cards cannot share an id.",
            ", ".join(sorted(set(dupes))),
        )
    ordered.extend(by_id.values())
    ordered.sort(key=lambda pair: pair[0])
    return ordered


def _insert_tasks(
    conn: sqlite3.Connection, tasks: list, *, replace: bool = True
) -> dict[str, int]:
    """Insert every card + its children.

    ``replace`` picks the conflict clause, and it is worth 42x — MEASURED on the
    live 1,370-card store (2026-07-13)::

        INSERT OR REPLACE INTO tasks , FK ON : 4,592 us/row  -> 6.3 s for the store
        INSERT           INTO tasks , FK ON  :   110 us/row  -> 0.15 s

    ``tasks`` is a PARENT of ``task_comments`` / ``task_edges`` / ``task_roles``
    (``ON DELETE CASCADE``), so under ``PRAGMA foreign_keys=ON`` a REPLACE — a DELETE
    plus an INSERT — runs the whole cascade/FK-check machinery FOR EVERY ROW. The
    control group is next door: ``task_comments`` already uses a plain INSERT and FK
    enforcement costs it NOTHING (150 vs 149 us/row, FK on vs off). It is
    REPLACE-**on-a-parent** that is expensive, not foreign keys. (``PRAGMA
    defer_foreign_keys=ON`` does NOT help — measured SLOWER. Do not reach for it.)

    So the clause is a PRECONDITION, not a style choice, and the callers differ:

    * ``replace=False`` — caller ALREADY DELETED these rows, so a conflict is
      impossible and REPLACE is pure waste. :func:`_rebuild_from_doc` clears every
      table first; this is its 42x.
    * ``replace=True`` (DEFAULT, the SAFE one) — caller is UPSERTING over rows that
      may still be present (the incremental mirror re-writes one changed card
      without dropping its ``tasks`` row). A plain INSERT would raise ``UNIQUE
      constraint failed: tasks.id`` there, so REPLACE is load-bearing.

    Duplicate ids are collapsed by :func:`_dedupe_last_wins` (last-wins — the winner
    REPLACE would have picked), so ``replace=False`` cannot conflict with itself.
    """
    counts = {"tasks": 0, "comments": 0, "edges": 0, "roles": 0}
    placeholders = ", ".join("?" for _ in TASK_INSERT_COLS)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    insert_sql = (
        f"{verb} INTO tasks ({', '.join(TASK_INSERT_COLS)}) VALUES ({placeholders})"
    )
    for order, row in _dedupe_last_wins(tasks):
        values = [row.get(ykey) for _, ykey in _TASK_SCALAR_COLS]
        values.append(_json_or_none(row.get("deadlines")))
        values.append(_json_or_none(row.get("_log_meta")))
        values.append(order)
        # The VERBATIM card — the payload an S2 read reconstructs from, exactly as
        # it appeared in the YAML (unknown keys, key order, types and all). The
        # typed columns above are only the INDEX. See :mod:`_db_payload`.
        values.append(card_payload_json(row))
        conn.execute(insert_sql, values)
        counts["tasks"] += 1
        tid = row.get("id")
        counts["comments"] += _insert_comments(conn, tid, row.get("comments"))
        counts["edges"] += _insert_edges(conn, tid, row)
        counts["roles"] += _insert_roles(conn, tid, row)
    return counts


def _insert_comments(conn, task_id, comments) -> int:
    if not isinstance(comments, list):
        return 0
    n = 0
    for seq, c in enumerate(comments):
        if not isinstance(c, dict):
            continue
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                seq,
                c.get("author"),
                c.get("ts"),
                c.get("kind"),
                "" if c.get("text") is None else str(c.get("text")),
            ),
        )
        n += 1
    return n


def _insert_edges(conn, task_id, row) -> int:
    n = 0
    for edge_type in ("depends_on", "blocks"):
        targets = row.get(edge_type)
        if not isinstance(targets, list):
            continue
        for dst in targets:
            if not (isinstance(dst, str) and dst):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO task_edges"
                "(src_task_id, dst_task_id, edge_type) VALUES (?, ?, ?)",
                (task_id, dst, edge_type),
            )
            n += 1
    return n


def _insert_roles(conn, task_id, row) -> int:
    n = 0
    for role, key in (("collaborator", "collaborators"), ("subscriber", "subscribers")):
        members = row.get(key)
        if not isinstance(members, list):
            continue
        for who in members:
            if not (isinstance(who, str) and who):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO task_roles(task_id, who, role) "
                "VALUES (?, ?, ?)",
                (task_id, who, role),
            )
            n += 1
    return n

__all__ = [
    "TASK_INSERT_COLS",
    "_dedupe_last_wins",
    "_insert_comments",
    "_insert_edges",
    "_insert_roles",
    "_insert_tasks",
]

# EOF
