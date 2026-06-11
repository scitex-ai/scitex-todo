#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the org-mode export adapter (P4 PR2, lead-approved 2026-06-12).

No mocks (STX-NM / PA-306); fixtures are plain dicts in the wire shape
``scitex_todo.load_tasks`` returns. AAA pattern; one assertion per test
where reasonable; descriptive names (TQ001 / TQ002 / TQ007).
"""

from __future__ import annotations

from scitex_todo._org import build_org


class TestOrgPreamble:
    def test_contains_title_directive(self):
        text = build_org([])
        assert "#+TITLE: scitex-todo export" in text

    def test_declares_todo_state_keywords(self):
        text = build_org([])
        assert "#+TODO: TODO INPROGRESS WAITING | DONE CANCELLED SOMEDAY" in text


class TestSingleTaskHeading:
    def test_renders_todo_heading(self):
        tasks = [{"id": "a", "title": "Ship it", "status": "pending"}]
        text = build_org(tasks)
        assert "* TODO Ship it" in text

    def test_maps_in_progress_to_inprogress(self):
        tasks = [{"id": "a", "title": "Mid", "status": "in_progress"}]
        text = build_org(tasks)
        assert "* INPROGRESS Mid" in text

    def test_maps_blocked_to_waiting(self):
        tasks = [{"id": "a", "title": "B", "status": "blocked"}]
        text = build_org(tasks)
        assert "* WAITING B" in text

    def test_maps_done_to_done(self):
        tasks = [{"id": "a", "title": "D", "status": "done"}]
        text = build_org(tasks)
        assert "* DONE D" in text


class TestDeadlineLine:
    def test_emits_bare_date_timestamp(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "deadline": "2026-06-15",
            }
        ]
        text = build_org(tasks)
        assert "DEADLINE: <2026-06-15>" in text

    def test_truncates_iso_datetime_to_date(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "deadline": "2026-06-15T18:00:00+09:00",
            }
        ]
        text = build_org(tasks)
        assert "DEADLINE: <2026-06-15>" in text

    def test_skips_when_absent(self):
        tasks = [{"id": "a", "title": "x", "status": "pending"}]
        text = build_org(tasks)
        assert "DEADLINE:" not in text


class TestScheduledLine:
    def test_emits_when_present(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "scheduled": "2026-06-10",
            }
        ]
        text = build_org(tasks)
        assert "SCHEDULED: <2026-06-10>" in text

    def test_co_exists_with_deadline_on_same_line(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "deadline": "2026-06-15", "scheduled": "2026-06-10",
            }
        ]
        text = build_org(tasks)
        # Both on one line per org convention.
        assert "DEADLINE: <2026-06-15> SCHEDULED: <2026-06-10>" in text


class TestPropertiesDrawer:
    def test_includes_id(self):
        tasks = [{"id": "task-42", "title": "x", "status": "pending"}]
        text = build_org(tasks)
        assert ":ID: task-42" in text

    def test_includes_project_when_set(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "project": "scitex-todo",
            }
        ]
        text = build_org(tasks)
        assert ":PROJECT: scitex-todo" in text

    def test_omits_unset_fields(self):
        tasks = [{"id": "a", "title": "x", "status": "pending"}]
        text = build_org(tasks)
        # Project absent → drawer should not have the line.
        assert ":PROJECT:" not in text

    def test_includes_parent_pointer(self):
        tasks = [
            {
                "id": "child", "title": "c", "status": "pending",
                "parent": "umbrella",
            }
        ]
        text = build_org(tasks)
        assert ":PARENT: umbrella" in text


class TestNoteBody:
    def test_indents_note_under_heading(self):
        tasks = [
            {
                "id": "a", "title": "x", "status": "pending",
                "note": "line1\nline2",
            }
        ]
        text = build_org(tasks)
        assert "  line1" in text
        assert "  line2" in text


class TestEmptyTaskList:
    def test_renders_preamble_only(self):
        text = build_org([])
        assert text.endswith("\n")
        # No headings emitted.
        assert "* " not in text


# EOF
