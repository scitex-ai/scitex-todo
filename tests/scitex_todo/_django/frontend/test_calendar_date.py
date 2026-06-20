#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Calendar layout's pure date-assignment helpers (no mocks).

Mirrors ``src/scitex_todo/_django/frontend/src/calendarDate.ts``.

Operator TG 13295 (relayed by lead a2a ``510a58d4``): add a CALENDAR VIEW
as a 4th LAYOUT option. Tasks land on a month grid by their
``deadline_next`` → ``deadline`` → ``last_activity`` precedence. The
helpers under test are pure (no DOM, no React) so we run them via
``node`` against an inline JS mirror — same pattern as
``test_table_filter.py`` (PR #171).

Contract covered:
  1. ``taskDateForCalendar`` precedence — deadline_next beats deadline
     beats last_activity; null when all absent.
  2. ``tasksByDate`` placement — given a tasks array, the day-cell map
     contains the expected counts on the expected dates.
  3. ``monthGridDays`` — given (year, monthIndex), returns 42 cells
     (6 × 7) with leading/trailing neighbour-month days marked
     ``inMonth=false``.
  4. ``isToday`` is set correctly on the matching cell.

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
    / "calendarDate.ts"
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
# table-filter test — no transpiler dependency for CI.
JS_RUNTIME = textwrap.dedent(
    """
    function parseCalendarDate(value) {
      if (value == null || typeof value !== "string") return null;
      const s = value.trim();
      if (!s) return null;
      const m = /^(\\d{4})-(\\d{2})-(\\d{2})/.exec(s);
      if (!m) return null;
      const year = Number(m[1]);
      const month = Number(m[2]);
      const day = Number(m[3]);
      if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
        return null;
      }
      if (month < 1 || month > 12 || day < 1 || day > 31) return null;
      const dt = new Date(year, month - 1, day, 12, 0, 0, 0);
      if (
        dt.getFullYear() !== year ||
        dt.getMonth() !== month - 1 ||
        dt.getDate() !== day
      ) {
        return null;
      }
      return dt;
    }

    function taskDateForCalendar(task) {
      if (task == null) return null;
      const dn = parseCalendarDate(task.deadline_next);
      if (dn) return dn;
      const d = parseCalendarDate(task.deadline);
      if (d) return d;
      const la = parseCalendarDate(task.last_activity);
      if (la) return la;
      return null;
    }

    function isSameDay(a, b) {
      if (a == null || b == null) return false;
      return (
        a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate()
      );
    }

    function dateKey(d) {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${day}`;
    }

    function monthGridDays(year, monthIndex, today) {
      const first = new Date(year, monthIndex, 1, 12, 0, 0, 0);
      const lead = first.getDay();
      const startDate = new Date(first);
      startDate.setDate(first.getDate() - lead);
      const cells = [];
      const now = today != null ? today : new Date();
      for (let i = 0; i < 42; i++) {
        const d = new Date(startDate);
        d.setDate(startDate.getDate() + i);
        cells.push({
          key: dateKey(d),
          inMonth: d.getMonth() === monthIndex && d.getFullYear() === year,
          isToday: isSameDay(d, now),
          weekday: d.getDay(),
          day: d.getDate(),
        });
      }
      return cells;
    }

    function tasksByDate(tasks) {
      const out = {};
      for (const t of tasks || []) {
        const d = taskDateForCalendar(t);
        if (d == null) continue;
        const k = dateKey(d);
        if (out[k]) out[k].push(t.id);
        else out[k] = [t.id];
      }
      return out;
    }
    """
).strip()


def _run(snippet: str) -> dict:
    """Run an inline JS snippet against the mirrored runtime and return
    the JSON-decoded result of `console.log(JSON.stringify(...))`."""
    script = JS_RUNTIME + "\n" + snippet
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


def test_deadline_beats_last_activity() -> None:
    """``deadline`` takes precedence over ``last_activity`` when both
    are present — operator's stated precedence (TG 13295)."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', deadline: '2026-06-14', last_activity: '2025-01-01T10:00:00Z'};\n"
        "const d = taskDateForCalendar(t);\n"
        "console.log(JSON.stringify({key: dateKey(d)}));"
    )
    # Assert
    assert out == {"key": "2026-06-14"}


def test_deadline_next_beats_deadline() -> None:
    """``deadline_next`` (server-computed next occurrence) takes
    precedence over the static ``deadline`` field when both are present —
    so recurring tasks land on the right cell automatically."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', deadline: '2026-06-14', deadline_next: '2026-07-01'};\n"
        "const d = taskDateForCalendar(t);\n"
        "console.log(JSON.stringify({key: dateKey(d)}));"
    )
    # Assert
    assert out == {"key": "2026-07-01"}


