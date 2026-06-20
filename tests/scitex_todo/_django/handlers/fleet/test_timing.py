#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure-function tests for ``compute_timing`` / ``task_durations``.

No mocks (STX-NM/PA-306). Synthetic task dicts only — the function is
deliberately I/O-free so tests don't need a Django settings module, a
task store on disk, or a tmp_path fixture.

Contract pinned here:

  1. Per-task durations are correct end-to-end (created → started →
     done) for a fully-instrumented task.
  2. Missing ``created_at`` → ``created_to_started`` and
     ``created_to_done`` are None; ``started_to_done`` is still
     computed if started + done both exist.
  3. Missing ``_log_meta.started_at`` → both ``created_to_started`` and
     ``started_to_done`` are None.
  4. Tasks whose ``_log_meta.completed_at`` falls OUTSIDE the
     ``window_days`` window are excluded from aggregates.
  5. ``per_agent`` only carries agents that had ≥1 done task in window.
  6. ``per_group`` rolls a null/empty group into the ``"<ungrouped>"``
     key (sentinel, not a content literal).
  7. Median + p95 are correct on a small distribution.
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo._django.handlers.fleet.timing import (
    compute_timing,
    task_durations,
)

# ---------------------------------------------------------------------------
# Helpers — small builders so each test stays readable.
# ---------------------------------------------------------------------------

