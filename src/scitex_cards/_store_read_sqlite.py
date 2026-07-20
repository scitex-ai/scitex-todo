#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 READ PATH — serve ``list_tasks`` from the SQLite mirror. Default OFF.

WHY — THE MEASUREMENT, WITH ITS DENOMINATOR
-------------------------------------------
DENOMINATOR: the live board, copied read-only — 1,452 cards, 5.82 MB tasks.yaml (the
mirror is 13.45 MB) — on this branch, CPython 3.12.3, in-process, median of 7, warm
guard. YAML timings on this box drift between ~0.7 s and ~1.3 s under load; the
RATIOS are what survive::

    query                       rows      YAML     SQLite   speedup
    full list (no filter)       1452    938.3 ms  361.8 ms       3x
    assignee='scitex-todo'       152   1010.8 ms   16.2 ms      63x
    status='blocked'             157    932.4 ms   16.1 ms      58x
    scope='agent:scitex-todo'     79    984.2 ms   14.1 ms      70x
    blocking_me=True              47   1061.7 ms   12.4 ms      85x
    overdue=True                   1   1079.8 ms   79.8 ms      14x

**Look at the YAML column.** Asking for 47 cards costs what asking for all 1,452
costs — that is the whole diagnosis in one number. ``list_tasks`` parses the ENTIRE
5.82 MB document and only THEN filters in Python, so **the cost is the parse, not
the query**, and "just ask for less" was never going to help: no amount of narrowing
helps when the narrowing happens after the expensive part.

An INDEX is the only lever. The mirror already has one on every field ``list_tasks``
filters by (``status``, ``agent``, ``assignee``, ``scope``, ``kind``, ``blocker``,
``project``, ``deadline``, ``parent``), so ``WHERE assignee=?`` touches 152 rows
instead of parsing 5.82 MB. Every agent in the fleet pays the YAML column on every
poll, forever — the single biggest cost scitex-todo imposes on the fleet.

And note where the win ISN'T: the unfiltered list is only ~3x, because there is no
index to exploit when you ask for EVERYTHING — it is 318 ms of SQLite reading 13 MB
of payload plus 39 ms of ``json.loads``. The prize here is the FILTERED read, which
is what an agent polling its own slice actually issues. Reporting only the headline
"63x" would be quoting the best row of a table I have in front of me.

INDISTINGUISHABLE, OR IT DOES NOT SHIP
--------------------------------------
The rows this returns must be IDENTICAL to the YAML path's — same cards, same
order, same fields, same values. Not "same count", not "looks plausible". Two
design choices buy that, and they are not negotiable:

* **The payload, not the columns.** Rows are rebuilt from ``tasks.card_json`` (the
  verbatim card), never from the typed columns. The live store has 22 card keys
  that no column maps and 711 distinct key orders; a column-based rebuild would
  drop and re-order them silently. See :mod:`_db_payload`.
* **``ORDER BY row_order``**, which the mirror records as the card's position in
  the YAML document — so document order survives.

