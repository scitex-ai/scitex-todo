#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 DUAL-WRITE — mirror every card write into SQLite, YAML still canonical.

WHY THIS EXISTS — THE NUMBERS (measured end-to-end, live store, 2026-07-13)
--------------------------------------------------------------------------
    ONE uncontended card write : 16.31 s   <- what the operator actually waits on
        of which, this mirror  :  8.69 s   <- MORE THAN HALF
    ONE SQLite row update      :  4.71 ms  <- what S2 buys

**A card write takes SIXTEEN SECONDS while holding a fleet-wide lock**, and a
16-second critical section serialises every other writer: two agents means the
second waits 32 s, ten means the last waits 160 s. A single comment waiting ~4
minutes (2026-07-11) is exactly what that predicts. The lock is not at fault — the
lock is correct. What we DO while holding it is at fault.

So this migration is not a performance nicety. The store is already broken, and
SQLite is the repair: it collapses the CRITICAL SECTION, which is what kills the
convoy. It was never about the YAML serialiser (that is ~1.7 s, about 10%).

WHAT THE EARLIER VERSION OF THIS DOCSTRING GOT WRONG — READ THIS BEFORE TRUSTING A NUMBER
-----------------------------------------------------------------------------------------
It said: "full YAML rewrite 11,176 ms; the mirror's full rebuild is NINE TIMES
FASTER than the YAML rewrite beside it; dual-write costs +11%; it is effectively
free." **Every one of those numbers was real, and the conclusion was still wrong.**

  * The 11,176 ms measured ``save_tasks`` IN ISOLATION — not the write path a card
    actually takes. It was a true measurement of one COMPONENT, quoted as the cost
    of the SYSTEM.
  * "+11%, effectively free" used a CONTENDED denominator (105 s, taken while the
    measurer's own writes were draining). Against the real 16.3 s write, the mirror
    is 8.7 s — it MORE THAN DOUBLES a card write. Not free: the largest single
    item in it.

The discipline that would have caught both: MEASURE THE PATH THE USER IS WAITING
ON, END TO END, AND STATE THE DENOMINATOR.

THE FULL REBUILD IS STILL O(n), AND STILL THE NEXT THING TO FIX
--------------------------------------------------------------
The write chokepoint (:func:`scitex_todo._model._save_doc_unlocked`) receives the
WHOLE doc and does not know which card changed, so this mirror rebuilds every row
on every write. That rebuild is now ~1.4 s (it was ~7.3 s until the ``INSERT OR
REPLACE`` blunder documented in :func:`scitex_todo._db_bootstrap._insert_tasks` was
removed — a 5x cut for one word of SQL), but O(n) still GROWS with the board. The
mirror must become INCREMENTAL — upsert the one changed card — and that is tracked
as work in its own right, ahead of the S2 read-cutover.

THE THREE RULES THIS MODULE ENFORCES
------------------------------------
1. **YAML STAYS CANONICAL.** The mirror is a different file and cannot corrupt it.
   A mirror failure must NEVER fail the user's write — by the time we run, their
   card is already safely on disk. Raising here would turn a cosmetic problem into
   data loss.

2. **BUT IT MUST NEVER BE SILENT.** A mirror that fails quietly lets the DB rot out
   of sync, and S2 would then cut over to a store that is confidently wrong. That
   is the exact disease this codebase spent 2026-07-11/12 digging out of: a signal
   that reads healthy and carries no information. So every failure is logged LOUD,
   counted, and surfaced in ``scitex-todo health``.

3. **IT MUST BE KILLABLE WITHOUT A RELEASE.** ``SCITEX_TODO_DUAL_WRITE`` gates it.
   Default OFF while S1 is proven under real traffic; flipped on per-agent first.
   A store this critical does not get a flag day.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Gate for the S1 mirror. OFF by default: this touches the write path of the
#: fleet's critical store, so it is proven on one agent under real traffic before
#: it becomes the default for everyone. "1"/"true"/"yes"/"on" enable it.
ENV_DUAL_WRITE = "SCITEX_TODO_DUAL_WRITE"

#: Process-local failure counter. Read by ``health`` so a silently-rotting mirror
#: cannot hide: a nonzero count means the DB has DIVERGED from the YAML and S2
#: must not cut over until it is explained.
_failures: list[str] = []


def enabled() -> bool:
    """True when the S1 mirror is switched on for this process."""
    raw = os.environ.get(ENV_DUAL_WRITE, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def failure_count() -> int:
    """How many mirror writes have failed in this process."""
    return len(_failures)


def failures() -> list[str]:
    """The recorded mirror failures (most recent last)."""
    return list(_failures)


def reset_failures() -> None:
    """Clear the counter (tests; and after a successful re-bootstrap)."""
    _failures.clear()


def mirror_after_save(doc: dict, store_path: str | Path) -> bool:
    """Mirror ``doc`` into the shadow DB. NEVER raises. Returns True on success.

    Called from the store's ONE write chokepoint, AFTER the canonical YAML write
    has succeeded and while the store lock is STILL HELD — so the mirror cannot
    interleave with another writer and needs no lock of its own.

    A failure here is NOT the user's problem: their card is already durably on
    disk. So we swallow the exception rather than turning a mirror hiccup into a
    failed card write.

    But we do NOT swallow it QUIETLY. It is logged at ERROR with the exception,
    counted, and exposed through ``health`` — because a mirror that fails in
    silence lets the DB rot out of sync while every check reports green, and S2
    would then cut the fleet over to a store that is confidently wrong.
    """
    if not enabled():
        return False

    try:
        from ._db import resolve_db_path
        from ._db_mirror import mirror_doc_incremental

        # INCREMENTAL: touch only the cards that actually changed.
        #
        # This used to call `_db_bootstrap.mirror_doc`, which DELETEs and
        # re-inserts every table on every write. MEASURED on the live board:
        # that full rebuild was 8.69 s of a 16.31 s card write — MORE THAN HALF.
        # It also grows with the board (1.24 s in the morning, 8.69 s by the
        # evening), and because it runs inside the store lock it doubled the
        # critical section, and so the convoy, for every other writer.
        #
        # A typical write changes ONE card. Now it writes one card.
        mirror_doc_incremental(doc, resolve_db_path())
        return True
    except Exception as exc:  # noqa: BLE001 - a mirror must never break the write
        msg = f"{type(exc).__name__}: {exc}"
        _failures.append(msg)
        logger.error(
            "!! DUAL-WRITE MIRROR FAILED (%d failure(s) this process) — the YAML "
            "write SUCCEEDED and your card is safe, but the SQLite mirror is now "
            "OUT OF SYNC with it: %s. The DB must be re-bootstrapped "
            "(`scitex-todo db import`) before S2 cutover; do NOT trust it until "
            "then. Store: %s",
            len(_failures),
            msg,
            store_path,
        )
        return False


def check_mirror_healthy() -> dict[str, object]:
    """Health-doctor check: has the mirror stayed in sync with the canonical YAML?

    ``ok`` is False as soon as ONE mirror write has failed — because a single
    failure means the DB no longer matches the YAML, and there is no partial
    credit for a store that is only mostly right.
    """
    if not enabled():
        return {
            "ok": True,
            "detail": f"dual-write mirror is OFF ({ENV_DUAL_WRITE} unset)",
            "hint": None,
        }
    n = failure_count()
    if n == 0:
        return {
            "ok": True,
            "detail": "dual-write mirror ON; every write mirrored successfully",
            "hint": None,
        }
    return {
        "ok": False,
        "detail": (
            f"dual-write mirror ON but {n} write(s) FAILED to mirror — the SQLite "
            f"DB has DIVERGED from the canonical YAML. Last: {_failures[-1]}"
        ),
        "hint": (
            "Your cards are safe (the YAML write is canonical and succeeded). But "
            "the DB is now unreliable and MUST NOT be cut over to. Re-bootstrap it "
            "from the YAML: `scitex-todo db import`. Then investigate why the "
            "mirror failed — a mirror that fails under real traffic is exactly the "
            "thing S1 exists to discover BEFORE S2 trusts the DB."
        ),
    }
