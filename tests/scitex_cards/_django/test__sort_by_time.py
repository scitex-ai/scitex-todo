#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the v3 board's sort-by-time + group-by-time controls.

Operator request (「時間でのビュー」 — relayed by lead a2a ``ff1441d7``,
2026-06-14): the home board (``/`` server-rendered v3) had NO time-based
view. This PR adds two filterbar controls inside the existing
``.stx-todo-filterbar__group--view`` group:

  1. Sort dropdown #f-sort — extends the existing options with
     ``created_at`` and ``completed_at``. ``last_activity`` is
     re-pointed at the new numeric-key comparator so missing values
     sort to the bottom.
  2. Group-by-time checkbox #stx-toggle-group-by-time — when ON,
     ``_renderColumnHtml`` walks each column's cards and inserts
     collapsible bucket headers (TODAY / THIS WEEK / THIS MONTH /
     OLDER) keyed on ``last_activity``.

Contract covered (no mocks, STX-NM/PA-306):
  * Pure ``timeBucketForCard`` classifier — boundary cases at 24 h /
    7 d / 30 d, missing/unparseable timestamps, future timestamps.
  * Pure ``timeSortKey`` helper — returns the right epoch key for
    each sort mode + falls back through ``_log_meta.completed_at``.
  * Template contract — the Sort dropdown carries the 3 time options;
    the Group-by-time checkbox is mounted inside the VIEW group;
    08-time-grouping.css is wired into the head.
  * CSS contract — required selectors exist; no hardcoded colors
    outside of ``var(--token, #fallback)`` slots; braces balance.

The JS helpers are pure (no DOM). We mirror them in an inline JS
RUNTIME and exercise them via ``node`` — same pattern as
``test_calendar_date.py`` (PR #174) and ``test_table_filter.py``
(PR #171).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]

_BOARD_V3_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)

_TIME_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "08-time-grouping.css"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


# ── Inline JS mirror of the runtime body ─────────────────────────────────
# Mirror the implementation in board_v3.html. The static-source assertions
# below pin the canonical function names so a rename trips CI here too.
JS_RUNTIME = textwrap.dedent(
    """
    const TIME_BUCKETS = ["today", "week", "month", "older"];

    function timeBucketForCard(task, nowMs) {
      const ref = (typeof nowMs === "number" && isFinite(nowMs))
        ? nowMs : Date.now();
      const ts = task && task.last_activity;
      if (!ts) return "older";
      const parsed = Date.parse(ts);
      if (isNaN(parsed)) return "older";
      const ageMs = ref - parsed;
      const ONE_DAY = 86400000;
      const ONE_WEEK = 7 * ONE_DAY;
      const ONE_MONTH = 30 * ONE_DAY;
      if (ageMs <= ONE_DAY) return "today";
      if (ageMs <= ONE_WEEK) return "week";
      if (ageMs <= ONE_MONTH) return "month";
      return "older";
    }

    function timeSortKey(task, mode) {
      if (!task) return 0;
      let raw = null;
      if (mode === "last_activity") raw = task.last_activity;
      else if (mode === "created_at") raw = task.created_at;
      else if (mode === "completed_at") {
        raw = (task._log_meta && task._log_meta.completed_at)
          || task.completed_at
          || null;
      }
      if (!raw) return 0;
      const ms = Date.parse(raw);
      return isNaN(ms) ? 0 : ms;
    }
    """
).strip()


def _run(snippet: str) -> dict:
    """Run an inline JS snippet against the mirrored runtime and return the
    JSON-decoded result of ``console.log(JSON.stringify(...))``."""
    script = JS_RUNTIME + "\n" + snippet
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# ============================================================================
# timeBucketForCard — classifier (TODAY / WEEK / MONTH / OLDER)
# ============================================================================

# A fixed `nowMs` so the boundary tests are deterministic regardless of
# CI clock. 2026-06-14T12:00:00Z — the date in the operator's spec.
_FIXED_NOW_MS = 1781438400000  # `new Date('2026-06-14T12:00:00Z').getTime()`


def test_bucket_today_for_recent_activity() -> None:
    """Activity within the last 24 h lands in TODAY — the operator's
    "what changed since yesterday" question."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-14T06:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "today"}


def test_bucket_week_for_4_days_old() -> None:
    """4 days old → THIS WEEK (between 24 h and 7 d)."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-10T12:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "week"}


def test_bucket_month_for_14_days_old() -> None:
    """14 days old → THIS MONTH (between 7 d and 30 d)."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-05-31T12:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "month"}


def test_bucket_older_for_60_days_old() -> None:
    """60 days old → OLDER (> 30 d)."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-04-14T12:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "older"}


def test_bucket_boundary_exactly_24h_is_today() -> None:
    """Exactly 24 h old must land in TODAY (the inclusive boundary —
    operator expectation: "yesterday at this time still counts")."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-13T12:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "today"}


def test_bucket_boundary_just_past_24h_is_week() -> None:
    """1 minute past 24 h falls into THIS WEEK — the next bucket."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-13T11:59:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "week"}


def test_bucket_boundary_exactly_7d_is_week() -> None:
    """Exactly 7 d old must still land in THIS WEEK (inclusive
    upper edge of the bucket)."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-07T12:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "week"}


def test_bucket_boundary_just_past_7d_is_month() -> None:
    """1 minute past 7 d → THIS MONTH bucket."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-07T11:59:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "month"}


def test_bucket_missing_last_activity_is_older() -> None:
    """A task with no ``last_activity`` sinks to OLDER so the operator
    sees it at the bottom of the bucket stack, not in TODAY."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "older"}


def test_bucket_unparseable_last_activity_is_older() -> None:
    """A garbage ``last_activity`` value also sinks to OLDER — graceful
    degrade for legacy snapshots."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: 'not-a-date'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "older"}


def test_bucket_future_last_activity_is_today() -> None:
    """A timestamp slightly in the future (clock drift) still reads as
    TODAY — ``ageMs`` is negative which is ≤ 24 h."""
    # Arrange
    # Act
    out = _run(
        f"const ref = {_FIXED_NOW_MS};\n"
        "const t = {id: 'a', last_activity: '2026-06-14T18:00:00Z'};\n"
        "console.log(JSON.stringify({b: timeBucketForCard(t, ref)}));"
    )
    # Assert
    assert out == {"b": "today"}


# ============================================================================
# timeSortKey — sort-key helper
# ============================================================================


_EPOCH_MS_2026_06_14 = 1781395200000  # new Date('2026-06-14T00:00:00Z').getTime()
_EPOCH_MS_2026_06_13 = 1781308800000  # new Date('2026-06-13T00:00:00Z').getTime()


def test_sort_key_last_activity_returns_epoch_ms() -> None:
    """``last_activity`` mode returns the parsed epoch ms of the
    timestamp — drives the descending Newest-first comparator."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', last_activity: '2026-06-14T00:00:00Z'};\n"
        "console.log(JSON.stringify({k: timeSortKey(t, 'last_activity')}));"
    )
    # Assert
    assert out["k"] == _EPOCH_MS_2026_06_14


def test_sort_key_created_at_returns_epoch_ms() -> None:
    """``created_at`` mode reads task.created_at."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', created_at: '2026-06-14T00:00:00Z'};\n"
        "console.log(JSON.stringify({k: timeSortKey(t, 'created_at')}));"
    )
    # Assert
    assert out["k"] == _EPOCH_MS_2026_06_14


def test_sort_key_completed_at_prefers_log_meta() -> None:
    """``completed_at`` mode prefers the ``_log_meta.completed_at``
    envelope — the canonical place the store stamps the completion
    transition (see operator schema ADR-0007)."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', _log_meta: "
        "  {completed_at: '2026-06-13T00:00:00Z'},\n"
        "  completed_at: '2026-01-01T00:00:00Z'};\n"
        "console.log(JSON.stringify({k: timeSortKey(t, 'completed_at')}));"
    )
    # _log_meta.completed_at wins over the top-level completed_at fallback.
    # Assert
    assert out["k"] == _EPOCH_MS_2026_06_13


def test_sort_key_completed_at_falls_back_to_top_level() -> None:
    """When ``_log_meta`` is absent, a top-level ``completed_at`` still
    feeds the sort key — back-compat with older snapshots."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', completed_at: '2026-06-14T00:00:00Z'};\n"
        "console.log(JSON.stringify({k: timeSortKey(t, 'completed_at')}));"
    )
    # Assert
    assert out["k"] == _EPOCH_MS_2026_06_14


