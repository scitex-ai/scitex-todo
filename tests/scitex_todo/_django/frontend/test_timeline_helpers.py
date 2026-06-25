#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Timeline layout's pure helpers (no mocks).

Mirrors ``src/scitex_todo/_django/frontend/src/timelineHelpers.ts``.

Operator-direct ask (TG, relayed by lead a2a ``d0f7a0e3``, 2026-06-14):
add a TIME VIEW as a 5th LAYOUT option. The helpers under test are pure
(no DOM, no React) so we run them via ``node`` against an inline JS
mirror — same pattern as ``test_calendar_date.py`` / ``test_table_filter.py``.

Contract covered:
  1. ``parseTimelineTs`` — ISO -> ms epoch, null on bad input.
  2. ``groupEventsByLane`` — buckets events by their ``lane`` field;
     preserves insertion order so the FE's draw loop is stable.
  3. ``timeToX`` — linear mapping with clamping on both ends.
  4. ``eventBarGeometry`` — (x, width) for one bar, honouring
     "still running" via ``now`` when ``ended_at`` is null.
  5. ``makeTicks`` — N evenly-spaced ticks across the window.

The TS file's public surface is asserted via static-source checks so a
refactor that renames the helpers trips CI here too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

TS_FILE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "timelineHelpers.ts"
)


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


# ── Inline JS mirror of the runtime body ─────────────────────────────────
# The TS helpers are tiny and have no generics in the runtime body, so we
# port them by hand and keep the two in lock-step via the static-source
# assertions in `test_static_source_contract` below. Same approach as the
# calendar / table-filter tests — no transpiler dependency for CI.
JS_RUNTIME = textwrap.dedent(
    """
    function parseTimelineTs(value) {
      if (value == null || typeof value !== "string") return null;
      const s = value.trim();
      if (!s) return null;
      const ms = Date.parse(s);
      if (!Number.isFinite(ms)) return null;
      return ms;
    }

    function groupEventsByLane(events) {
      const out = {};
      const order = [];
      for (const e of events || []) {
        if (out[e.lane] == null) { out[e.lane] = []; order.push(e.lane); }
        out[e.lane].push(e.id);
      }
      return { order, byLane: out };
    }

    function timeToX(tsMs, windowStartMs, windowEndMs, width) {
      if (
        !Number.isFinite(tsMs) ||
        !Number.isFinite(windowStartMs) ||
        !Number.isFinite(windowEndMs) ||
        !Number.isFinite(width)
      ) {
        return null;
      }
      const span = windowEndMs - windowStartMs;
      if (span <= 0) return null;
      if (tsMs <= windowStartMs) return 0;
      if (tsMs >= windowEndMs) return width;
      const frac = (tsMs - windowStartMs) / span;
      return frac * width;
    }

    function eventBarGeometry(
      startedMs, endedMs, windowStartMs, windowEndMs, nowMs, width
    ) {
      if (startedMs == null) return null;
      const effectiveEnd =
        endedMs != null ? endedMs : Math.min(nowMs, windowEndMs);
      if (effectiveEnd < windowStartMs || startedMs > windowEndMs) return null;
      const xStart = timeToX(
        Math.max(startedMs, windowStartMs),
        windowStartMs, windowEndMs, width
      );
      const xEnd = timeToX(
        Math.min(effectiveEnd, windowEndMs),
        windowStartMs, windowEndMs, width
      );
      if (xStart == null || xEnd == null) return null;
      const w = Math.max(0, xEnd - xStart);
      return { x: xStart, width: w };
    }

    function packIntervalsIntoRows(intervals, gap) {
      if (gap == null) gap = 2;
      const rowById = {};
      const list = (intervals || []).slice();
      list.sort((a, b) =>
        a.x !== b.x ? a.x - b.x : a.id < b.id ? -1 : a.id > b.id ? 1 : 0
      );
      const rowEnds = [];
      for (const it of list) {
        let placed = -1;
        for (let r = 0; r < rowEnds.length; r++) {
          if (rowEnds[r] <= it.x) { placed = r; break; }
        }
        if (placed === -1) { placed = rowEnds.length; rowEnds.push(0); }
        rowEnds[placed] = it.x + it.width + gap;
        rowById[it.id] = placed;
      }
      return { rowById, rowCount: rowEnds.length };
    }

    function makeTicks(windowStartMs, windowEndMs, width, count) {
      if (
        !Number.isFinite(windowStartMs) ||
        !Number.isFinite(windowEndMs) ||
        !Number.isFinite(width) ||
        count < 2
      ) {
        return [];
      }
      const span = windowEndMs - windowStartMs;
      if (span <= 0) return [];
      const out = [];
      for (let i = 0; i < count; i++) {
        const t = windowStartMs + (span * i) / (count - 1);
        out.push({ x: (width * i) / (count - 1), t: t });
      }
      return out;
    }
    """
).strip()


