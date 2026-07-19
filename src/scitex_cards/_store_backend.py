#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which store is CANONICAL — the YAML file, or the SQLite DB?

THE ORDER (operator, 2026-07-18): 「db にハードに切り替えてください、ヤムル
ファイルは全てアーカイブしてください」「書き出ししなくていいですよ！！ぜんぶ
db で！」 — switch hard to the DB, archive the YAML, and do NOT keep writing an
export. The DB is the store, not a mirror of one.

WHY THIS IS THE RIGHT END STATE and not merely an instruction followed. While
YAML was canonical and the DB a mirror, the SQLite read path was gated on
``_db_freshness.check_fresh``, which demands EXACT (mtime_ns, size) equality
between the DB's stamp and the YAML on disk. The fleet rewrites that YAML every
few seconds, so the gate could pass only in the gaps between writes — measured
alternating fresh/stale under live load. That race exists BY CONSTRUCTION in a
mirror design; no tuning removes it, because tightening the gate keeps the fast
path off and loosening it risks serving stale cards. Making the DB canonical
deletes the entire failure class: there is nothing left for it to be stale
against.

THE ONE-WAY DOOR, stated plainly. In canonical mode a card that fails to reach
SQLite is GONE — there is no YAML behind it to fall back on. So the write path
must NOT keep the mirror's best-effort, never-raise posture: under
:func:`db_is_canonical` a failed DB write MUST raise and the caller MUST see
it. Swallowing an exception here would silently drop the operator's cards,
which is the single worst thing this package can do. See ``_store_write``.

DEFAULT IS OFF. This flips the source of truth for the fleet's shared memory,
so it is opt-in per process and reversible by unsetting one variable — with a
full archive taken before the flip (~/.scitex/cards/.old/<stamp>/).
"""

from __future__ import annotations

import os

#: Selects the canonical store. ``sqlite`` = the DB IS the store; anything
#: else (default) = the YAML is canonical and the DB is a mirror.
ENV_STORE_BACKEND = "SCITEX_CARDS_STORE_BACKEND"

#: Legacy twin. ``_env_compat`` mirrors SCITEX_CARDS_* onto SCITEX_TODO_* at
#: import, but a process that sets only the OLD name must still be honoured
#: during the transition window — so both are read here rather than trusting
#: the mirror to have run.
ENV_STORE_BACKEND_LEGACY = "SCITEX_TODO_STORE_BACKEND"

BACKEND_SQLITE = "sqlite"


def db_is_canonical() -> bool:
    """True when SQLite is the STORE rather than a mirror of the YAML.

    Reads both env prefixes so it cannot be defeated by the rename being
    half-applied — which is exactly how the fleet's dual-write silently sat
    OFF on three daemons for hours (they carried no scitex env at all, so
    every write they made skipped the mirror and rotted the DB).
    """
    for name in (ENV_STORE_BACKEND, ENV_STORE_BACKEND_LEGACY):
        raw = os.environ.get(name, "")
        if raw.strip().lower() == BACKEND_SQLITE:
            return True
    return False


def write_doc_to_db(doc: dict, store_path) -> dict:
    """Commit ``doc`` to SQLite as the CANONICAL store. RAISES on failure.

    THE INVERSE POSTURE of :func:`_dual_write.mirror_after_save`, and the
    inversion is deliberate. That one swallows every exception because the
    YAML already holds the card, so a mirror hiccup must never turn a
    successful write into a failed one. Here SQLite is the ONLY copy: a
    swallowed exception is a card that vanished while the caller was told it
    saved. So this propagates, and callers must not wrap it in a bare
    ``except``.

    ``store_path`` still identifies WHICH logical store is addressed (and
    therefore stamps provenance), even though nothing is written to that file
    in this mode.
    """
    from ._db import resolve_db_path
    from ._db_mirror import mirror_doc_incremental

    # `mirror_doc_incremental` already raises on failure — no try/except here
    # ON PURPOSE. Adding one could only make this quieter, which is the one
    # direction this function must never move.
    return mirror_doc_incremental(doc, resolve_db_path(None), store_path=store_path)


__all__ = [
    "BACKEND_SQLITE",
    "ENV_STORE_BACKEND",
    "ENV_STORE_BACKEND_LEGACY",
    "db_is_canonical",
    "write_doc_to_db",
]

# EOF