def test_sort_key_missing_value_returns_zero() -> None:
    """A task with neither the requested field nor a fallback returns 0
    so it sorts to the END for a DESCENDING comparator (b - a)."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a'};\n"
        "console.log(JSON.stringify({"
        "k1: timeSortKey(t, 'last_activity'),\n"
        "k2: timeSortKey(t, 'created_at'),\n"
        "k3: timeSortKey(t, 'completed_at')}));"
    )
    # Assert
    assert out == {"k1": 0, "k2": 0, "k3": 0}


def test_sort_key_unparseable_returns_zero() -> None:
    """A garbage ISO string returns 0 (graceful degrade)."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', last_activity: 'garbage'};\n"
        "console.log(JSON.stringify({k: timeSortKey(t, 'last_activity')}));"
    )
    # Assert
    assert out == {"k": 0}


def test_sort_key_descending_order_via_comparator() -> None:
    """Wire-level: sort an array DESCENDING by ``b - a`` and assert the
    newest landing at the front (matches the comparator emitted by
    ``_sortComparator('created_at')`` in board_v3.html)."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const cards = [
              {id: 'a', created_at: '2026-06-10T00:00:00Z'},
              {id: 'b', created_at: '2026-06-14T00:00:00Z'},
              {id: 'c', created_at: '2026-06-01T00:00:00Z'},
              {id: 'd'},
            ];
            cards.sort((a, b) =>
              timeSortKey(b, 'created_at') - timeSortKey(a, 'created_at'));
            console.log(JSON.stringify(cards.map(c => c.id)));
            """
        )
    )
    # Newest first; the no-created-at card lands at the end.
    # Assert
    assert out == ["b", "a", "c", "d"]


