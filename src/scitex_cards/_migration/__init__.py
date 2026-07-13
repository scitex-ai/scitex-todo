#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Directory-card migration subpackage (PR-D).

Re-exports the public surface of :mod:`._migrate` so callers (CLI, tests,
ad-hoc scripts) can import every symbol via
``from scitex_cards._migration import …`` without reaching into the
internal module path.

The module was relocated here from ``scitex_cards/_migrate.py`` to keep
the package's flat-file count under the PS-108b §1 threshold (≤15 .py
files at the package root). The migrator is topical enough to deserve
its own subpackage — Stage 1 (scanner) + Stage 2 (apply) already live
in one file and the Stage 3 validator hook will land here as a sibling.
"""

from ._migrate import (
    MAX_COMMENT_CHARS,
    MAX_TITLE_CHARS,
    FleetPlan,
    LaneApplyResult,
    LanePlan,
    RowApplyResult,
    RowPlan,
    apply_all_lanes,
    apply_lane,
    classify_row,
    render_markdown,
    scan_all_lanes,
    scan_lane,
)

__all__ = [
    "MAX_TITLE_CHARS",
    "MAX_COMMENT_CHARS",
    "RowPlan",
    "LanePlan",
    "FleetPlan",
    "RowApplyResult",
    "LaneApplyResult",
    "classify_row",
    "scan_lane",
    "scan_all_lanes",
    "render_markdown",
    "apply_lane",
    "apply_all_lanes",
]
