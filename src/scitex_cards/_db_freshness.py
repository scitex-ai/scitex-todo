#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IS THE MIRROR ACTUALLY CURRENT? — the question an S2 read must never assume.

THE FAILURE THIS PREVENTS
-------------------------
The YAML is canonical, and *anything* may write it: an agent running an older
build, a process with ``SCITEX_TODO_DUAL_WRITE`` unset (the default!), a hand-edit,
a ``git checkout`` of the store. None of those touch the SQLite mirror. So the
mirror can be perfectly well-formed — right schema, right indexes, ``quick_check
ok``, thousands of plausible cards — and still be a photograph of a store that has
since moved on. Every structural check passes. The data is just *old*.

That is the exact disease this codebase spent 2026-07-11/12 digging out of: a
signal that reads healthy and carries no information. A read backend that trusts
"the DB exists and parses" would serve the whole fleet stale cards with total
confidence, and nothing would ever go red.

THE STAMP
---------
So every path that writes the mirror records WHICH YAML IT MIRRORED — the store's
path, its ``mtime_ns``, its size in bytes, and the number of cards in the doc — into
``schema_meta``. A read then re-``stat``s the YAML (microseconds; no parse) and
compares. Differ by one byte, one nanosecond, or one card and the DB is declared
STALE and the read falls back to YAML.

This is deliberately a CONTENT-INDEPENDENT check: it does not need to parse the
YAML to know the mirror is behind, which is the whole point — a freshness check
that had to parse the store would cost exactly what we are trying to avoid.

THE CARD COUNT IS NOT REDUNDANT
-------------------------------
``mirror_doc_incremental`` skips cards with no ``id`` (they cannot be keyed), and
``_dedupe_last_wins`` collapses duplicate ids. Both are LOSSY: the doc has more
cards than the mirror can hold. ``yaml_card_count`` is the doc's raw count and the
guard compares it against ``COUNT(*) FROM tasks``; a mismatch means the mirror
CANNOT reproduce this store's rows, so it is refused. Without this, a store with a
duplicate id would quietly return one card fewer, forever.

WHICH ``stat`` — AND WHY THE ORDER MATTERS
------------------------------------------
Callers pass the stat snapshot explicitly, because taking it at the wrong moment
inverts the safety:

* the BOOTSTRAP (``db import``) stats the YAML **before** parsing it. If the store
  is rewritten mid-import, the stamp holds the pre-read stat, which no longer
  matches the file → the next read calls it STALE. Wrong in the SAFE direction.
* the DUAL-WRITE mirror stats the YAML **after** the write it is mirroring, under
  the store lock — the doc in hand IS the file on disk.

Stat-after-parse in the bootstrap would have stamped a file the DB never saw.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: ``schema_meta`` keys holding the provenance of the mirrored YAML.
KEY_YAML_PATH = "yaml_path"
KEY_YAML_MTIME_NS = "yaml_mtime_ns"
KEY_YAML_SIZE = "yaml_size"
KEY_YAML_CARD_COUNT = "yaml_card_count"

_KEYS = (KEY_YAML_PATH, KEY_YAML_MTIME_NS, KEY_YAML_SIZE, KEY_YAML_CARD_COUNT)


def canonical_path(store_path: str | Path) -> str:
    """The ONE spelling of a store path that both the stamp and the check must use.

    ``resolve()``, not just ``expanduser()``. The stamp and the check can be made by
    different processes with different working directories — ``db import ./tasks.yaml``
    stamps a RELATIVE path, and a later reader resolving the same file absolutely then
    sees "the DB mirrors a DIFFERENT store" and refuses a perfectly good mirror.

    (That is not hypothetical: the first benchmark run after this guard landed refused
    itself for exactly this reason. It failed SAFE — fell back to YAML, correct but
    slow — which is the right direction to be wrong in, and is also why it would have
    been easy to never notice. Comparing paths means comparing them CANONICALLY.)
    """
    return str(Path(store_path).expanduser().resolve())