# ============================================================================
# Template contract — Sort options + Group-by-time checkbox + CSS link
# ============================================================================


def test_template_sort_dropdown_has_created_at_option() -> None:
    """The Sort <select> must carry the new ``created_at`` option so the
    operator sees "created (newest first)" in the dropdown."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        '<option value="created_at">' in html
    ), "board_v3.html missing 'created_at' option in #f-sort"


def test_template_sort_dropdown_has_completed_at_option() -> None:
    """The Sort <select> must carry the new ``completed_at`` option."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        '<option value="completed_at">' in html
    ), "board_v3.html missing 'completed_at' option in #f-sort"


def test_template_sort_dropdown_keeps_last_activity_option() -> None:
    """The pre-existing ``last_activity`` option must survive the edit
    (back-compat: the operator may already have it persisted in
    localStorage['scitex-cards:sort'])."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        '<option value="last_activity">' in html
    ), "board_v3.html lost the pre-existing 'last_activity' option"


def test_template_group_by_time_checkbox_present() -> None:
    """The Group-by-time checkbox must be mounted with id
    ``stx-toggle-group-by-time`` so the JS handler binds to it."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        'id="stx-toggle-group-by-time"' in html
    ), "board_v3.html missing #stx-toggle-group-by-time checkbox"


def test_template_group_by_time_lives_in_view_group_m() -> None:
    """The Group-by-time checkbox must live inside the existing
    ``.stx-todo-filterbar__group--view`` so the operator's time
    controls cluster with Layout/Sort/Group."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert m, "could not locate VIEW group block in template"


def test_template_group_by_time_lives_in_view_group_view_body_contains() -> None:
    """The Group-by-time checkbox must live inside the existing
    ``.stx-todo-filterbar__group--view`` so the operator's time
    controls cluster with Layout/Sort/Group."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert (
        'id="stx-toggle-group-by-time"' in view_body
    ), "Group-by-time checkbox not inside the VIEW group"


def test_template_loads_time_grouping_css() -> None:
    """The new 08-time-grouping.css must be wired into <head>."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "08-time-grouping.css" in html
    ), "board_v3.html never <link>s 08-time-grouping.css"


def test_template_wires_on_group_by_time_change_handler_html_contains() -> None:
    """The checkbox must wire onchange → onGroupByTimeChange, and the
    handler function must be defined in the inline <script>."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "onGroupByTimeChange" in html
    ), "board_v3.html missing onGroupByTimeChange handler reference"


def test_template_wires_on_group_by_time_change_handler_html_contains_2() -> None:
    """The checkbox must wire onchange → onGroupByTimeChange, and the
    handler function must be defined in the inline <script>."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "function onGroupByTimeChange" in html
    ), "board_v3.html missing onGroupByTimeChange function definition"


def test_template_defines_time_bucket_helper_html_contains() -> None:
    """The bucket classifier ``timeBucketForCard`` must be defined in
    the inline <script> — the test JS RUNTIME mirrors its body, so the
    name must continue to exist for the mirror to be load-bearing."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "function timeBucketForCard" in html
    ), "board_v3.html missing timeBucketForCard helper"


