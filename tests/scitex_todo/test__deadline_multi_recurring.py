#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for P4 PR3 — multi + recurring deadlines.

Extends ``test__deadline.py`` with the new shapes:
  * org-style repeater suffix on the single ``deadline`` field:
    ``+Nu`` / ``++Nu`` (catch-up), u in {d, w, m, y};
  * optional ``deadlines: list[str]`` field, mutually exclusive with
    ``deadline``; loader computes a synthetic ``deadline_next``;
  * ``next_deadline_for_task`` helper used by the graph endpoint.

No mocks (STX-NM / PA-306); AAA + descriptive class names; one
assertion per logical concept.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scitex_todo import TaskValidationError
from scitex_todo._model import (
    Repeater,
    _parse_deadline_or_raise,
    load_tasks,
    next_deadline_for_task,
)


def _write(tmp_path, text):
    path = tmp_path / "tasks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# === Repeater parser ====================================================


class TestRepeaterParseWeekly:
    def test_parses_weekly_suffix(self):
        _, rep = _parse_deadline_or_raise(
            "2026-06-15 +1w", source="<t>", tid="x", label="deadline",
        )
        assert rep == Repeater(n=1, unit="w", catchup=False)


class TestRepeaterParseCatchupMonthly:
    def test_parses_catchup_monthly(self):
        _, rep = _parse_deadline_or_raise(
            "2026-06-15 ++2m", source="<t>", tid="x", label="deadline",
        )
        assert rep == Repeater(n=2, unit="m", catchup=True)


class TestRepeaterRejectZeroN:
    def test_raises(self):
        with pytest.raises(TaskValidationError, match="zero/negative"):
            _parse_deadline_or_raise(
                "2026-06-15 +0w",
                source="<t>", tid="x", label="deadline",
            )


class TestRepeaterAcceptsAllUnits:
    @pytest.mark.parametrize("suffix,unit", [
        ("+3d", "d"), ("+1w", "w"), ("+2m", "m"), ("+5y", "y"),
    ])
    def test_unit(self, suffix, unit):
        _, rep = _parse_deadline_or_raise(
            f"2026-06-15 {suffix}",
            source="<t>", tid="x", label="deadline",
        )
        assert rep.unit == unit


class TestRepeaterRejectUnknownUnit:
    def test_raises(self):
        with pytest.raises(TaskValidationError, match="unparseable"):
            _parse_deadline_or_raise(
                "2026-06-15 +1x",
                source="<t>", tid="x", label="deadline",
            )


# === next_occurrence math ===============================================


class TestNextOccurrenceWeekly:
    def test_advances_one_week_from_past_to_present(self):
        # base 2026-06-15; now is 2026-06-22 — first future occurrence
        # is 2026-06-22.
        rep = Repeater(n=1, unit="w", catchup=False)
        nxt = rep.next_occurrence(
            dt.datetime(2026, 6, 15),
            now=dt.datetime(2026, 6, 22),
        )
        assert nxt == dt.datetime(2026, 6, 22)


class TestNextOccurrenceCatchupSkipsPast:
    def test_catchup_skips_to_next_future(self):
        # base way in the past; should jump to the next future Mon.
        rep = Repeater(n=1, unit="w", catchup=True)
        nxt = rep.next_occurrence(
            dt.datetime(2026, 1, 1),
            now=dt.datetime(2026, 6, 12),
        )
        # 2026-01-01 + N*7d ≥ 2026-06-12
        assert nxt > dt.datetime(2026, 6, 12) - dt.timedelta(days=7)


class TestNextOccurrenceFutureBaseReturnsBase:
    def test_future_base_unchanged(self):
        rep = Repeater(n=1, unit="w", catchup=False)
        base = dt.datetime(2030, 1, 1)
        assert rep.next_occurrence(base, now=dt.datetime(2026, 6, 12)) == base


# === Mutual exclusion: deadline vs deadlines ===========================


class TestMutualExclusion:
    def test_rejects_both_set(self, tmp_path):
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-15', deadlines: ['2026-06-15']}\n",
        )
        with pytest.raises(
            TaskValidationError, match="BOTH deadline and deadlines"
        ):
            load_tasks(store)


class TestDeadlinesEmptyListRejected:
    def test_raises(self, tmp_path):
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, deadlines: []}\n",
        )
        with pytest.raises(TaskValidationError, match="empty deadlines list"):
            load_tasks(store)


class TestDeadlinesPerEntryValidated:
    def test_garbage_entry_rejected(self, tmp_path):
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadlines: ['2026-06-15', 'nope']}\n",
        )
        with pytest.raises(TaskValidationError, match="deadlines\\[1\\]"):
            load_tasks(store)


# === next_deadline_for_task (graph wire field) ==========================


class TestNextDeadlineForSingleScalar:
    def test_bare_iso(self):
        out = next_deadline_for_task({"deadline": "2026-06-15"})
        assert out == "2026-06-15"


class TestNextDeadlineForRecurring:
    def test_advances_recurring_past_seed(self):
        out = next_deadline_for_task(
            {"deadline": "2026-01-01 +1w"},
            now=dt.datetime(2026, 6, 12),
        )
        # Some Monday on/after 2026-06-12.
        parsed = dt.date.fromisoformat(out)
        assert parsed >= dt.date(2026, 6, 12)


class TestNextDeadlineForMultiPicksEarliest:
    def test_chooses_earliest_next_occurrence(self):
        out = next_deadline_for_task(
            {"deadlines": ["2026-07-01", "2026-06-15"]},
            now=dt.datetime(2026, 6, 1),
        )
        assert out == "2026-06-15"


class TestNextDeadlineForNoneWhenAbsent:
    def test_no_fields_returns_none(self):
        assert next_deadline_for_task({"id": "a"}) is None


# EOF
