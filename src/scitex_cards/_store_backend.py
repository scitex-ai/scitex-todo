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


def write_doc_to_db(doc: dict, store_path) -> dict:
    """Commit ``doc`` to SQLite, the only store. RAISES on failure.

    ``store_path`` is carried through to stamp provenance. Nothing is written
    to that path.

    THE YAML-PATH OWNERSHIP CHECK USED TO GUARD THIS FUNCTION AND IS DELETED.
    It asked ``_db_mirrors_this_store(db_path, store_path)`` — does this
    database's stamp name the same YAML file the caller is addressing.

    It was a sound check and it is going anyway, so the reason must be exact:

    It never caught the dangerous case. If the environment pointed at a
    DIFFERENT database, ``resolve_db_path()`` would open that one, whose stamp
    matches its own resolver, and the check would stay silent. Writing to the
    wrong board is invisible from inside the code — the real barrier for the
    test suite lives in the harness (``tests/conftest.py``), above the code
    under test, precisely because no in-code guard can bound this.

    What it DID catch is two processes disagreeing about the NAME of the one
    database they are both correctly using. With a YAML path as the store's
    identity, ``~/.scitex/cards/tasks.yaml`` and ``~/.scitex/todo/tasks.yaml``
    were two names for one board and each writer re-stamped the other out.
    SQLite is now the only store, so identity IS the database path: one name,
    the file itself, nothing left to disagree about. The question is removed,
    not the answer.

    WHAT STILL RAISES, because destruction is a different question from
    identity: a missing database (``_store._read_canonical_db_or_raise``), the
    ambient-store-creation guard, and the shrink floor. And this function still
    propagates rather than swallowing — declining is right when the card is
    durable elsewhere and wrong when this is the only copy. Silently
    not-writing would report success for a card that was never stored.
    """
    from ._db import resolve_db_path
    from ._db_mirror import mirror_doc_incremental

    db_path = resolve_db_path(None)

    # `mirror_doc_incremental` already raises on failure — no try/except here
    # ON PURPOSE. Adding one could only make this quieter, which is the one
    # direction this function must never move.
    return mirror_doc_incremental(doc, db_path, store_path=store_path)


__all__ = [
    "write_doc_to_db",
]

# EOF