def test_template_defines_time_bucket_helper_html_contains_2() -> None:
    """The bucket classifier ``timeBucketForCard`` must be defined in
    the inline <script> — the test JS RUNTIME mirrors its body, so the
    name must continue to exist for the mirror to be load-bearing."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert "function timeSortKey" in html, "board_v3.html missing timeSortKey helper"


def test_template_persists_group_by_time_in_localstorage() -> None:
    """The Group-by-time state must persist in
    ``localStorage['scitex-cards:group-by-time']`` so the operator's
    preference survives reloads."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "scitex-cards:group-by-time" in html
    ), "board_v3.html does not persist group-by-time in localStorage"


# ============================================================================
# CSS contract — required selectors, no hardcoded colors, braces balance
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-todo-time-bucket",
        ".stx-todo-time-bucket-header",
        ".stx-todo-time-bucket-chevron",
        ".stx-todo-time-bucket-label",
        ".stx-todo-time-bucket-count",
        ".stx-todo-time-bucket-body",
        ".stx-todo-time-bucket--collapsed",
        ".stx-todo-time-bucket--today",
        ".stx-todo-time-bucket--week",
        ".stx-todo-time-bucket--month",
        ".stx-todo-time-bucket--older",
        ".filt-groupby-time",
        ".stx-todo-group-by-time-label",
    ],
)
def test_time_css_declares_selector(selector: str) -> None:
    """Every required bucket / toggle selector must have at least one
    CSS rule in 08-time-grouping.css."""
    # Arrange
    # Act
    css = _read(_TIME_CSS)
    # Assert
    assert selector in css, f"08-time-grouping.css missing rule for {selector!r}"


def test_time_css_no_hardcoded_colors() -> None:
    """Every color in 08-time-grouping.css must source from a
    ``var(--…)`` token. The only acceptable hex literals are inside
    ``var(--token, #fallback)`` fallback slots — same convention as
    07-toolbar-groups.css."""
    # Arrange
    css = _read(_TIME_CSS)
    comments_stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Strip every `var(--token, …)` substring AND any nested var() too.
    no_var = comments_stripped
    while "var(" in no_var:
        n = re.sub(r"var\([^()]*\)", "", no_var, flags=re.DOTALL)
        if n == no_var:
            break
        no_var = n
    # Forbidden literals: `#fff` / `#ffffff` / bare `white` / `black`
    # outside of property names like `white-space:`.
    pattern = re.compile(
        r"(?<![\w-])" r"(#[0-9a-fA-F]{3,8}\b|\b(?:white|black)\b(?!-))",
    )
    # Act
    matches = pattern.findall(no_var)
    # Assert
    assert not matches, (
        f"hardcoded colors found in 08-time-grouping.css "
        f"(must use var(--…) tokens): {matches[:5]}"
    )


def test_time_css_balanced_braces() -> None:
    """Edits must not corrupt brace nesting."""
    # Arrange
    # Act
    css = _read(_TIME_CSS)
    # Assert
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces in {_TIME_CSS}: "
        f"{css.count('{')} opens vs {css.count('}')} closes"
    )


def test_time_css_chevron_signals_collapsible_css_contains() -> None:
    """The chevron class must exist — collapsibility cue for the
    operator. Spec calls out ``▸`` / ``▾`` glyphs in the inline JS."""
    # Arrange
    # Act
    css = _read(_TIME_CSS)
    # Assert
    html = _read(_BOARD_V3_TEMPLATE)
    assert (
        ".stx-todo-time-bucket-chevron" in css
    ), "08-time-grouping.css missing chevron rule"


def test_time_css_chevron_signals_collapsible_case_2() -> None:
    """The chevron class must exist — collapsibility cue for the
    operator. Spec calls out ``▸`` / ``▾`` glyphs in the inline JS."""
    # Arrange
    # Act
    css = _read(_TIME_CSS)
    # Assert
    html = _read(_BOARD_V3_TEMPLATE)
    assert (
        "▸" in html and "▾" in html
    ), "board_v3.html chevron glyphs ▸ / ▾ missing from template"
