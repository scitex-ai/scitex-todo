#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``fleet`` key on the GET /graph payload — the per-agent
liveness summary the operator scans from the board header to answer
"who is alive + working on what + blocked on me" without leaving the
board (ADR-0008 design, ticket ``proj-scitex-todo-fleet-liveness``,
operator TG 9576: 返事が来ない＝私にとって死んだのと同じ).

First-slice scope (this PR — what's covered here):
  * payload includes a ``fleet`` list, ordered deterministically (sorted
    by agent name)
  * status precedence: blocking-operator > working > active > idle
  * current_task derivation: in_progress > most-recent activity > top-
    priority pending
  * counts (task_count / runnable_count / blocked_count /
    blocking_operator_count) reflect the input
  * tasks without ``agent`` (and without ``assignee`` fallback) are
    excluded
  * ``assignee`` is honoured as a fallback for older rows

DEFERRED (NOT covered):
  * sidecar daemon liveness (no daemon today; status derived from YAML)
  * cross-host roll-up (single-host slice only)
  * JS interaction tests (no headless browser in the suite)

Real ``RequestFactory`` GET against a tmp ``tasks.yaml`` — no mocks
(STX-NM / PA-306). Mirrors the test pattern in ``test_resolve.py``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django import views  # noqa: E402
from scitex_todo._django.services import _reset_cache  # noqa: E402


# Curated 6-task fixture exercising every status-precedence branch + the
# current_task derivation chain + the assignee fallback.
_STORE_TEXT = (
    "tasks:\n"
    "  - id: clew-pac-line\n"
    "    title: 'clew PAC line'\n"
    "    status: in_progress\n"
    "    agent: proj-paper-scitex-clew\n"
    "    project: paper-scitex-clew\n"
    "    priority: 1\n"
    "    last_activity: '2026-06-08T06:00:00Z'\n"
    "  - id: clew-figure-fixes\n"
    "    title: 'clew figure fixes'\n"
    "    status: pending\n"
    "    agent: proj-paper-scitex-clew\n"
    "    priority: 30\n"
    "  - id: nv-stuck-on-op\n"
    "    title: 'NV stuck on operator decision'\n"
    "    status: blocked\n"
    "    blocker: operator-decision\n"
    "    agent: proj-neurovista\n"
    "    last_activity: '2026-06-07T22:00:00Z'\n"
    "  - id: nv-other\n"
    "    title: 'NV other'\n"
    "    status: pending\n"
    "    agent: proj-neurovista\n"
    "    priority: 50\n"
    "  - id: hub-recent\n"
    "    title: 'hub recent activity'\n"
    "    status: pending\n"
    "    agent: proj-scitex-hub\n"
    "    last_activity: '2026-06-08T05:30:00Z'\n"
    "    priority: 20\n"
    "  - id: orphan-task\n"
    "    title: 'task with no agent or assignee'\n"
    "    status: pending\n"
    "  - id: legacy-assignee-task\n"
    "    title: 'older row using assignee instead of agent'\n"
    "    status: pending\n"
    "    assignee: legacy-agent\n"
    "    priority: 60\n"
)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _graph(store_path: str) -> dict:
    """GET /graph against the tmp store and return the parsed payload."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    assert response.status_code == 200
    return json.loads(response.content)


def _fleet_by_name(payload: dict) -> dict:
    """Index the fleet list by agent name."""
    return {a["name"]: a for a in payload["fleet"]}


# === Payload shape ==========================================================


def test_graph_payload_contains_fleet_key(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    assert "fleet" in payload


def test_fleet_is_a_list(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    assert isinstance(payload["fleet"], list)


def test_fleet_is_sorted_by_agent_name(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert — deterministic ordering so the FE dot-strip doesn't reshuffle
    # on every poll. Sorted by the `name` field ascending.
    names = [a["name"] for a in payload["fleet"]]
    assert names == sorted(names)


# === Exclusion of agent-less tasks ==========================================


def test_orphan_task_with_no_agent_is_excluded(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert — the only task carrying neither `agent` nor `assignee`
    # should not surface a fleet row (the dot-strip stays focused on
    # known agents).
    names = {a["name"] for a in payload["fleet"]}
    assert "orphan-task" not in names


def test_assignee_is_used_as_agent_fallback(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert — older rows using `assignee` (the pre-rename field) still
    # surface as a fleet row keyed by that name.
    names = {a["name"] for a in payload["fleet"]}
    assert "legacy-agent" in names


# === Status precedence ======================================================


def test_blocking_operator_takes_precedence_over_working(store):
    # Arrange — proj-neurovista has 1 blocker=operator-decision + 1 pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["status"] == "blocking-operator"


def test_in_progress_yields_working_status(store):
    # Arrange — proj-paper-scitex-clew has an in_progress task + a pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-paper-scitex-clew"]["status"] == "working"


def test_recent_activity_yields_active_status(store):
    # Arrange — proj-scitex-hub has only pending tasks but one has
    # last_activity set (no blocker, no in_progress).
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-scitex-hub"]["status"] == "active"


def test_no_activity_no_progress_yields_idle_status(store):
    # Arrange — legacy-agent owns one pending task with no last_activity.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["legacy-agent"]["status"] == "idle"


# === current_task derivation ================================================


def test_current_task_prefers_in_progress(store):
    # Arrange — proj-paper-scitex-clew has clew-pac-line (in_progress) and
    # clew-figure-fixes (pending). The dot-strip tooltip should name the
    # in_progress one.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-paper-scitex-clew"]["current_task_id"] == "clew-pac-line"


def test_current_task_falls_back_to_most_recent_activity(store):
    # Arrange — proj-scitex-hub: no in_progress; one task with
    # last_activity. The dot-strip should surface that one.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-scitex-hub"]["current_task_id"] == "hub-recent"


# === Counts =================================================================


def test_task_count_matches_input(store):
    # Arrange — proj-neurovista has 2 tasks in the fixture.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["task_count"] == 2


def test_blocked_count_only_counts_status_blocked(store):
    # Arrange — proj-neurovista has 1 blocked + 1 pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["blocked_count"] == 1


def test_blocking_operator_count_only_counts_operator_decision(store):
    # Arrange — proj-neurovista has 1 task with blocker=operator-decision.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["blocking_operator_count"] == 1


def test_runnable_count_excludes_blocked_done_deferred_failed_goal(store):
    # Arrange — proj-neurovista: 1 blocked + 1 pending. Runnable = the
    # pending one. blocked / done / deferred / failed / goal are the
    # non-runnable set (mirrors the task-harvest skill).
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["runnable_count"] == 1