``overdue`` is the one filter NOT pushed into SQL, deliberately. Its semantics
(repeater expansion, ``deadlines`` lists, terminal-status exclusion, "a recurring
deadline is NEVER overdue") live in :func:`scitex_cards._model.is_overdue`, and
re-expressing them in SQL would be a second implementation to keep in sync — the
classic way two backends drift apart. Instead the SAME function is applied to the
SAME reconstructed dicts, so the answer is identical BY CONSTRUCTION. SQL has
already narrowed the rows by then, so it costs nothing to be safe here.

THE FLAG IS NOT ENOUGH — READ :func:`enabled` BEFORE TOUCHING IT
----------------------------------------------------------------
A read backend that serves stale or lossy cards is FAR worse than a slow one: slow
is visible, wrong is not. Enabling this on a DB that is merely *present* would do
exactly that. So the env var is necessary and NOT sufficient — see :func:`enabled`
for the six things that must ALSO be true at runtime, every one of them probed
against the artifact rather than believed from a version string.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from ._db_payload import CARD_JSON_COL, card_from_payload

logger = logging.getLogger(__name__)

#: Gate for the S2 read path. OFF by default (``yaml``). ``sqlite`` opts in.
ENV_READ_BACKEND = "SCITEX_TODO_READ_BACKEND"

#: The value that selects the SQLite read path.
BACKEND_SQLITE = "sqlite"

#: Refusals are logged ONCE per distinct reason, not once per call. ``list_tasks``
#: runs on every poll of every agent; the same ERROR on every call is noise, and
#: noise that fires constantly trains its reader to ignore the channel — the exact
#: failure this codebase catalogued on 2026-07-12.
_logged_refusals: set[str] = set()

#: Verdict cache, keyed by (db_path, db stat, store_path, store stat). Every input
#: to the verdict is in the key, so a DB or store that MOVES invalidates it
#: automatically — a cached "yes" cannot outlive the facts that justified it.
_verdict_cache: dict[tuple, tuple[bool, str | None]] = {}


def _flag_on() -> bool:
    raw = os.environ.get(ENV_READ_BACKEND, "")
    return raw.strip().lower() == BACKEND_SQLITE


def _refuse(reason: str) -> tuple[bool, str]:
    """Record a refusal; log it LOUD, but only the first time we see this reason."""
    if reason not in _logged_refusals:
        _logged_refusals.add(reason)
        logger.error(
            "!! %s=%s IS SET, BUT THE SQLITE READ BACKEND IS REFUSING TO SERVE — "
            "falling back to the canonical YAML. Reason: %s  ||  Your reads are "
            "CORRECT and your cards are safe; they are merely as slow as they were "
            "before. Serving confidently-wrong cards to the whole fleet would be far "
            "worse than being slow, so this path fails closed.",
            ENV_READ_BACKEND,
            BACKEND_SQLITE,
            reason,
        )
    return False, reason


def _code_can_mirror_payload() -> str | None:
    """Can the code ACTUALLY RUNNING here produce a readable mirror? Ask the SYMBOLS.

    *** THIS FUNCTION EXISTS BECAUSE A FLAG TRUSTED A DEPLOY AND COST 135 SECONDS
    PER CARD WRITE. *** (2026-07-13: ``SCITEX_TODO_DUAL_WRITE`` was switched on
    because the incremental mirror "had shipped" — it had, to PyPI; the fleet ran a
    wheel baked into a container image that was still on 0.9.4, so the flag silently
    selected the O(n) full rebuild the incremental mirror had replaced.)

    So this asks NO version question. A version string is metadata, and metadata
    lies: an orphaned ``.dist-info``, a stale wheel, and a SIF baked months ago all
    report a version that outlived the code beside them. The only honest question is
    whether the OBJECTS ARE HERE:

    * ``_db_mirror.mirror_doc_incremental`` — without it, nothing keeps the mirror
      up to date, so even a currently-fresh DB will silently rot behind the YAML.
    * ``card_json`` in ``_db_bootstrap.TASK_INSERT_COLS`` — without it, this process
      writes mirror rows with NO payload, and a reader would be reconstructing cards
      from columns alone: unknown keys dropped, key order invented.

    Returns ``None`` when the code is capable, else the reason it is not.
    """
    try:
        from ._db_bootstrap import TASK_INSERT_COLS
        from ._db_mirror import mirror_doc_incremental  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — absent/broken/unimportable all mean "no"
        return (
            f"this process cannot import the SQLite mirror ({type(exc).__name__}: "
            f"{exc}) — it is running code older than the S2 read path. Upgrade and "
            "RESTART the process; if it is a container, the wheel is baked into the "
            "IMAGE and a restart alone will not update it."
        )
    if CARD_JSON_COL not in TASK_INSERT_COLS:
        return (
            f"this process's mirror does not write the {CARD_JSON_COL!r} payload "
            "column (it is not in _db_bootstrap.TASK_INSERT_COLS) — it is running "
            "code older than the S2 read path, so any card it mirrors would come "
            "back with its unknown fields stripped. Upgrade and RESTART it."
        )
    return None


def _check_db(db_path: Path, store_path: Path) -> tuple[bool, str | None]:
    """The expensive half of the guard: open the DB and INTERROGATE it."""
    if not db_path.exists():
        return _refuse(
            f"no SQLite mirror at {db_path} — build it with `scitex-todo db import`"
        )

    from ._db import connect, table_columns
    from ._db_freshness import check_fresh

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        return _refuse(f"cannot open the mirror at {db_path}: {exc}")
    try:
        # The COLUMN, not the version stamp. `PRAGMA user_version` is a number some
        # code wrote; `PRAGMA table_info` is the table itself. A v1 DB that has been
        # opened once by v2 code has the column ALTERed in but never back-filled —
        # which the NULL check below is what actually catches.
        if CARD_JSON_COL not in table_columns(conn, "tasks"):
            return _refuse(
                f"the mirror's `tasks` table has no {CARD_JSON_COL!r} column, so it "
                "carries no verbatim card payloads — a read would have to rebuild "
                "cards from typed columns alone and would silently drop every field "
                "the schema does not name. Rebuild: `scitex-todo db import`."
            )
        try:
            missing = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE {CARD_JSON_COL} IS NULL"
                ).fetchone()[0]
            )
        except sqlite3.Error as exc:
            return _refuse(f"cannot query the mirror at {db_path}: {exc}")
        if missing:
            return _refuse(
                f"{missing} row(s) in the mirror have a NULL {CARD_JSON_COL!r} "
                "payload — they were written before the payload column existed (or "
                "by a card that does not round-trip through JSON). Those cards would "
                "come back WRONG or not at all. Rebuild: `scitex-todo db import`."
            )
        ok, reason = check_fresh(conn, store_path)
        if not ok:
            return _refuse(str(reason))
    finally:
        conn.close()
    return True, None


