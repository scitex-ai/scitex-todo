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
from ._db_cards import (  # re-exported: _db_mirror imports these from here
    TASK_INSERT_COLS,  # noqa: F401
    _dedupe_last_wins,  # noqa: F401
    _insert_comments,  # noqa: F401
    _insert_edges,  # noqa: F401
    _insert_roles,  # noqa: F401
    _insert_tasks,
)
from ._db_freshness import stamp_yaml_provenance
from ._db_sections import (  # re-exported: _db_mirror imports these from here
    _gen_id,  # noqa: F401
    _insert_messages,
    _insert_notifications,
    _insert_users,
)
from ._paths import resolve_tasks_path

logger = logging.getLogger(__name__)

#: Data tables cleared before a rebuild, child-before-parent so the explicit
#: deletes never fight the FK order (cascade would also cover the children).
_CLEAR_ORDER: tuple[str, ...] = (
    "task_comments",
    "task_edges",
    "task_roles",
    "tasks",
    "user_names",
    "users",
    "inbox_recipients",
    "notifications",
    "messages",
)


def _load_source(
    tasks_path: str | Path | None,
) -> tuple[dict, Path, tuple[int, int] | None]:
    """Load the YAML doc (tasks/users/inboxes) + the store path + its stat snapshot.

    Parses the file directly (no validation gate — a bootstrap must ingest
    whatever is on disk). A missing store yields an empty doc so ``db import``
    on a fresh install is a no-op, not a crash.

    The stat is taken BEFORE the parse, on purpose. If another writer rewrites the
    store while we are reading it, the snapshot we stamp is the pre-read one, which
    will no longer match the file on disk — so the next read declares the mirror
    STALE and falls back to YAML. Stat-AFTER-parse would instead stamp a version of
    the file the DB never saw, and the mirror would look fresh while being wrong.

    READS THE FILE, NOT THE BACKEND. This used to call ``_model.load_doc``, which
    ROUTES: under ``db_is_canonical()`` it ignores the path it is handed and returns
    the canonical DB's own contents. That turned this function — whose entire
    contract is "read THIS yaml" — into "read the destination", and broke the only
    documented recovery command in BOTH of the states you are ever in when
    recovering:

      * DB present -> the restore read the DB, wrote it back into itself, and
        reported a summary indistinguishable from success. A no-op that says it
        worked is worse than a crash: the 2026-07-19 recovery had to be done by
        hand with ``mirror_doc_incremental`` because ``db import --from-yaml``
        silently restored nothing.
      * DB absent -> the canonical reader raised "store does not exist" and told
        the operator to bootstrap with ``db import --from-yaml``. This command.

    A bootstrap must never consult the thing it is bootstrapping, so the YAML is
    parsed directly here and the backend has no say in it.
    """
    from ._db_freshness import stat_snapshot
    from ._yaml import safe_load

    path = resolve_tasks_path(tasks_path)
    if not path.exists():
        return {}, path, None
    snapshot = stat_snapshot(path)
    with path.open(encoding="utf-8") as handle:
        doc = safe_load(handle) or {}
    return (doc if isinstance(doc, dict) else {}), path, snapshot


def _load_threads(store_path: Path) -> dict[str, list[dict]]:
    """Load the ``threads.yaml`` sidecar map (absent → empty)."""
    from . import _threads

    tpath = _threads.threads_path(store_path)
    return _threads._load_threads(tpath)


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
    as_store: str | Path | None = None,
) -> dict:
    """Bootstrap the shadow DB from the canonical YAML store. Idempotent.

    Reads the resolved ``tasks.yaml`` (+ ``threads.yaml`` sidecar) and rebuilds
    every DB table inside ONE transaction. The YAML is opened read-only and is
    never modified. Returns a summary dict::

        {"db_path", "yaml_path", "tasks", "comments", "edges", "roles",
         "users", "user_names", "notifications", "messages"}

    ``as_store`` separates WHERE THE DATA CAME FROM from WHAT THIS DB IS.

    The provenance stamp is the DB's IDENTITY — the ownership guards in
    ``_dual_write`` / ``_store_backend`` refuse a write whose store does not
    match it. By default the stamp names the imported file, which is right for
    a bootstrap and WRONG for a RESTORE: recovering from
    ``snapshots/tasks.yaml`` re-labels the live database as the snapshot's, and
    every subsequent normal write is then correctly-but-uselessly refused.

    That happened during the 2026-07-19 recovery and was patched by hand with an
    UPDATE on ``schema_meta`` — a sharp edge that should not need a human. Pass
    ``as_store=<the store this DB serves>`` to keep the identity while taking
    the data from anywhere.
    """
    doc, store_path, snapshot = _load_source(tasks_path)
    threads = _load_threads(store_path)
    resolved_db = resolve_db_path(db_path)

    # AN AMBIENT DESTINATION MAY NOT BE REBUILT FROM A FOREIGN STORE.
    #
    # This function DELETEs every data table and re-stamps the DB's identity as
    # the file it imported. Both are correct for the bootstrap it was written
    # for, and catastrophic when the source is unrelated to the destination —
    # which is exactly what happens when a caller isolates its YAML and forgets
    # the DB, because `db_path=None` then resolves the LIVE database out of the
    # ambient environment while `tasks_path` points at a fixture.
    #
    # MEASURED, twice, on the live fleet board (2026-07-19 and 2026-07-20):
    # ~2,150 real cards replaced by a handful of test fixtures.
    #
    # The re-stamp is the half that made it recur. Once the live DB is labelled
    # for the fixture's path, `_db_mirrors_this_store` answers True for that
    # store, so the ownership guard on the ORDINARY write path — which had been
    # refusing those writes correctly — is DISARMED for every write that
    # follows. A guard whose credential the guarded operation can rewrite is not
    # a guard.
    #
    # Both legitimate callers already say which DB they mean, so neither is
    # affected: a RESTORE passes `as_store=` (taking data from a snapshot for a
    # store it names), and a test pairing a tmp store with a tmp DB passes
    # `db_path=`. Only the accident is silent, so only the accident is refused.
    if db_path is None and as_store is None:
        from ._dual_write import _db_mirrors_this_store

        if not _db_mirrors_this_store(resolved_db, store_path):
            raise RuntimeError(
                f"REFUSING to rebuild {resolved_db} from {store_path}: that "
                f"database is the store for a DIFFERENT path, and this import "
                f"DELETES every row and re-stamps the database as this source's."
                f" Nothing here named that database — it came from the ambient "
                f"environment ($SCITEX_CARDS_DB, or the user default), which is "
                f"how a test that isolated its YAML but not its DB replaced the "
                f"live fleet board with its fixtures. If you are RESTORING, name "
                f"the store this database serves: `--as-store <path>` keeps that "
                f"identity while taking the data from here. If you meant a "
                f"different database, name it: `--db <path>`."
            )

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
        # Stamp the DB's IDENTITY. `as_store` lets a RESTORE keep the identity
        # of the store it serves while taking data from a snapshot/backup —
        # without it, recovering re-labels the live DB as the snapshot's and the
        # ownership guards then refuse every ordinary write.
        stamp_target = Path(as_store).expanduser() if as_store else store_path
        stamp_snapshot = snapshot if as_store is None else None
        stamp_yaml_provenance(conn, stamp_target, card_count, snapshot=stamp_snapshot)
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
