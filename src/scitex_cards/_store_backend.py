#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite IS the store. There is no other backend and no way to select one.

THE ORDER (operator, 2026-07-20): 「例外を用意しないでください。甘くせずにハード
に切り替えてください。曖昧にするとバグが残ります。他のエージェントも迷ってしまい
ます。唯一の方法だけソースコードに含めてください。」 — provide no exceptions,
switch hard, leave nothing ambiguous, and carry exactly ONE way in the source.

WHAT THIS MODULE USED TO DO, and why that is gone. It exported
``db_is_canonical()``, reading ``$SCITEX_CARDS_STORE_BACKEND`` to decide whether
SQLite was the store or merely a mirror of a YAML file. That switch is deleted,
along with both env names, because a selectable backend is precisely the defect:

    「今の問題はワイエムLとデータベース両方使えてしまってるところで壊れると思う
      んですよ」 — the problem is that BOTH can be used, and THAT is where it breaks.

He is right, and the evidence is on the record rather than in the argument. Every
board wipe in the 2026-07-19/20 sequence needed a SECOND store to be
authoritative-ish. Reconcile means "make identical", and identical includes
deleting rows absent from whatever document is treated as the source — which is
how a 5-row temporary YAML replaced 2,159 live rows. With one store there is no
second document to reconcile TO, so that entire failure class stops being
REACHABLE rather than being guarded against. A guard is a thing that can be
bypassed; an unreachable state cannot.

Flipping the DEFAULT would not have been enough, and this is the part worth
keeping. A default is an opinion about the common case; it leaves the other
world supported, reachable, and reviewed by nobody. Two supported worlds is two
sets of behaviour to reason about at every call site, and the fleet's agents
each resolve their own environment — so "which world am I in?" would be a live
question with a different answer per process. That is the ambiguity the operator
named, and deletion is the only thing that answers it.

THE ONE-WAY DOOR, stated plainly because it is the real cost. A card that fails
to reach SQLite is GONE — there is no YAML behind it to fall back on. So the
write path must NOT take the mirror's best-effort, never-raise posture: a failed
write MUST raise and the caller MUST see it. Swallowing an exception here would
silently drop the operator's cards, which is the single worst thing this package
can do.
"""

from __future__ import annotations


def write_doc_to_db(doc: dict, store_path, *, deleted_ids=None) -> dict:
    """Commit ``doc`` to SQLite, the only store. RAISES on failure.

    ``store_path`` identifies WHICH logical store is addressed, and therefore
    stamps provenance. Nothing is written to that path.

    ``deleted_ids`` are ids a caller intentionally removed (``delete_task``);
    they are forwarded to the mirror, which drops exactly those rows. The mirror
    never infers a delete from a card's absence — only these named ids go.

    OWNERSHIP IS CHECKED FIRST and a mismatch RAISES rather than returning
    quietly. The destination comes from the ambient environment
    (``resolve_db_path(None)``) while ``doc`` comes from the caller, so a
    mispairing is possible on this path and there is no second copy to recover
    from when it happens.

    That is not hypothetical. Both halves were demonstrated on 2026-07-19:
    first the mirror path let a pytest fixture rebuild the live DB
    (2,136 cards -> 21), and after that path was guarded, the canonical path —
    unguarded — let the same suite do it again, harder (2,138 -> 1). Two doors
    into one room; guarding one taught the caller nothing about the other.

    Why RAISE rather than decline: declining is right when the card is already
    durable somewhere else, and wrong when this is the only copy. Silently
    not-writing would report success for a card that was never stored. Both
    outcomes of a mismatch — clobbering the wrong database, or dropping the
    card — are unacceptable, so the caller is told.
    """
    from ._db import resolve_db_path
    from ._db_mirror import mirror_doc_incremental
    from ._dual_write import _db_mirrors_this_store

    db_path = resolve_db_path(None)
    if not _db_mirrors_this_store(db_path, store_path):
        raise RuntimeError(
            f"refusing to write {store_path} into {db_path}: that database is "
            f"the store for a DIFFERENT path, and writing it would replace "
            f"that store's rows with this one's. Point $SCITEX_CARDS_DB at "
            f"this store's own database."
        )

    # `mirror_doc_incremental` already raises on failure — no try/except here
    # ON PURPOSE. Adding one could only make this quieter, which is the one
    # direction this function must never move.
    return mirror_doc_incremental(
        doc, db_path, store_path=store_path, deleted_ids=deleted_ids
    )


__all__ = [
    "write_doc_to_db",
]

# EOF