def stat_snapshot(store_path: str | Path) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` of the store, or ``None`` if it does not exist."""
    try:
        st = os.stat(str(store_path))
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def stamp_yaml_provenance(
    conn: sqlite3.Connection,
    store_path: str | Path,
    card_count: int,
    *,
    snapshot: tuple[int, int] | None = None,
) -> None:
    """Record WHICH YAML this mirror reflects. Call inside the mirror's txn.

    ``snapshot`` is the ``(mtime_ns, size)`` the caller captured at the correct
    moment (see the module docstring); omitted, it is taken now.

    A store path with NO FILE still gets stamped, with a zeroed snapshot. That
    is not a degraded case, it is the NORMAL one under DB-canonical mode: the
    store is a logical identity and no YAML exists behind it. Returning early
    here used to leave the PREVIOUS stamp in place, so a restore silently kept
    claiming to be whatever the DB was last built from — which is exactly the
    identity confusion the ownership guards then punish. Zeroed mtime/size are
    harmless because the freshness comparison they feed only runs in mirror
    mode, where the file exists by definition.
    """
    snap = snapshot if snapshot is not None else stat_snapshot(store_path)
    if snap is None:
        snap = (0, 0)
    mtime_ns, size = snap
    rows = {
        KEY_YAML_PATH: canonical_path(store_path),
        KEY_YAML_MTIME_NS: str(mtime_ns),
        KEY_YAML_SIZE: str(size),
        KEY_YAML_CARD_COUNT: str(card_count),
    }
    conn.executemany(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        list(rows.items()),
    )


def read_provenance(conn: sqlite3.Connection) -> dict[str, str]:
    """The stamped provenance rows (missing keys simply absent)."""
    placeholders = ", ".join("?" for _ in _KEYS)
    rows = conn.execute(
        f"SELECT key, value FROM schema_meta WHERE key IN ({placeholders})",
        _KEYS,
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def check_fresh(
    conn: sqlite3.Connection, store_path: str | Path
) -> tuple[bool, str | None]:
    """Does this DB mirror the CURRENT ``store_path``? ``(ok, reason_if_not)``.

    Cheap by design: one ``stat`` + one ``COUNT(*)``, no YAML parse. Every failure
    mode returns a reason a human can act on — a guard that says only "no" teaches
    its reader to stop asking.
    """
    prov = read_provenance(conn)
    missing = [k for k in _KEYS if k not in prov]
    if missing:
        return False, (
            "the DB carries NO YAML-provenance stamp "
            f"(missing {', '.join(missing)}) — it predates the S2 read path, or was "
            "written by code that cannot stamp it. It may mirror any store, or none. "
            "Rebuild it: `scitex-todo db import`."
        )

    resolved = canonical_path(store_path)
    if prov[KEY_YAML_PATH] != resolved:
        return False, (
            f"the DB mirrors a DIFFERENT store ({prov[KEY_YAML_PATH]!r}) than the one "
            f"being read ({resolved!r}). Rebuild it against this store: "
            "`scitex-todo db import`."
        )

    snap = stat_snapshot(resolved)
    if snap is None:
        return False, f"the canonical store {resolved!r} does not exist"
    mtime_ns, size = snap
    if str(mtime_ns) != prov[KEY_YAML_MTIME_NS] or str(size) != prov[KEY_YAML_SIZE]:
        return False, (
            "the DB is STALE — the canonical YAML has been written since it was "
            f"mirrored (stamped mtime_ns={prov[KEY_YAML_MTIME_NS]} size="
            f"{prov[KEY_YAML_SIZE]}; on disk now mtime_ns={mtime_ns} size={size}). "
            "Something wrote the store without mirroring it — most likely a process "
            f"with the dual-write mirror OFF. Enable it, or re-run "
            "`scitex-todo db import`."
        )

    db_rows = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
    stamped = int(prov[KEY_YAML_CARD_COUNT])
    if db_rows != stamped:
        return False, (
            f"the mirror is LOSSY for this store: the doc had {stamped} cards but "
            f"only {db_rows} rows reached the `tasks` table. Cards with no `id`, or "
            "duplicate ids, cannot be mirrored — so a SQLite read would silently "
            "return fewer cards than the YAML. Fix the store's ids, then "
            "`scitex-todo db import`."
        )
    return True, None


__all__ = [
    "KEY_YAML_CARD_COUNT",
    "KEY_YAML_MTIME_NS",
    "KEY_YAML_PATH",
    "KEY_YAML_SIZE",
    "check_fresh",
    "read_provenance",
    "stamp_yaml_provenance",
    "stat_snapshot",
]

# EOF