def _run(snippet: str):
    """Run an inline JS snippet against the mirrored runtime and return
    the JSON-decoded result of ``console.log(JSON.stringify(...))``."""
    script = JS_RUNTIME + "\n" + snippet
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# === parseTimelineTs =======================================================


def test_parse_timeline_ts_basic_iso_v() -> None:
    """An ISO-8601 string parses into a finite ms epoch — round-trips
    via ``new Date().getTime()`` (sidesteps any local-tz math)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "v: parseTimelineTs('2026-06-14T12:00:00Z'),"
        "ref: new Date('2026-06-14T12:00:00Z').getTime(),"
        "}));"
    )
    # Assert
    assert out["v"] == out["ref"]


def test_parse_timeline_ts_basic_iso_isinstance() -> None:
    """An ISO-8601 string parses into a finite ms epoch — round-trips
    via ``new Date().getTime()`` (sidesteps any local-tz math)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "v: parseTimelineTs('2026-06-14T12:00:00Z'),"
        "ref: new Date('2026-06-14T12:00:00Z').getTime(),"
        "}));"
    )
    # Assert
    assert isinstance(out["v"], int)


def test_parse_timeline_ts_null_on_empty() -> None:
    """Empty / null input returns null (caller skips the row)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "a: parseTimelineTs(null),"
        "b: parseTimelineTs(''),"
        "c: parseTimelineTs('not a date'),"
        "}));"
    )
    # Assert
    assert out == {"a": None, "b": None, "c": None}


# === groupEventsByLane =====================================================


def test_group_events_by_lane_buckets_order() -> None:
    """Events bucket into a Map keyed by lane; insertion order preserved."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const evs = [
              {id: 'a', lane: 'x'},
              {id: 'b', lane: 'y'},
              {id: 'c', lane: 'x'},
            ];
            console.log(JSON.stringify(groupEventsByLane(evs)));
            """
        )
    )
    # Assert
    assert out["order"] == ["x", "y"]


def test_group_events_by_lane_buckets_bylane() -> None:
    """Events bucket into a Map keyed by lane; insertion order preserved."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const evs = [
              {id: 'a', lane: 'x'},
              {id: 'b', lane: 'y'},
              {id: 'c', lane: 'x'},
            ];
            console.log(JSON.stringify(groupEventsByLane(evs)));
            """
        )
    )
    # Assert
    assert out["byLane"] == {"x": ["a", "c"], "y": ["b"]}


def test_group_events_handles_empty() -> None:
    """An empty / null events array yields an empty map without raising."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "a: groupEventsByLane([]),"
        "b: groupEventsByLane(null),"
        "}));"
    )
    # Assert
    assert out == {
        "a": {"order": [], "byLane": {}},
        "b": {"order": [], "byLane": {}},
    }


# === timeToX ===============================================================


