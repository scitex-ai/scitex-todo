#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Directory-card migration scanner + plan emitter (PR-D Stage 1).

Operator-direct directive (2026-06-13, lead a2a `[operator-driven]`):

  "Todo ではtasksディレクトリの中にディレクトリカードがないとダメですよ？
   直接書くのは厳禁です、エラーで止めてくださいね"

Translation: the canonical card shape is a per-task DIRECTORY at
``<proj>/.scitex/todo/tasks/<card-id>/`` (with ``README.md`` for the
body + ``adr.md`` for decisions per skill 30). Writes that lay a row
directly into the flat ``tasks.yaml`` are FORBIDDEN — the system must
fail loud + name the offending path.

This module is the SCAN side: walk every discovered lane (the
``~/proj/*/.scitex/todo/tasks.yaml`` glob), classify each row, and
emit a machine-readable plan + a human-readable Markdown summary.
NOTHING is written to disk by the scanner — the migrator runs in a
separate verb behind ``-y`` after the plan is operator-approved.

Field-split rules (lead-approved 2026-06-13, refined from operator's
"minimal index" directive):

YAML keeps (metadata, structured graph-shape):
  - id, title (SHORT label, ≤120 chars)
  - status, priority, kind, blocker (closed enums)
  - parent, depends_on, blocks (edges)
  - assignee, agent, collaborators (ownership)
  - project, scope, repo, host
  - pr_url, issue_url
  - deadline / deadlines / scheduled
  - created_at, last_activity
  - comments[] — Gitea-compat activity log (short events; ``text``
    capped at 280 chars per entry)

MOVED to ``tasks/<id>/README.md``:
  - note (long-form body)
  - title prefix overflow (a >120-char title is body content)

MOVED to ``tasks/<id>/adr.md`` (skill 30 — no yaml field today):
  - decision records (convention only; no yaml migration needed)

The scanner classifies each row into one of:

  - ``CANONICAL`` — already minimal-metadata + has ``tasks/<id>/``
  - ``NEEDS_DIR`` — row exists, no ``tasks/<id>/`` sibling
  - ``NEEDS_NOTE_MIGRATE`` — ``note`` field present (body in yaml)
  - ``NEEDS_TITLE_TRIM`` — title > 120 chars
  - ``NEEDS_COMMENT_TRIM`` — any ``comments[].text`` > 280 chars
  - ``EMPTY_ID`` — row missing an id (legacy data; flagged separately)

A single row may carry multiple classifications; the plan emits the
union per row + per-project totals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


# === Tuning knobs ==========================================================

#: Maximum title length kept in tasks.yaml; anything longer counts as
#: body content and migrates to README.md. ≤120 chars = "fits in a
#: column header / chip without truncation."
MAX_TITLE_CHARS = 120

#: Maximum per-comment text length kept in tasks.yaml's comments[]
#: activity log; longer = body, migrates to the per-card README/adr.
MAX_COMMENT_CHARS = 280


# === Row classification ====================================================


@dataclass
class RowPlan:
    """Per-row classification + migration delta the migrator will apply."""
    id: str
    lane_path: Path
    classifications: List[str] = field(default_factory=list)
    note_excerpt: Optional[str] = None  # first 80 chars of `note`
    title_len: int = 0
    long_comment_count: int = 0
    canonical: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lane_path": str(self.lane_path),
            "classifications": list(self.classifications),
            "note_excerpt": self.note_excerpt,
            "title_len": self.title_len,
            "long_comment_count": self.long_comment_count,
            "canonical": self.canonical,
        }


@dataclass
class LanePlan:
    """Per-lane (= per-project) plan rollup."""
    lane_path: Path
    rows: List[RowPlan] = field(default_factory=list)
    total: int = 0
    canonical_count: int = 0
    needs_migration_count: int = 0
    counts_by_kind: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lane_path": str(self.lane_path),
            "total": self.total,
            "canonical_count": self.canonical_count,
            "needs_migration_count": self.needs_migration_count,
            "counts_by_kind": dict(self.counts_by_kind),
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class FleetPlan:
    """Top-level plan emitted by :func:`scan_all_lanes`."""
    lanes: List[LanePlan] = field(default_factory=list)

    def to_dict(self) -> dict:
        total = sum(l.total for l in self.lanes)
        canonical = sum(l.canonical_count for l in self.lanes)
        migrate = sum(l.needs_migration_count for l in self.lanes)
        return {
            "lane_count": len(self.lanes),
            "total_rows": total,
            "canonical_rows": canonical,
            "needs_migration_rows": migrate,
            "lanes": [l.to_dict() for l in self.lanes],
        }


# === The scan ==============================================================


def classify_row(row: dict, lane_path: Path) -> RowPlan:
    """Classify a single row. Pure function (no I/O on the row itself;
    only ``tasks/<id>/`` directory existence is probed under
    ``lane_path.parent / 'tasks' / row.id``)."""
    rid = row.get("id") or ""
    plan = RowPlan(id=str(rid), lane_path=lane_path)
    if not rid:
        plan.classifications.append("EMPTY_ID")
        return plan
    # `note` body in yaml?
    note = row.get("note")
    if isinstance(note, str) and note.strip():
        plan.classifications.append("NEEDS_NOTE_MIGRATE")
        plan.note_excerpt = note[:80] + ("…" if len(note) > 80 else "")
    # Title length?
    title = row.get("title") or ""
    plan.title_len = len(title)
    if plan.title_len > MAX_TITLE_CHARS:
        plan.classifications.append("NEEDS_TITLE_TRIM")
    # Long comments?
    comments = row.get("comments") or []
    if isinstance(comments, list):
        long_n = sum(
            1 for c in comments
            if isinstance(c, dict)
            and isinstance(c.get("text"), str)
            and len(c["text"]) > MAX_COMMENT_CHARS
        )
        plan.long_comment_count = long_n
        if long_n:
            plan.classifications.append("NEEDS_COMMENT_TRIM")
    # tasks/<id>/ directory present?
    tasks_dir = lane_path.parent / "tasks" / rid
    if not tasks_dir.is_dir():
        plan.classifications.append("NEEDS_DIR")
    plan.canonical = not plan.classifications
    return plan


def scan_lane(lane_path: Path) -> LanePlan:
    """Scan one ``tasks.yaml`` and produce a :class:`LanePlan`.

    Malformed YAML → empty plan + log a WARNING. Per-row crash is
    swallowed (the migrator can still skip the row); whole-lane
    abort would mask other findings the operator needs to see.
    """
    from scitex_todo._model import load_tasks
    plan = LanePlan(lane_path=lane_path)
    try:
        rows = load_tasks(lane_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[scitex-todo._migrate] cannot load %s: %s", lane_path, exc,
        )
        return plan
    for row in rows:
        if not isinstance(row, dict):
            continue
        rp = classify_row(row, lane_path)
        plan.rows.append(rp)
    plan.total = len(plan.rows)
    plan.canonical_count = sum(1 for r in plan.rows if r.canonical)
    plan.needs_migration_count = plan.total - plan.canonical_count
    counts: dict = {}
    for r in plan.rows:
        for k in r.classifications:
            counts[k] = counts.get(k, 0) + 1
    plan.counts_by_kind = counts
    return plan


def scan_all_lanes(
    lane_paths: Optional[Iterable[Path]] = None,
) -> FleetPlan:
    """Scan every discovered lane + every project's ``tasks.yaml`` AND
    the global user-scope ``~/.scitex/todo/tasks.yaml``.

    Defaults to :func:`scitex_todo._django.services._discover_lanes` +
    the resolved global store. Pass an explicit iterable for tests
    (the scanner is pure-data and easy to fixture).
    """
    from scitex_todo._paths import resolve_tasks_path
    if lane_paths is None:
        from scitex_todo._django.services import _discover_lanes
        lanes = list(_discover_lanes())
        global_path = resolve_tasks_path(None)
        if global_path.exists() and global_path not in lanes:
            lanes = [global_path] + lanes
    else:
        lanes = list(lane_paths)
    fleet = FleetPlan()
    for lp in lanes:
        fleet.lanes.append(scan_lane(lp))
    return fleet


# === Markdown rendering ====================================================


def render_markdown(fleet: FleetPlan) -> str:
    """Render the plan as a human-readable Markdown report for the
    operator's review pass."""
    top = fleet.to_dict()
    out = [
        "# Directory-card migration plan",
        "",
        f"Lanes scanned: **{top['lane_count']}**",
        f"Total rows: **{top['total_rows']}**",
        f"Already canonical: **{top['canonical_rows']}**",
        f"Need migration: **{top['needs_migration_rows']}**",
        "",
        "## Per-lane summary",
        "",
    ]
    for lane in fleet.lanes:
        out.append(f"### {lane.lane_path}")
        out.append("")
        out.append(
            f"- total: **{lane.total}** "
            f"(canonical {lane.canonical_count}, "
            f"need migration {lane.needs_migration_count})"
        )
        for kind, n in sorted(lane.counts_by_kind.items()):
            out.append(f"- `{kind}`: {n}")
        # Sample 5 non-canonical rows per lane.
        sample = [r for r in lane.rows if not r.canonical][:5]
        if sample:
            out.append("")
            out.append("Sample non-canonical rows:")
            for r in sample:
                kinds = "/".join(r.classifications)
                note_hint = (
                    f" — note='{r.note_excerpt}'" if r.note_excerpt else ""
                )
                out.append(f"- `{r.id}` [{kinds}]{note_hint}")
        out.append("")
    return "\n".join(out)


__all__ = [
    "MAX_TITLE_CHARS",
    "MAX_COMMENT_CHARS",
    "RowPlan",
    "LanePlan",
    "FleetPlan",
    "classify_row",
    "scan_lane",
    "scan_all_lanes",
    "render_markdown",
]
