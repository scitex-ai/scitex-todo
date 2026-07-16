#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YAML → shadow-SQLite bootstrap import — STAGE S0 (RFC #348).

Reads the CURRENT canonical YAML store (``tasks.yaml`` — tasks + users +
inboxes — plus the ``threads.yaml`` sidecar) via the existing load path and
populates the shadow SQLite DB across every table. The YAML is the source of
truth and is opened READ-ONLY here; the import NEVER writes back to it (the S0
safety boundary — verified byte-for-byte by the tests).

Idempotency
-----------
The import is a FULL REBUILD inside one transaction: it clears every data
table and re-inserts from the YAML. Re-running therefore yields byte-identical
DB state (``import --from-yaml`` twice == once). This is the simplest correct
idempotency for a SHADOW that is, by definition, a projection of the YAML —
there is no DB-only state to preserve in S0.

Field mapping (see :mod:`scitex_cards._db` for the schema rationale):
  * scalar Task fields → columns (``group`` → ``grp``; SQL reserved word),
  * ``deadlines`` / ``_log_meta`` → JSON TEXT columns,
  * ``comments`` → ``task_comments`` (``seq`` = position),
  * ``depends_on`` / ``blocks`` → ``task_edges`` (directional),
  * ``collaborators`` / ``subscribers`` → ``task_roles``,
  * ``users`` → ``users`` + ``user_names`` (alias fan-out),
  * ``inboxes`` map → ``notifications`` (``recipient_id`` = map key),
  * ``threads.yaml`` map → ``messages`` (``thread_key`` = map key).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ._db import SCHEMA_VERSION, connect, init_schema, resolve_db_path
from ._db_freshness import stamp_yaml_provenance
from ._db_payload import CARD_JSON_COL, card_payload_json
from ._db_payload import json_or_none as _json_or_none
from ._db_sections import (  # re-exported: _db_mirror imports these from here
    _gen_id,  # noqa: F401
    _insert_messages,
    _insert_notifications,
    _insert_users,
)
from ._paths import resolve_tasks_path

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

#: Data tables cleared before a rebuild, child-before-parent so the explicit
#: deletes never fight the FK order (cascade would also cover the children).
_CLEAR_ORDER: tuple[str, ...] = (
    "task_comments",
    "task_edges",
    "task_roles",
    "tasks",
    "user_names",
    "users",
    "notifications",
    "messages",
)


def _load_source(
    tasks_path: str | Path | None,
) -> tuple[dict, Path, tuple[int, int] | None]:
    """Load the YAML doc (tasks/users/inboxes) + the store path + its stat snapshot.

    Uses the existing ``load_doc`` read primitive (no validation gate — a
    bootstrap must ingest whatever is on disk). A missing store yields an
    empty doc so ``db import`` on a fresh install is a no-op, not a crash.

    The stat is taken BEFORE the parse, on purpose. If another writer rewrites the
    store while we are reading it, the snapshot we stamp is the pre-read one, which
    will no longer match the file on disk — so the next read declares the mirror
    STALE and falls back to YAML. Stat-AFTER-parse would instead stamp a version of
    the file the DB never saw, and the mirror would look fresh while being wrong.
    """
    from ._db_freshness import stat_snapshot
    from ._model import load_doc

    path = resolve_tasks_path(tasks_path)
    if not path.exists():
        return {}, path, None
    snapshot = stat_snapshot(path)
    doc = load_doc(path, validate=False)
    return (doc if isinstance(doc, dict) else {}), path, snapshot


def _load_threads(store_path: Path) -> dict[str, list[dict]]:
    """Load the ``threads.yaml`` sidecar map (absent → empty)."""
    from . import _threads

    tpath = _threads.threads_path(store_path)
    return _threads._load_threads(tpath)


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


#: Tables owned by the ``tasks.yaml`` doc. The ``messages`` table is DELIBERATELY
#: absent: it is derived from the ``threads.yaml`` SIDECAR, which the doc-write
#: path never touches. A doc mirror that cleared ``messages`` would silently
#: destroy every DM thread on each card write — the tables must be owned by
#: exactly the file that produces them.
_DOC_CLEAR_ORDER = tuple(t for t in _CLEAR_ORDER if t != "messages")