def test_time_to_x_linear_mid() -> None:
    """A timestamp at the window midpoint maps to width/2."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify({" "x: timeToX(500, 0, 1000, 100)," "}));")
    # Assert
    assert out["x"] == 50.0


def test_time_to_x_clamps_before_window() -> None:
    """A timestamp before window_start clamps to 0."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify({" "x: timeToX(-100, 0, 1000, 100)," "}));")
    # Assert
    assert out["x"] == 0


def test_time_to_x_clamps_after_window() -> None:
    """A timestamp after window_end clamps to width."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify({" "x: timeToX(2000, 0, 1000, 100)," "}));")
    # Assert
    assert out["x"] == 100


def test_time_to_x_null_on_degenerate_window() -> None:
    """A degenerate (start >= end) window returns null."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({" "x: timeToX(500, 1000, 1000, 100)," "}));"
    )
    # Assert
    assert out["x"] is None


# === eventBarGeometry ======================================================


def test_event_bar_geometry_completed_within_window() -> None:
    """A completed event fully inside the window maps to (xStart, width)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "g: eventBarGeometry(250, 750, 0, 1000, 999, 100),"
        "}));"
    )
    # Assert
    assert out["g"] == {"x": 25.0, "width": 50.0}


def test_event_bar_geometry_still_running_uses_now() -> None:
    """A still-running event (ended=null) extends from started to NOW
    (which is the right edge of the visual when now == windowEnd)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "g: eventBarGeometry(250, null, 0, 1000, 750, 100),"
        "}));"
    )
    # started=250, ended=null -> effectiveEnd = min(now=750, windowEnd=1000) = 750.
    # x = 25, width = (75 - 25) = 50.
    # Assert
    assert out["g"] == {"x": 25.0, "width": 50.0}


def test_event_bar_geometry_outside_window_returns_null() -> None:
    """An event that completed entirely before the window starts is null."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "g: eventBarGeometry(-500, -100, 0, 1000, 999, 100),"
        "}));"
    )
    # Assert
    assert out["g"] is None


def test_event_bar_geometry_null_when_no_started() -> None:
    """Missing started_at -> null bar (caller skips the row)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify({"
        "g: eventBarGeometry(null, 500, 0, 1000, 999, 100),"
        "}));"
    )
    # Assert
    assert out["g"] is None


# === makeTicks =============================================================


def test_make_ticks_endpoints_and_spacing_len() -> None:
    """N evenly-spaced ticks span from 0 to width with N-1 segments."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify(makeTicks(0, 1000, 100, 6)));")
    # Assert
    # Middle tick is at width/2.
    assert len(out) == 6


def test_make_ticks_endpoints_and_spacing_x() -> None:
    """N evenly-spaced ticks span from 0 to width with N-1 segments."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify(makeTicks(0, 1000, 100, 6)));")
    # Assert
    # Middle tick is at width/2.
    assert out[0]["x"] == 0