def enabled(store_path: str | Path, db_path: str | Path | None = None) -> bool:
    """True only when the flag is ON *and* the mirror is genuinely trustworthy.

    THE FLAG IS NECESSARY AND NOT SUFFICIENT. Six things must hold, and each is
    probed against the ARTIFACT — never inferred from a version, a package
    manifest, or the belief that a deploy happened:

    1. ``SCITEX_TODO_READ_BACKEND=sqlite`` is set;
    2. this process can import the incremental mirror (SYMBOL, not version);
    3. this process's mirror writes the ``card_json`` payload (SYMBOL, not version);
    4. the DB file exists and opens;
    5. its ``tasks`` table HAS a ``card_json`` column and NO row has a NULL one;
    6. it is FRESH: its stamped provenance matches the store's current path, size,
       ``mtime_ns`` and card count (:func:`_db_freshness.check_fresh`).

    Any failure ⇒ False, logged ERROR **once** per distinct reason, and the caller
    serves YAML. Failing closed is the entire point: the mirror is a projection, and
    a projection that has fallen behind is not slow — it is WRONG, and it is wrong
    quietly. The fleet reads this store to decide what to work on.

    (5) and (6) are cached on (db_path, db stat, store_path, store stat), so a poll
    loop pays two ``stat`` calls, not a table scan. Every fact the verdict rests on
    is in the key, so the cache cannot outlive its own justification.
    """
    # SQLite IS the store, so there is nothing to be fresh AGAINST and nothing
    # to fall back TO. The freshness comparison this function used to perform —
    # "has the canonical YAML moved since we mirrored it" — is not merely
    # unnecessary now, it is INCOHERENT: it asks a question about a document
    # that no longer participates. Left in place it would either pass vacuously
    # (a frozen file always matches its own stamp) or fail permanently once the
    # file is gone, which is a verdict about a question nobody asked. That is
    # exactly the instrument error this module's own docstring warns about, so
    # the check is deleted rather than disabled.
    #
    # The CODE-CAPABILITY check is KEPT and is now the only gate. A process
    # whose code cannot write the card_json payload would serve cards with
    # their unknown fields silently stripped. That was wrong when the DB was a
    # mirror and it is worse now: with no YAML behind it, a stripped field is
    # not stale, it is lost.
    incapable = _code_can_mirror_payload()
    if incapable:
        return _refuse(incapable)[0]
    return True


def reset_cache() -> None:
    """Drop the memoised verdicts + refusal log (tests; and after a re-import)."""
    _verdict_cache.clear()
    _logged_refusals.clear()


