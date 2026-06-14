#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS + template contract tests for the Time View layout.

Operator-direct ask (TG, relayed by lead a2a ``d0f7a0e3``, 2026-06-14):
add a live raster TIME VIEW as the 5th LAYOUT toggle alongside Graph /
Table / Recent / Calendar.

This module pins the LOAD-BEARING CSS CONTRACT: key selectors are
present + NO hardcoded colors (the dark/light theme flip rides entirely
on the scitex-ui token variables). Mocks-free: open the source files
and grep them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]

_TIMELINE_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "timeline.css"
)

_BOARD_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "board.css"
)

_TIMELINE_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "TimelineView.tsx"
)

_TODOBOARD_TSX = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "TodoBoard.tsx"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — CSS contract: every required selector is present
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-todo-timeline",
        ".stx-todo-timeline__bar",
        ".stx-todo-timeline__title",
        ".stx-todo-timeline__controls",
        ".stx-todo-timeline__select",
        ".stx-todo-timeline__svg",
        ".stx-todo-timeline__lane-label",
        ".stx-todo-timeline__lane-bg",
        ".stx-todo-timeline__bar--completed",
        ".stx-todo-timeline__edge",
        ".stx-todo-timeline__ticktext",
        ".stx-todo-timeline__tickline",
    ],
)
def test_timeline_css_declares_selector(selector: str) -> None:
    """Every load-bearing selector must have at least one rule in
    timeline.css. Catches a rename / accidental drop."""
    css = _read(_TIMELINE_CSS)
    assert selector in css, (
        f"timeline.css missing rule for {selector!r}"
    )


def test_timeline_css_no_hardcoded_colors() -> None:
    """No hex / rgb / named-color literals — everything must ride through
    the scitex-ui token variables so the dark/light flip stays clean."""
    css = _read(_TIMELINE_CSS)
    # Strip comments before scanning so /* ...#ffffff... */ doc colour
    # tokens don't trip the assertion.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Hex colors.
    hex_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", no_comments)
    assert hex_hits == [], f"timeline.css has hex colors: {hex_hits!r}"
    # rgb()/rgba()/hsl()/hsla() literals.
    func_hits = re.findall(
        r"\b(?:rgb|rgba|hsl|hsla)\s*\(", no_comments
    )
    assert func_hits == [], (
        f"timeline.css has color function literals: {func_hits!r}"
    )
    # Common CSS named colors — not exhaustive, but covers the easy
    # mistakes ("red", "blue", etc.).
    named = re.findall(
        r":\s*(red|blue|green|yellow|orange|purple|black|white|gray|grey|"
        r"pink|cyan|magenta)\b",
        no_comments,
        re.IGNORECASE,
    )
    assert named == [], (
        f"timeline.css has named color literals: {named!r}"
    )


def test_timeline_css_imported_by_board_css() -> None:
    """board.css must @import timeline.css so the bundle picks it up."""
    css = _read(_BOARD_CSS)
    assert '@import "./timeline.css";' in css


def test_timeline_css_uses_token_variables() -> None:
    """The stylesheet must rely on the scitex-ui token namespace
    (``--stx-…``) — that's how the dark/light flip propagates."""
    css = _read(_TIMELINE_CSS)
    assert "var(--stx-" in css, (
        "timeline.css should reference at least one --stx-* token "
        "variable so the dark/light flip wires up"
    )


# ============================================================================
# Half B — TSX wiring: the 5th LAYOUT toggle exists + TimelineView mounted
# ============================================================================


def test_timeline_view_is_a_module() -> None:
    """TimelineView.tsx exports the TimelineView component."""
    tsx = _read(_TIMELINE_TSX)
    assert "export function TimelineView(" in tsx


def test_todoboard_wires_timeline_view() -> None:
    """TodoBoard.tsx mounts TimelineView when ``view === 'timeline'`` and
    the toggle button sets view='timeline'."""
    tsx = _read(_TODOBOARD_TSX)
    assert 'import { TimelineView }' in tsx
    assert 'setView("timeline")' in tsx
    assert 'view === "timeline"' in tsx


def test_todoboard_polls_30s_default_window() -> None:
    """The TimelineView component declares a polling cadence that matches
    the other fleet surfaces (30s) — keeps the operator's "what just
    changed" cognitive load uniform."""
    tsx = _read(_TIMELINE_TSX)
    assert "30_000" in tsx or "30000" in tsx