def _rebuild_from_doc(
    conn: sqlite3.Connection,
    doc: dict,
    *,
    threads: dict[str, list[dict]] | None = None,
) -> dict:
    """Rebuild the doc-derived tables from an ALREADY-PARSED doc, in ONE txn.

    The shared core of :func:`import_from_yaml` (which reads the doc off disk)
    and :func:`mirror_doc` (S1 dual-write, which already holds the doc in memory
    under the store lock and must NOT pay an 11-second re-parse to get it back).

    ``threads`` rebuilds the ``messages`` table too. Pass it ONLY when the caller
    genuinely owns the threads sidecar — the dual-write path does not, and
    clearing ``messages`` there would wipe every DM on every card write.

    Caller owns the transaction boundary and the connection.
    """
    clear = _CLEAR_ORDER if threads is not None else _DOC_CLEAR_ORDER
    for table in clear:
        conn.execute(f"DELETE FROM {table}")

    summary: dict = {}
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    # replace=False: every row was just DELETEd above, so a conflict is impossible
    # and REPLACE would only buy SQLite's per-row FK-cascade check — which was 6.3 s
    # of this rebuild's 7.3 s. See _insert_tasks.
    summary.update(
        _insert_tasks(conn, tasks if isinstance(tasks, list) else [], replace=False)
    )
    summary.update(_insert_users(conn, doc.get("users")))
    summary["notifications"] = _insert_notifications(conn, doc.get("inboxes"))
    if threads is not None:
        summary["messages"] = _insert_messages(conn, threads)
    return summary


def _stamp_meta(conn: sqlite3.Connection, source: str) -> None:
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('source', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (source,),
    )
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def import_from_yaml(
    tasks_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Bootstrap the shadow DB from the canonical YAML store. Idempotent.

    Reads the resolved ``tasks.yaml`` (+ ``threads.yaml`` sidecar) and rebuilds
    every DB table inside ONE transaction. The YAML is opened read-only and is
    never modified. Returns a summary dict::

        {"db_path", "yaml_path", "tasks", "comments", "edges", "roles",
         "users", "user_names", "notifications", "messages"}
    """
    doc, store_path, snapshot = _load_source(tasks_path)
    threads = _load_threads(store_path)
    resolved_db = resolve_db_path(db_path)
    doc_cards = doc.get("tasks") if isinstance(doc, dict) else None
    card_count = len(doc_cards) if isinstance(doc_cards, list) else 0

    conn = connect(resolved_db)
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary: dict = {
            "db_path": str(resolved_db),
            "yaml_path": str(store_path),
        }
        summary.update(_rebuild_from_doc(conn, doc, threads=threads))
        _stamp_meta(conn, "yaml-import")
        # Record WHICH yaml this mirror reflects, so a read can tell — with one
        # stat(2) and no parse — whether the store has moved on since. Without
        # this the DB looks perfectly healthy while serving a stale photograph.
        stamp_yaml_provenance(conn, store_path, card_count, snapshot=snapshot)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def mirror_doc(doc: dict, db_path: str | Path | None = None) -> dict:
    """S1 DUAL-WRITE: mirror an in-memory doc into the DB. Raises on failure.

    Called from the store's ONE write chokepoint
    (:func:`scitex_cards._model._save_doc_unlocked`) AFTER the canonical YAML write
    has succeeded, while the store lock is still held — so the mirror can never
    interleave with another writer, and needs no lock of its own.

    Takes the doc IN MEMORY on purpose: the caller already parsed it under the
    lock, and re-reading ``tasks.yaml`` from disk to get it back would cost another
    full parse (~1.0-1.7 s, MEASURED on 1,370 cards) inside the critical section.

    Rebuilds the doc-derived tables in ONE transaction and leaves ``messages``
    (owned by the threads sidecar) untouched.

    RAISES on failure, deliberately. The POLICY of what to do about a failed
    mirror — never break the user's write, but never be silent either — belongs
    to the caller (:mod:`scitex_cards._dual_write`), not here. A primitive that
    swallows its own errors cannot be given a policy later.
    """
    resolved_db = resolve_db_path(db_path)
    conn = connect(resolved_db)
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary = _rebuild_from_doc(conn, doc)
        summary["db_path"] = str(resolved_db)
        _stamp_meta(conn, "dual-write")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


__all__ = ["import_from_yaml", "mirror_doc"]

# EOF