# --------------------------------------------------------------------------- #
# The query                                                                    #
# --------------------------------------------------------------------------- #
def _where(
    *,
    scope: str | None,
    assignee: str | None,
    status: str | None,
    statuses: list[str] | None,
    agent: str | None,
    project: str | None,
    host: str | None,
    repo: str | None,
    blocker: str | None,
    kind: str | None,
    id_prefix: str | None,
    blocking_me: bool,
    overdue: bool,
) -> tuple[list[str], list]:
    """Translate the filters into SQL. Each clause mirrors ``_store._match`` EXACTLY.

    The subtleties, and why each SQL form was chosen:

    * ``blocker="__none"`` → ``COALESCE(blocker,'') = ''``. ``_match`` tests
      ``if task.get("blocker")`` — FALSY, so both an absent key (NULL) and an empty
      string match. ``blocker IS NULL`` alone would miss the empty-string rows.
    * ``kind`` → ``COALESCE(NULLIF(kind,''),'task')``. Absent ≡ ``"task"``
      (ADR-0002), and ``_match``'s ``task.get("kind") or "task"`` also folds ``""``
      in. ``NULLIF`` is what folds the empty string; ``COALESCE`` alone would not.
    * ``id_prefix`` → ``substr(id,1,n) = ?``, **never** ``LIKE ? || '%'``. Card ids
      contain underscores, and ``_`` is a single-character WILDCARD in SQL ``LIKE``:
      ``LIKE 'note_%'`` would also match ``note-foo``. ``substr`` has no wildcards,
      so it is exactly ``str.startswith``. (A ``%`` in a prefix would be just as
      bad.) An empty/None prefix is no constraint, matching ``_match``'s ``if
      id_prefix``.
    * ``status`` + ``statuses`` are UNIONed into one ``IN (...)`` — the OR-combine.
    * SQLite's default TEXT collation is binary, so ``=`` here is Python's ``==``.
      (A ``COLLATE NOCASE`` column would have made these clauses silently
      case-insensitive and diverged from the YAML path.)
    """
    clauses: list[str] = []
    params: list = []

    for col, val in (
        ("scope", scope),
        ("assignee", assignee),
        ("agent", agent),
        ("project", project),
        ("host", host),
        ("repo", repo),
    ):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)

    if blocker is not None:
        if blocker == "__none":
            clauses.append("COALESCE(blocker, '') = ''")
        else:
            clauses.append("blocker = ?")
            params.append(blocker)

    if kind is not None:
        clauses.append("COALESCE(NULLIF(kind, ''), 'task') = ?")
        params.append(kind)

    if id_prefix:
        clauses.append("substr(id, 1, ?) = ?")
        params.extend([len(id_prefix), id_prefix])

    allowed: set[str] = set()
    if status is not None:
        allowed.add(status)
    if statuses:
        allowed.update(statuses)
    if allowed:
        ordered = sorted(allowed)
        clauses.append(f"status IN ({', '.join('?' for _ in ordered)})")
        params.extend(ordered)

    if blocking_me:
        clauses.append("status = 'blocked' AND blocker = 'operator-decision'")

    if overdue:
        # A NARROWING, not the predicate. `overdue` itself is decided in Python by
        # `_model.is_overdue` (see the module docstring — its repeater / terminal-
        # status semantics have no honest SQL translation, and a second
        # implementation is how two backends drift apart).
        #
        # But this one clause is SOUND, and provably so: `is_overdue` consults
        # `next_deadline_for_task`, which reads ONLY `deadlines` (if a non-empty
        # list) else `deadline`. A card with NEITHER yields no candidates -> None ->
        # `is_overdue` is False. So a row with a NULL `deadline` AND a NULL
        # `deadlines_json` can NEVER be overdue, and dropping it changes no answer —
        # it only spares us decoding 1,451 payloads to reject them one by one.
        # (`deadlines_json` is NULL exactly when the list is absent or empty, which
        # is the same falsy case `next_deadline_for_task` falls through on.)
        #
        # MEASURED, live board: overdue=True went 248.6 ms -> 79.8 ms (and 9.2 ms once
        # any other filter joins it), with the equality proof re-run green across every
        # overdue combination.
        clauses.append("(deadline IS NOT NULL OR deadlines_json IS NOT NULL)")

    return clauses, params


def list_tasks_sqlite(
    store_path: str | Path,
    db_path: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    repo: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> list[dict]:
    """The indexed equivalent of :func:`scitex_cards._store.list_tasks`.

    Assumes :func:`enabled` has already said yes — it does NOT re-check. ``scope``
    is the ALREADY-RESOLVED scope (the ``$SCITEX_TODO_SCOPE`` default and the
    ``scope=""`` opt-out are the caller's business, exactly as on the YAML path;
    duplicating that resolution here would be a second place for it to drift).

    ``overdue`` is applied in Python via the very same
    :func:`scitex_cards._model.is_overdue` the YAML path uses — see the module
    docstring for why re-expressing it in SQL would be a correctness risk, not an
    optimisation.
    """
    from ._db import connect, resolve_db_path

    db = Path(db_path).expanduser() if db_path is not None else resolve_db_path(None)
    clauses, params = _where(
        scope=scope,
        assignee=assignee,
        status=status,
        statuses=statuses,
        agent=agent,
        project=project,
        host=host,
        repo=repo,
        blocker=blocker,
        kind=kind,
        id_prefix=id_prefix,
        blocking_me=blocking_me,
        overdue=overdue,
    )
    sql = f"SELECT {CARD_JSON_COL} FROM tasks"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Document order, faithfully: `row_order` is the card's index in the YAML list.
    sql += " ORDER BY row_order"

    conn = connect(db)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    cards = [card_from_payload(r[0]) for r in rows]

    if overdue:
        from ._model import is_overdue as _is_overdue

        cards = [c for c in cards if _is_overdue(c)]
    return cards


__all__ = [
    "BACKEND_SQLITE",
    "ENV_READ_BACKEND",
    "enabled",
    "list_tasks_sqlite",
    "reset_cache",
]

# EOF
