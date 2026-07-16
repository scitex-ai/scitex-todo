#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cutover rehearsal: prove yaml → cards.db → yaml is exact, in one call.

The RFC-R4 equivalence gate as a REPEATABLE command instead of a one-off
script. READ-ONLY on the live store: both yaml files are frozen (copied)
first, so a fleet write mid-rehearsal cannot masquerade as an exporter bug —
that race produced two false mismatches on the first live run (2026-07-16).

The verdict compares the DB export against the frozen yaml per section.
``tasks`` are compared through the importer's one documented normalization
(duplicate ids collapse, last occurrence wins); everything else must be
deep-equal, unknown keys and key order included.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from ._db_bootstrap import import_from_yaml
from ._db_export import export_doc
from ._model import load_doc
from ._paths import resolve_tasks_path
from ._threads import THREADS_FILENAME, _load_threads


def rehearse(
    tasks_path: str | Path | None = None,
    workdir: str | Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    """Run one frozen-copy rehearsal; return the verdict report.

    ``workdir`` (default: a fresh temp dir) receives the frozen copies and
    the throwaway ``cards.db``. With ``keep=False`` a passing rehearsal
    removes it; a FAILING one always keeps it, so the evidence — frozen
    inputs plus the DB that disagreed — survives for diagnosis.
    """
    live = resolve_tasks_path(tasks_path)
    live_threads = live.parent / THREADS_FILENAME

    root = Path(workdir).expanduser() if workdir else Path(tempfile.mkdtemp(prefix="cards-rehearse-"))
    root.mkdir(parents=True, exist_ok=True)
    frozen = root / "tasks.yaml"
    shutil.copyfile(live, frozen)
    if live_threads.exists():
        shutil.copyfile(live_threads, root / THREADS_FILENAME)

    t0 = time.time()
    import_from_yaml(tasks_path=frozen, db_path=root / "cards.db")
    t_import = time.time() - t0

    t0 = time.time()
    doc_db, threads_db = export_doc(root / "cards.db")
    t_export = time.time() - t0

    doc_yaml = load_doc(frozen, validate=False)
    threads_yaml = _load_threads(root / THREADS_FILENAME)

    # The importer's one normalization: duplicate ids collapse, LAST wins.
    deduped: dict[Any, dict] = {}
    for card in doc_yaml.get("tasks", []):
        deduped[card.get("id")] = card
    db_by_id = {card.get("id"): card for card in doc_db.get("tasks", [])}
    mismatched = [i for i, card in deduped.items() if db_by_id.get(i) != card]

    sections = {
        "tasks": doc_db.get("tasks", []) == list(deduped.values()),
        "users": doc_db.get("users", []) == doc_yaml.get("users", []),
        "inboxes": doc_db.get("inboxes", {}) == doc_yaml.get("inboxes", {}),
        "threads": threads_db == threads_yaml,
    }
    equal = all(sections.values())

    report: dict[str, Any] = {
        "equal": equal,
        "sections": sections,
        "store": str(live),
        "workdir": str(root),
        "tasks": len(doc_db.get("tasks", [])),
        "tasks_yaml_raw": len(doc_yaml.get("tasks", [])),
        "task_content_mismatches": len(mismatched),
        "mismatch_sample": mismatched[:5],
        "users": len(doc_db.get("users", [])),
        "inbox_recipients": len(doc_db.get("inboxes", {})),
        "threads": len(threads_db),
        "import_s": round(t_import, 2),
        "export_s": round(t_export, 2),
    }

    if equal and not keep:
        shutil.rmtree(root, ignore_errors=True)
        report["workdir"] = None
    return report


__all__ = ["rehearse"]

# EOF