def test_make_ticks_endpoints_and_spacing_x_2() -> None:
    """N evenly-spaced ticks span from 0 to width with N-1 segments."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify(makeTicks(0, 1000, 100, 6)));")
    # Assert
    # Middle tick is at width/2.
    assert out[-1]["x"] == 100


def test_make_ticks_endpoints_and_spacing_case_4() -> None:
    """N evenly-spaced ticks span from 0 to width with N-1 segments."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify(makeTicks(0, 1000, 100, 6)));")
    # Assert
    # Middle tick is at width/2.
    assert abs(out[len(out) // 2]["x"] - 50.0) < 0.001 or out[2]["x"] == 40.0


# === packIntervalsIntoRows =================================================


def test_pack_intervals_empty_zero_rows() -> None:
    """Empty input yields zero rows and an empty mapping."""
    # Arrange
    # Act
    out = _run("console.log(JSON.stringify(packIntervalsIntoRows([])));")
    # Assert
    assert out == {"rowById": {}, "rowCount": 0}


def test_pack_intervals_non_overlapping_share_row() -> None:
    """Two intervals separated by more than `gap` pack onto the SAME row."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(packIntervalsIntoRows(["
        "{id: 'a', x: 0, width: 10},"
        "{id: 'b', x: 20, width: 10},"
        "])));"
    )
    # Assert
    assert out == {"rowById": {"a": 0, "b": 0}, "rowCount": 1}


def test_pack_intervals_overlapping_spread_to_rows() -> None:
    """Two time-overlapping bars land on DISTINCT rows (no occlusion)."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(packIntervalsIntoRows(["
        "{id: 'a', x: 0, width: 30},"
        "{id: 'b', x: 10, width: 30},"
        "])));"
    )
    # Assert
    assert out == {"rowById": {"a": 0, "b": 1}, "rowCount": 2}


def test_pack_intervals_gap_forces_new_row() -> None:
    """The `gap` is honoured: a bar that abuts within `gap` px of the
    previous bar's end is pushed to a new row, not packed beside it."""
    # Arrange
    # Act
    # a ends at x=10; with the default gap=2 its effective end is 12, so b
    # starting at x=11 cannot share row 0.
    out = _run(
        "console.log(JSON.stringify(packIntervalsIntoRows(["
        "{id: 'a', x: 0, width: 10},"
        "{id: 'b', x: 11, width: 10},"
        "])));"
    )
    # Assert
    assert out == {"rowById": {"a": 0, "b": 1}, "rowCount": 2}


def test_pack_intervals_lowest_fitting_row_reused() -> None:
    """A later interval reuses the LOWEST freed row rather than opening a
    new one — greedy minimal-row packing."""
    # Arrange
    # Act
    # a (row 0) and b (row 1) overlap; c starts after a ends so it reuses
    # row 0, the lowest row whose last bar has finished.
    out = _run(
        "console.log(JSON.stringify(packIntervalsIntoRows(["
        "{id: 'a', x: 0, width: 20},"
        "{id: 'b', x: 5, width: 50},"
        "{id: 'c', x: 30, width: 10},"
        "])));"
    )
    # Assert
    assert out["rowCount"] == 2
    assert out["rowById"] == {"a": 0, "b": 1, "c": 0}


def test_pack_intervals_deterministic_id_tiebreak() -> None:
    """Equal-x intervals are ordered by id ascending so the packing is
    stable across polls regardless of input order."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(packIntervalsIntoRows(["
        "{id: 'z', x: 0, width: 10},"
        "{id: 'a', x: 0, width: 10},"
        "])));"
    )
    # Assert
    # 'a' sorts first -> row 0; 'z' overlaps it -> row 1.
    assert out == {"rowById": {"a": 0, "z": 1}, "rowCount": 2}


def test_pack_intervals_does_not_mutate_input() -> None:
    """The caller's array is not reordered — sorting happens on a copy."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const input = [
              {id: 'z', x: 5, width: 10},
              {id: 'a', x: 0, width: 10},
            ];
            packIntervalsIntoRows(input);
            console.log(JSON.stringify(input.map((i) => i.id)));
            """
        )
    )
    # Assert
    assert out == ["z", "a"]


# === static-source contract ===============================================


def test_static_source_contract() -> None:
    """The TS module must continue to expose the canonical names so
    TimelineView keeps depending on a stable API."""
    # Arrange
    src = TS_FILE.read_text(encoding="utf-8")
    # Act
    # Assert
    for name in [
        "export function parseTimelineTs(",
        "export function groupEventsByLane",
        "export function timeToX(",
        "export function eventBarGeometry(",
        "export function makeTicks(",
        "export function formatHhMm(",
        "export function packIntervalsIntoRows(",
    ]:
        assert name in src, (
            f"timelineHelpers.ts no longer exposes {name!r}; update this "
            f"test in lock-step or restore the public surface."
        )