_NOW = _dt.datetime(2026, 6, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(delta_minutes: float) -> str:
    """Return an ISO-8601 string ``delta_minutes`` before ``_NOW``."""
    ts = _NOW - _dt.timedelta(minutes=delta_minutes)
    return ts.isoformat()


def _task(
    *,
    id_: str = "t1",
    created_min_ago: float | None = 100.0,
    started_min_ago: float | None = 90.0,
    done_min_ago: float | None = 60.0,
    agent: str | None = "agent-alpha",
    project: str | None = "proj-x",
    group: str | None = "g1",
    status: str = "done",
) -> dict:
    """Build a synthetic task dict with the timing fields populated."""
    log_meta: dict = {}
    if started_min_ago is not None:
        log_meta["started_at"] = _iso(started_min_ago)
    if done_min_ago is not None:
        log_meta["completed_at"] = _iso(done_min_ago)
    out: dict = {
        "id": id_,
        "status": status,
        "_log_meta": log_meta,
    }
    if created_min_ago is not None:
        out["created_at"] = _iso(created_min_ago)
    if agent is not None:
        out["agent"] = agent
    if project is not None:
        out["project"] = project
    if group is not None:
        out["group"] = group
    return out


# ---------------------------------------------------------------------------
# task_durations — per-task math.
# ---------------------------------------------------------------------------


def test_task_durations_full_task_computes_all_three_fields_created_to_started():
    # Arrange: created 100m ago, started 90m ago, done 60m ago.
    t = _task()
    # Act
    d = task_durations(t)
    # Assert — values in seconds.
    assert d["created_to_started"] == 10 * 60


def test_task_durations_full_task_computes_all_three_fields_started_to_done():
    # Arrange: created 100m ago, started 90m ago, done 60m ago.
    t = _task()
    # Act
    d = task_durations(t)
    # Assert — values in seconds.
    assert d["started_to_done"] == 30 * 60


def test_task_durations_full_task_computes_all_three_fields_created_to_done():
    # Arrange: created 100m ago, started 90m ago, done 60m ago.
    t = _task()
    # Act
    d = task_durations(t)
    # Assert — values in seconds.
    assert d["created_to_done"] == 40 * 60


def test_task_durations_missing_created_at_leaves_two_fields_none_created_to_started():
    # Arrange
    t = _task(created_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started + created_to_done both None; the
    # started_to_done leg is still valid.
    assert d["created_to_started"] is None


def test_task_durations_missing_created_at_leaves_two_fields_none_created_to_done():
    # Arrange
    t = _task(created_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started + created_to_done both None; the
    # started_to_done leg is still valid.
    assert d["created_to_done"] is None


def test_task_durations_missing_created_at_leaves_two_fields_none_started_to_done():
    # Arrange
    t = _task(created_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started + created_to_done both None; the
    # started_to_done leg is still valid.
    assert d["started_to_done"] == 30 * 60


def test_task_durations_missing_started_at_leaves_two_fields_none_created_to_started():
    # Arrange
    t = _task(started_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — both legs touching ``started`` go None; created_to_done
    # is independent of ``started`` and stays valid.
    assert d["created_to_started"] is None


def test_task_durations_missing_started_at_leaves_two_fields_none_started_to_done():
    # Arrange
    t = _task(started_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — both legs touching ``started`` go None; created_to_done
    # is independent of ``started`` and stays valid.
    assert d["started_to_done"] is None


def test_task_durations_missing_started_at_leaves_two_fields_none_created_to_done():
    # Arrange
    t = _task(started_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — both legs touching ``started`` go None; created_to_done
    # is independent of ``started`` and stays valid.
    assert d["created_to_done"] == 40 * 60


def test_task_durations_missing_completed_at_leaves_two_fields_none_created_to_started():
    # Arrange
    t = _task(done_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started survives; the two ``done`` legs go None.
    assert d["created_to_started"] == 10 * 60


def test_task_durations_missing_completed_at_leaves_two_fields_none_started_to_done():
    # Arrange
    t = _task(done_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started survives; the two ``done`` legs go None.
    assert d["started_to_done"] is None


def test_task_durations_missing_completed_at_leaves_two_fields_none_created_to_done():
    # Arrange
    t = _task(done_min_ago=None)
    # Act
    d = task_durations(t)
    # Assert — created_to_started survives; the two ``done`` legs go None.
    assert d["created_to_done"] is None


def test_task_durations_handles_z_suffix_iso_strings_created_to_started():
    # Arrange — emulate a writer that wrote "Z" instead of "+00:00".
    t = {
        "created_at": "2026-06-14T11:00:00Z",
        "_log_meta": {
            "started_at": "2026-06-14T11:10:00Z",
            "completed_at": "2026-06-14T11:40:00Z",
        },
    }
    # Act
    d = task_durations(t)
    # Assert
    assert d["created_to_started"] == 10 * 60


def test_task_durations_handles_z_suffix_iso_strings_started_to_done():
    # Arrange — emulate a writer that wrote "Z" instead of "+00:00".
    t = {
        "created_at": "2026-06-14T11:00:00Z",
        "_log_meta": {
            "started_at": "2026-06-14T11:10:00Z",
            "completed_at": "2026-06-14T11:40:00Z",
        },
    }
    # Act
    d = task_durations(t)
    # Assert
    assert d["started_to_done"] == 30 * 60


# ---------------------------------------------------------------------------
# compute_timing — window filter + aggregates.
# ---------------------------------------------------------------------------


def test_compute_timing_excludes_tasks_completed_outside_window_n_tasks_in_window():
    # Arrange — one task done 5 days ago (in window), one done 60 days
    # ago (outside the default 30-day window).
    in_window = _task(
        id_="t-in",
        created_min_ago=5 * 24 * 60 + 30,
        started_min_ago=5 * 24 * 60 + 20,
        done_min_ago=5 * 24 * 60,
    )
    out_of_window = _task(
        id_="t-out",
        created_min_ago=60 * 24 * 60 + 30,
        started_min_ago=60 * 24 * 60 + 20,
        done_min_ago=60 * 24 * 60,
        agent="agent-beta",
    )
    # Act
    result = compute_timing([in_window, out_of_window], window_days=30, now=_NOW)
    # Assert — only the in-window task contributes.
    assert result["n_tasks_in_window"] == 1


def test_compute_timing_excludes_tasks_completed_outside_window_per_agent_contains():
    # Arrange — one task done 5 days ago (in window), one done 60 days
    # ago (outside the default 30-day window).
    in_window = _task(
        id_="t-in",
        created_min_ago=5 * 24 * 60 + 30,
        started_min_ago=5 * 24 * 60 + 20,
        done_min_ago=5 * 24 * 60,
    )
    out_of_window = _task(
        id_="t-out",
        created_min_ago=60 * 24 * 60 + 30,
        started_min_ago=60 * 24 * 60 + 20,
        done_min_ago=60 * 24 * 60,
        agent="agent-beta",
    )
    # Act
    result = compute_timing([in_window, out_of_window], window_days=30, now=_NOW)
    # Assert — only the in-window task contributes.
    assert "agent-alpha" in result["per_agent"]


def test_compute_timing_excludes_tasks_completed_outside_window_per_agent_excludes():
    # Arrange — one task done 5 days ago (in window), one done 60 days
    # ago (outside the default 30-day window).
    in_window = _task(
        id_="t-in",
        created_min_ago=5 * 24 * 60 + 30,
        started_min_ago=5 * 24 * 60 + 20,
        done_min_ago=5 * 24 * 60,
    )
    out_of_window = _task(
        id_="t-out",
        created_min_ago=60 * 24 * 60 + 30,
        started_min_ago=60 * 24 * 60 + 20,
        done_min_ago=60 * 24 * 60,
        agent="agent-beta",
    )
    # Act
    result = compute_timing([in_window, out_of_window], window_days=30, now=_NOW)
    # Assert — only the in-window task contributes.
    assert "agent-beta" not in result["per_agent"]


def test_compute_timing_per_agent_only_lists_agents_with_done_tasks_set():
    # Arrange — two agents, both in window.
    a = _task(id_="a", agent="agent-alpha")
    b = _task(id_="b", agent="agent-beta")
    # Act
    result = compute_timing([a, b], window_days=30, now=_NOW)
    # Assert
    assert set(result["per_agent"].keys()) == {"agent-alpha", "agent-beta"}


def test_compute_timing_per_agent_only_lists_agents_with_done_tasks_n_tasks_done():
    # Arrange — two agents, both in window.
    a = _task(id_="a", agent="agent-alpha")
    b = _task(id_="b", agent="agent-beta")
    # Act
    result = compute_timing([a, b], window_days=30, now=_NOW)
    # Assert
    assert result["per_agent"]["agent-alpha"]["n_tasks_done"] == 1


def test_compute_timing_per_agent_only_lists_agents_with_done_tasks_n_tasks_done_2():
    # Arrange — two agents, both in window.
    a = _task(id_="a", agent="agent-alpha")
    b = _task(id_="b", agent="agent-beta")
    # Act
    result = compute_timing([a, b], window_days=30, now=_NOW)
    # Assert
    assert result["per_agent"]["agent-beta"]["n_tasks_done"] == 1


def test_compute_timing_per_group_rolls_null_group_into_ungrouped_key_per_group_contains():
    # Arrange — one task with group=None, one with group="g1".
    grouped = _task(id_="g", group="g1")
    ungrouped = _task(id_="u", group=None)
    # Act
    result = compute_timing([grouped, ungrouped], window_days=30, now=_NOW)
    # Assert
    assert "g1" in result["per_group"]


def test_compute_timing_per_group_rolls_null_group_into_ungrouped_key_per_group_contains_2():
    # Arrange — one task with group=None, one with group="g1".
    grouped = _task(id_="g", group="g1")
    ungrouped = _task(id_="u", group=None)
    # Act
    result = compute_timing([grouped, ungrouped], window_days=30, now=_NOW)
    # Assert
    assert "<ungrouped>" in result["per_group"]


def test_compute_timing_per_group_rolls_null_group_into_ungrouped_key_n_tasks_done():
    # Arrange — one task with group=None, one with group="g1".
    grouped = _task(id_="g", group="g1")
    ungrouped = _task(id_="u", group=None)
    # Act
    result = compute_timing([grouped, ungrouped], window_days=30, now=_NOW)
    # Assert
    assert result["per_group"]["<ungrouped>"]["n_tasks_done"] == 1


def test_compute_timing_per_group_rolls_null_group_into_ungrouped_key_n_tasks_done_2():
    # Arrange — one task with group=None, one with group="g1".
    grouped = _task(id_="g", group="g1")
    ungrouped = _task(id_="u", group=None)
    # Act
    result = compute_timing([grouped, ungrouped], window_days=30, now=_NOW)
    # Assert
    assert result["per_group"]["g1"]["n_tasks_done"] == 1


def test_compute_timing_per_group_rolls_empty_string_group_into_ungrouped_per_group_contains():
    # Arrange — empty-string group should land in ungrouped too.
    t = _task(group="")
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert "<ungrouped>" in result["per_group"]


def test_compute_timing_per_group_rolls_empty_string_group_into_ungrouped_per_group_excludes():
    # Arrange — empty-string group should land in ungrouped too.
    t = _task(group="")
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert "" not in result["per_group"]


def test_compute_timing_median_and_p95_on_small_distribution_n_tasks_done():
    # Arrange — five tasks for ONE agent with started_to_done values
    # of 10, 20, 30, 40, 100 seconds. Median = 30; p95 (nearest-rank,
    # n=5, idx=ceil(0.95*5)-1=4) = 100.
    tasks = []
    for i, (started, done) in enumerate([(10, 0), (20, 0), (30, 0), (40, 0), (100, 0)]):
        # done_min_ago = 1 minute ago; started_min_ago = 1m + started_seconds.
        tasks.append(
            _task(
                id_=f"t{i}",
                created_min_ago=2 + started / 60.0,
                started_min_ago=1 + started / 60.0,
                done_min_ago=1,
                agent="agent-uno",
            )
        )
    # Act
    result = compute_timing(tasks, window_days=30, now=_NOW)
    agg = result["per_agent"]["agent-uno"]
    # Assert
    assert agg["n_tasks_done"] == 5


def test_compute_timing_median_and_p95_on_small_distribution_median_started_to_done_s():
    # Arrange — five tasks for ONE agent with started_to_done values
    # of 10, 20, 30, 40, 100 seconds. Median = 30; p95 (nearest-rank,
    # n=5, idx=ceil(0.95*5)-1=4) = 100.
    tasks = []
    for i, (started, done) in enumerate([(10, 0), (20, 0), (30, 0), (40, 0), (100, 0)]):
        # done_min_ago = 1 minute ago; started_min_ago = 1m + started_seconds.
        tasks.append(
            _task(
                id_=f"t{i}",
                created_min_ago=2 + started / 60.0,
                started_min_ago=1 + started / 60.0,
                done_min_ago=1,
                agent="agent-uno",
            )
        )
    # Act
    result = compute_timing(tasks, window_days=30, now=_NOW)
    agg = result["per_agent"]["agent-uno"]
    # Assert
    assert agg["median_started_to_done_s"] == 30


def test_compute_timing_median_and_p95_on_small_distribution_p95_started_to_done_s():
    # Arrange — five tasks for ONE agent with started_to_done values
    # of 10, 20, 30, 40, 100 seconds. Median = 30; p95 (nearest-rank,
    # n=5, idx=ceil(0.95*5)-1=4) = 100.
    tasks = []
    for i, (started, done) in enumerate([(10, 0), (20, 0), (30, 0), (40, 0), (100, 0)]):
        # done_min_ago = 1 minute ago; started_min_ago = 1m + started_seconds.
        tasks.append(
            _task(
                id_=f"t{i}",
                created_min_ago=2 + started / 60.0,
                started_min_ago=1 + started / 60.0,
                done_min_ago=1,
                agent="agent-uno",
            )
        )
    # Act
    result = compute_timing(tasks, window_days=30, now=_NOW)
    agg = result["per_agent"]["agent-uno"]
    # Assert
    assert agg["p95_started_to_done_s"] == 100


def test_compute_timing_per_project_aggregates_correctly_per_project_contains():
    # Arrange — two tasks on the same project.
    a = _task(id_="a", project="proj-foo")
    b = _task(id_="b", project="proj-foo")
    # Act
    result = compute_timing([a, b], window_days=30, now=_NOW)
    # Assert
    assert "proj-foo" in result["per_project"]


def test_compute_timing_per_project_aggregates_correctly_n_tasks_done():
    # Arrange — two tasks on the same project.
    a = _task(id_="a", project="proj-foo")
    b = _task(id_="b", project="proj-foo")
    # Act
    result = compute_timing([a, b], window_days=30, now=_NOW)
    # Assert
    assert result["per_project"]["proj-foo"]["n_tasks_done"] == 2


def test_compute_timing_emits_window_envelope_fields_window_days():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["window_days"] == 7


def test_compute_timing_emits_window_envelope_fields_n_tasks_in_window():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["n_tasks_in_window"] == 0


def test_compute_timing_emits_window_envelope_fields_n_tasks_missing_timestamps():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["n_tasks_missing_timestamps"] == 0


def test_compute_timing_emits_window_envelope_fields_per_agent():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["per_agent"] == {}


def test_compute_timing_emits_window_envelope_fields_per_project():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["per_project"] == {}


def test_compute_timing_emits_window_envelope_fields_per_group():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["per_group"] == {}


def test_compute_timing_emits_window_envelope_fields_window_start():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["window_start"] == expected_start


def test_compute_timing_emits_window_envelope_fields_window_end():
    # Arrange — empty input still emits the envelope so the FE can show
    # "no data in last N days" without crashing.
    # Act
    result = compute_timing([], window_days=7, now=_NOW)
    # Assert — envelope fields all present + window math correct.
    expected_start = (_NOW - _dt.timedelta(days=7)).isoformat()
    assert result["window_end"] == _NOW.isoformat()


def test_compute_timing_counts_done_tasks_with_missing_metadata_n_tasks_in_window():
    # Arrange — a task with status=done but no _log_meta.completed_at.
    # The data hole should be surfaced via n_tasks_missing_timestamps
    # rather than silently dropped.
    broken = _task(done_min_ago=None, status="done")
    # Act
    result = compute_timing([broken], window_days=30, now=_NOW)
    # Assert
    assert result["n_tasks_in_window"] == 0


def test_compute_timing_counts_done_tasks_with_missing_metadata_n_tasks_missing_timestamps():
    # Arrange — a task with status=done but no _log_meta.completed_at.
    # The data hole should be surfaced via n_tasks_missing_timestamps
    # rather than silently dropped.
    broken = _task(done_min_ago=None, status="done")
    # Act
    result = compute_timing([broken], window_days=30, now=_NOW)
    # Assert
    assert result["n_tasks_missing_timestamps"] == 1


def test_compute_timing_skips_task_with_no_agent_from_per_agent_per_agent():
    # Arrange — task with agent=None should not contribute to
    # per_agent, but DOES contribute to n_tasks_in_window + per_group.
    t = _task(agent=None)
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert result["per_agent"] == {}


def test_compute_timing_skips_task_with_no_agent_from_per_agent_n_tasks_in_window():
    # Arrange — task with agent=None should not contribute to
    # per_agent, but DOES contribute to n_tasks_in_window + per_group.
    t = _task(agent=None)
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert result["n_tasks_in_window"] == 1


def test_compute_timing_skips_task_with_no_agent_from_per_agent_get():
    # Arrange — task with agent=None should not contribute to
    # per_agent, but DOES contribute to n_tasks_in_window + per_group.
    t = _task(agent=None)
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert result["per_group"].get("g1", {}).get("n_tasks_done") == 1


def test_compute_timing_assignee_legacy_falls_back_to_agent_bucket():
    # Arrange — legacy task uses "assignee" instead of "agent".
    t = _task(agent=None)
    t["assignee"] = "legacy-agent"
    # Act
    result = compute_timing([t], window_days=30, now=_NOW)
    # Assert
    assert "legacy-agent" in result["per_agent"]


def test_compute_timing_invalid_window_days_falls_back_to_default():
    # Arrange — negative window should fall back to the 30-day default.
    t = _task()
    # Act
    result = compute_timing([t], window_days=-1, now=_NOW)
    # Assert
    assert result["window_days"] == 30


# EOF