def test_last_activity_fallback() -> None:
    """When ``deadline``/``deadline_next`` are absent, ``last_activity``'s
    date portion is the bucket."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a', last_activity: '2026-06-10T18:42:00Z'};\n"
        "const d = taskDateForCalendar(t);\n"
        "console.log(JSON.stringify({key: dateKey(d)}));"
    )
    # The date portion is "2026-06-10" — anchored at local noon by
    # parseCalendarDate so timezone wobble doesn't shift the bucket.
    # Assert
    assert out == {"key": "2026-06-10"}


def test_no_date_returns_null() -> None:
    """A task with neither deadline nor last_activity yields null —
    the operator's rule "skip; don't render it"."""
    # Arrange
    # Act
    out = _run(
        "const t = {id: 'a'};\n"
        "const d = taskDateForCalendar(t);\n"
        "console.log(JSON.stringify({isNull: d === null}));"
    )
    # Assert
    assert out == {"isNull": True}


def test_tasks_by_date_placement_counts_out() -> None:
    """Given a tasks array, ``tasksByDate`` produces a map keyed by
    YYYY-MM-DD with the right task ids on each day — matches the
    operator-facing "this day has N tasks" contract."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const tasks = [
              {id: 't1', deadline: '2026-06-14'},
              {id: 't2', deadline: '2026-06-14'},
              {id: 't3', deadline: '2026-06-15'},
              {id: 't4', last_activity: '2026-06-15T08:00:00Z'},
              {id: 't5'},  // no date — should be skipped
              {id: 't6', deadline_next: '2026-06-20'},
            ];
            console.log(JSON.stringify(tasksByDate(tasks)));
            """
        )
    )
    # Assert
    assert out == {
        "2026-06-14": ["t1", "t2"],
        "2026-06-15": ["t3", "t4"],
        "2026-06-20": ["t6"],
    }


def test_tasks_by_date_placement_counts_value_excludes() -> None:
    """Given a tasks array, ``tasksByDate`` produces a map keyed by
    YYYY-MM-DD with the right task ids on each day — matches the
    operator-facing "this day has N tasks" contract."""
    # Arrange
    # Act
    out = _run(
        textwrap.dedent(
            """
            const tasks = [
              {id: 't1', deadline: '2026-06-14'},
              {id: 't2', deadline: '2026-06-14'},
              {id: 't3', deadline: '2026-06-15'},
              {id: 't4', last_activity: '2026-06-15T08:00:00Z'},
              {id: 't5'},  // no date — should be skipped
              {id: 't6', deadline_next: '2026-06-20'},
            ];
            console.log(JSON.stringify(tasksByDate(tasks)));
            """
        )
    )
    # Assert
    assert "t5" not in {tid for ids in out.values() for tid in ids}


def test_month_grid_returns_42_cells_with_leading_trailing_len() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert len(out) == 42


def test_month_grid_returns_42_cells_with_leading_trailing_key() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert out[0]["key"] == "2026-05-31"


def test_month_grid_returns_42_cells_with_leading_trailing_inmonth() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert out[0]["inMonth"] is False


def test_month_grid_returns_42_cells_with_leading_trailing_key_2() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert out[1]["key"] == "2026-06-01"


def test_month_grid_returns_42_cells_with_leading_trailing_inmonth_2() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert out[1]["inMonth"] is True


