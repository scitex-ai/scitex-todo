#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB → YAML export: the backup/audit rail of ADR-0010.

The operator's ruling (2026-07-16): the DATABASE is the single source of
truth; backup = periodically EXPORT a YAML snapshot *from* the DB and git-
snapshot that export. Git tracks an export, never live data — which is what
retires the "dotfiles working tree IS the live store" merge hazard.

Exactness contract
------------------
Every entity is reconstructed from its VERBATIM ``*_json`` payload
(``tasks.card_json`` — v2; ``users/notifications/messages.record_json`` — v3),
never from typed columns: a column-based rebuild would drop unknown keys and
reorder the rest. A NULL payload means the row predates its payload column and
the DB was never re-imported — the export REFUSES loudly rather than emit a
stripped record.

The columns that legitimately MUTATE in the DB after import (``seen`` on a
notification, ``read`` on a message, ``last_seen`` on a user) are overlaid
onto the payload so a post-cutover export reflects live state, not the
import-time snapshot of those flags.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ._db import open_db
from ._db_payload import card_from_payload

#: (table, mutable-column overlays applied on top of the verbatim payload)
_OVERLAYS: dict[str, tuple[str, ...]] = {
    "users": ("last_seen",),
    "notifications": ("seen",),
    "messages": ("read",),
}

#: columns whose SQL integer form maps back to a YAML bool.
_BOOL_COLS = {"seen", "read"}


class ExportRefused(RuntimeError):
    """A row has no verbatim payload — the DB must be re-imported first."""


def _record(row, table: str) -> dict[str, Any]:
    """Rebuild one record from its verbatim payload + mutable-column overlay."""
    blob = row["record_json"]
    if blob is None:
        raise ExportRefused(
            f"{table} row {row['id']!r} has no record_json payload — this DB "
            "predates schema v3 or was never re-imported. Run "
            "`scitex-cards db import --from-yaml` first; exporting stripped "
            "records is worse than exporting none."
        )
    rec = card_from_payload(blob)
    for col in _OVERLAYS[table]:
        if row[col] is not None:
            rec[col] = bool(row[col]) if col in _BOOL_COLS else row[col]
    return rec


def export_doc(db_path: str | Path | None = None) -> tuple[dict, dict]:
    """Assemble ``({tasks, users, inboxes}, threads)`` from the DB, exactly.

    Tasks come back in document order (``row_order``); inbox and thread
    records in insertion (rowid) order — matching how the YAML lists grew.
    """
    conn = open_db(db_path)
    try:
        tasks: list[dict] = []
        for r in conn.execute(
            "SELECT id, card_json FROM tasks ORDER BY row_order"
        ).fetchall():
            if r["card_json"] is None:
                raise ExportRefused(
                    f"task {r['id']!r} has no card_json payload — run "
                    "`scitex-cards db import --from-yaml` first."
                )
            tasks.append(card_from_payload(r["card_json"]))

        users = [
            _record(r, "users")
            for r in conn.execute(
                "SELECT * FROM users ORDER BY rowid"
            ).fetchall()
        ]

        # Seed from the recipients table first so a DRAINED inbox (a
        # key with zero rows) still appears as an empty list (v4).
        inboxes: dict[str, list[dict]] = {
            r["recipient_id"]: []
            for r in conn.execute(
                "SELECT recipient_id FROM inbox_recipients ORDER BY rowid"
            ).fetchall()
        }
        for r in conn.execute(
            "SELECT * FROM notifications ORDER BY rowid"
        ).fetchall():
            inboxes.setdefault(r["recipient_id"], []).append(
                _record(r, "notifications")
            )

        threads: dict[str, list[dict]] = {}
        for r in conn.execute(
            "SELECT * FROM messages ORDER BY rowid"
        ).fetchall():
            threads.setdefault(r["thread_key"], []).append(
                _record(r, "messages")
            )
    finally:
        conn.close()

    doc: dict[str, Any] = {"tasks": tasks}
    if users:
        doc["users"] = users
    if inboxes:
        doc["inboxes"] = inboxes
    return doc, threads


def _atomic_write(path: Path, text: str) -> None:
    """tmp → flush+fsync → rename; a crash never leaves a torn export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def export_yaml(
    db_path: str | Path | None = None,
    out: str | Path | None = None,
    threads_out: str | Path | None = None,
) -> dict[str, Any]:
    """Export the DB to YAML text files; return a count report.

    ``out`` defaults to ``<db_dir>/export/tasks.yaml``; ``threads_out``
    defaults to ``threads.yaml`` beside it. The report carries the counts so
    a caller (or the snapshot rail) prints what was exported — a silent
    export is a bulk operation with no dry-run trace.
    """
    from ._db import resolve_db_path
    from ._yaml import safe_dump

    doc, threads = export_doc(db_path)

    db = resolve_db_path(db_path)
    out_path = (
        Path(out).expanduser() if out else db.parent / "export" / "tasks.yaml"
    )
    threads_path = (
        Path(threads_out).expanduser()
        if threads_out
        else out_path.parent / "threads.yaml"
    )

    _atomic_write(out_path, safe_dump(doc))
    # The sidecar contract is a top-level ``threads:`` mapping
    # (scitex_cards._threads._load_threads reads exactly that key) — an
    # export must be loadable by the same reader as the live sidecar.
    _atomic_write(threads_path, safe_dump({"threads": threads}))

    return {
        "db": str(db),
        "tasks_yaml": str(out_path),
        "threads_yaml": str(threads_path),
        "tasks": len(doc.get("tasks", [])),
        "users": len(doc.get("users", [])),
        "inbox_recipients": len(doc.get("inboxes", {})),
        "notifications": sum(len(v) for v in doc.get("inboxes", {}).values()),
        "threads": len(threads),
        "messages": sum(len(v) for v in threads.values()),
    }


__all__ = ["ExportRefused", "export_doc", "export_yaml"]

# EOF
