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

THE FULL REBUILD IS GONE FROM THIS PATH — MIND THE DENOMINATOR
--------------------------------------------------------------
The 8.69 s above is the OLD full rebuild, and it is no longer what a card write
pays. The write chokepoint (:func:`scitex_todo._model._save_doc_unlocked`) hands us
the WHOLE doc without saying which card changed, so this mirror used to DELETE and
re-insert every row, every time — O(n), growing with the board (1.24 s in the
morning, 8.69 s by the evening). It now diffs by card hash and touches only what
actually changed: **8.69 s -> 0.199 s**. A typical write changes one card, so it
writes one card.

The full rebuild still exists in :mod:`scitex_todo._db_bootstrap` — the right shape
for ``db import`` and for the re-bootstrap after a mirror failure — and it is ~5x
faster than it was, because ``INSERT OR REPLACE INTO tasks`` turned out to be 86% of
it (see :func:`scitex_todo._db_bootstrap._insert_tasks`; one word of SQL, 42x per
row). But that is the BOOTSTRAP path, NOT the write path. Do not quote the rebuild's
numbers as the cost of a card write: that substitution — a true measurement of one
component, reported against the wrong denominator — is the exact mistake catalogued
above, and it has now been made twice in this file.

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

#: The version-guard refusal is logged ONCE per process, not once per write. A 135 s
#: bug deserves a loud message; the same message on every card write is just noise that
#: teaches the reader to skip the channel.
_refusal_logged = False


def _has_incremental_mirror() -> bool:
    """Can the code ACTUALLY RUNNING honour this flag? Ask for the SYMBOL.

    NOT a version check. A version string is metadata and metadata lies — an orphaned
    ``.dist-info``, a stale wheel, a SIF image baked months ago all report a version
    that outlived the code beside them. This repo has been bitten by exactly that.

    The only honest question is: IS THE FUNCTION HERE? If
    :func:`scitex_todo._db_mirror.mirror_doc_incremental` cannot be imported, then this
    process physically cannot do an incremental mirror, no matter what any string claims.
    """
    try:
        from ._db_mirror import mirror_doc_incremental  # noqa: F401
    except Exception:  # noqa: BLE001 — absent, broken, or unimportable: all mean "no"
        return False
    return True


def enabled() -> bool:
    """True when the S1 mirror is switched on AND this code can actually honour it.

    *** THIS GUARD EXISTS BECAUSE THE FLAG ALONE COST 135 SECONDS PER CARD WRITE. ***

    MEASURED on the live 1,449-card board, in the configuration the fleet was really
    running (2026-07-13)::

        scitex-todo 0.9.4, dual-write ON  : add_task()    = 135.2 s
        scitex-todo 0.9.4, dual-write OFF : delete_task() =   3.8 s     35x

    WHAT HAPPENED: the flag was switched on because the incremental mirror had shipped —
    and it HAD, on PyPI. But the fleet's agents do not run PyPI; they run a wheel BAKED
    INTO A CONTAINER IMAGE, and that image was still on 0.9.4. So the flag did not enable
    the incremental mirror. IT ENABLED THE FULL REBUILD THAT THE INCREMENTAL MIRROR HAD
    REPLACED — an O(n) rewrite of every row on every write, which grows with the board.

    The precondition ("only turn this on once the mirror is incremental") was real,
    agreed, and written down — IN A CONVERSATION BETWEEN TWO AGENTS. A precondition that
    lives only in a message is not a precondition; it is a hope. So it now lives here, in
    the code, where it cannot be forgotten, misremembered, or outrun by a stale deploy:

        A FLAG WHOSE SAFETY DEPENDS ON A CODE VERSION MUST VERIFY THAT CODE AT RUNTIME.
        IT MUST NOT TRUST THAT A DEPLOY HAPPENED.

    So: the env var is necessary but NOT sufficient. If the running code has no
    incremental mirror, the flag is REFUSED — loudly, once — and the write path stays on
    the fast, YAML-only route rather than silently paying 35x. Failing safe here means
    NOT mirroring; a missing mirror is a recoverable inconvenience (``db import`` rebuilds
    it), while a 135-second card write is an outage every agent feels.
    """
    raw = os.environ.get(ENV_DUAL_WRITE, "")
    if raw.strip().lower() not in {"1", "true", "yes", "on"}:
        return False

    if not _has_incremental_mirror():
        global _refusal_logged
        if not _refusal_logged:
            _refusal_logged = True
            logger.error(
                "!! %s IS SET, BUT THIS CODE HAS NO INCREMENTAL MIRROR "
                "(scitex_todo._db_mirror.mirror_doc_incremental is not importable) — "
                "REFUSING TO DUAL-WRITE. Honouring the flag here would fall back to the "
                "OLD FULL REBUILD, which rewrites every row of every table on every card "
                "write: MEASURED AT 135 SECONDS PER WRITE on a 1,449-card board (vs 3.8 s "
                "with the flag off). Your writes are proceeding NORMALLY and your cards "
                "are safe — only the SQLite mirror is skipped, and `scitex-todo db import` "
                "rebuilds it. FIX: upgrade this process to scitex-todo >= 0.9.5 (the "
                "release that made the mirror incremental) and restart it. If you are in a "
                "container, the wheel is baked into the IMAGE — a restart alone will not "
                "update it; the image must be rebuilt.",
                ENV_DUAL_WRITE,
            )
        return False

    return True


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
        #
        # `store_path` is passed so the mirror can stamp WHICH yaml it reflects
        # (path + mtime + size + card count) in the same transaction as the rows.
        # We are called AFTER the canonical write and still under the store lock,
        # so the file on disk is exactly the doc in hand — the one moment at which
        # that stamp is truthful. The S2 read guard refuses an unstamped DB.
        mirror_doc_incremental(doc, resolve_db_path(), store_path=store_path)
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