def test_month_grid_returns_42_cells_with_leading_trailing_in_month_keys() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert in_month_keys[0] == "2026-06-01"


def test_month_grid_returns_42_cells_with_leading_trailing_in_month_keys_2() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert in_month_keys[-1] == "2026-06-30"


def test_month_grid_returns_42_cells_with_leading_trailing_len_2() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert len(in_month_keys) == 30


def test_month_grid_returns_42_cells_with_leading_trailing_all() -> None:
    """``monthGridDays`` always returns 42 cells (6 weeks × 7 days), so
    the grid height never jitters. Leading + trailing days that fall
    OUTSIDE the requested month must be flagged ``inMonth=false``."""
    # June 2026: June 1 is a Monday — so one leading Sunday (May 31) and
    # several trailing July days fill the grid.
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Assert
    # First cell is the leading Sunday before June 1 — May 31, 2026
    # (a Sunday). Out-of-month.
    # Cell 1 (Mon June 1) is the first in-month day.
    # The last in-month day is June 30 — every later cell is the
    # trailing July fill and must be inMonth=false.
    in_month_keys = [c["key"] for c in out if c["inMonth"]]
    # Trailing cells (after June 30) are July, inMonth=false.
    trailing = [c for c in out if not c["inMonth"] and c["key"] > "2026-06-30"]
    assert all(c["key"].startswith("2026-07-") for c in trailing)


def test_today_cell_flag_is_set_len() -> None:
    """The cell matching the `today` parameter must carry
    ``isToday=true`` — drives the accent-border ring on the Calendar."""
    # Render June 2026 with `today` pinned at June 14 — exactly one
    # cell should fire isToday.
    # Arrange
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Act
    today_cells = [c for c in out if c["isToday"]]
    # Assert
    assert len(today_cells) == 1


def test_today_cell_flag_is_set_key() -> None:
    """The cell matching the `today` parameter must carry
    ``isToday=true`` — drives the accent-border ring on the Calendar."""
    # Render June 2026 with `today` pinned at June 14 — exactly one
    # cell should fire isToday.
    # Arrange
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Act
    today_cells = [c for c in out if c["isToday"]]
    # Assert
    assert today_cells[0]["key"] == "2026-06-14"


def test_today_cell_flag_is_set_inmonth() -> None:
    """The cell matching the `today` parameter must carry
    ``isToday=true`` — drives the accent-border ring on the Calendar."""
    # Render June 2026 with `today` pinned at June 14 — exactly one
    # cell should fire isToday.
    # Arrange
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 5, new Date(2026, 5, 14, 12))));"
    )
    # Act
    today_cells = [c for c in out if c["isToday"]]
    # Assert
    assert today_cells[0]["inMonth"] is True


def test_today_flag_false_when_today_is_in_a_different_month() -> None:
    """Browsing to a non-current month must yield zero `isToday` cells —
    so the "today" highlight only fires when looking at the live month."""
    # Render May 2026 but pin `today` at June 14 — no May cell should
    # be flagged today.
    # Arrange
    out = _run(
        "console.log(JSON.stringify(monthGridDays(2026, 4, new Date(2026, 5, 14, 12))));"
    )
    # Act
    today_cells = [c for c in out if c["isToday"]]
    # Assert
    assert today_cells == []


def test_static_source_contract() -> None:
    """The TS module must continue to expose the canonical names so the
    CalendarView component keeps depending on a stable API."""
    # Arrange
    src = TS_FILE.read_text(encoding="utf-8")
    # Act
    # Assert
    for name in [
        "export function taskDateForCalendar(",
        "export function monthGridDays(",
        "export function tasksByDate<",
        "export function isSameDay(",
        "export function dateKey(",
        "export function parseCalendarDate(",
        "export function shiftMonth(",
        "export const MONTH_NAMES",
        "export const WEEKDAY_NAMES",
    ]:
        assert name in src, (
            f"calendarDate.ts no longer exposes {name!r}; update this "
            f"test in lock-step or restore the public surface."
        )
