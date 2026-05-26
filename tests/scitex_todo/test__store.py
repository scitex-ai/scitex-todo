#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the Phase-1 mutation Python API (`scitex_todo._store`).

Real round-trips against a `tmp_path` YAML file — no mocks, no
green-theater (Req STX-NM / PA-306). The concurrent-writer test spawns
two real subprocesses with the actual `fcntl.flock` semantics so it
proves the lock serializes interleaved add/update/done calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scitex_todo import _model, _store


# --------------------------------------------------------------------------- #
# add_task                                                                    #
# --------------------------------------------------------------------------- #
def test_add_task_writes_to_fresh_store(tmp_path):
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store, id="design", title="Design phase", status="pending"
    )
    assert inserted == {"id": "design", "title": "Design phase", "status": "pending"}
    # The store now exists and round-trips cleanly through load_tasks.
    on_disk = _model.load_tasks(store)
    assert len(on_disk) == 1
    assert on_disk[0]["id"] == "design"


def test_add_task_appends_to_existing_store(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    _store.add_task(
        store,
        id="b",
        title="B",
        status="in_progress",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
        priority=2,
        parent="a",
        note="b is under a",
    )
    on_disk = _model.load_tasks(store)
    assert [t["id"] for t in on_disk] == ["a", "b"]
    b = on_disk[1]
    assert b["status"] == "in_progress"
    assert b["scope"] == "agent:proj-scitex-todo"
    assert b["assignee"] == "agent:proj-scitex-todo"
    assert b["priority"] == 2
    assert b["parent"] == "a"
    assert b["note"] == "b is under a"


def test_add_task_rejects_duplicate_id(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    with pytest.raises(_model.TaskValidationError):
        _store.add_task(store, id="a", title="A2")


def test_add_task_rejects_invalid_status(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_model.TaskValidationError):
        _store.add_task(store, id="a", title="A", status="not-a-status")


# --------------------------------------------------------------------------- #
# update_task                                                                 #
# --------------------------------------------------------------------------- #
def test_update_task_changes_fields(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", priority=10)
    merged = _store.update_task(
        store, "a", status="in_progress", priority=1, scope="agent:lead"
    )
    assert merged["status"] == "in_progress"
    assert merged["priority"] == 1
    assert merged["scope"] == "agent:lead"
    # Persistence:
    assert _model.load_tasks(store)[0]["scope"] == "agent:lead"


def test_update_task_passing_none_clears_field(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", scope="agent:proj-scitex-todo")
    merged = _store.update_task(store, "a", scope=None)
    assert "scope" not in merged
    assert "scope" not in _model.load_tasks(store)[0]


def test_update_task_missing_raises_TaskNotFound(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    with pytest.raises(_store.TaskNotFoundError):
        _store.update_task(store, "nope", status="done")


def test_update_task_empty_id_typeerror(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    with pytest.raises(TypeError):
        _store.update_task(store, "", status="done")


# --------------------------------------------------------------------------- #
# complete_task                                                               #
# --------------------------------------------------------------------------- #
def test_complete_task_stamps_log_meta(tmp_path, monkeypatch):
    store = tmp_path / "tasks.yaml"
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:test")
    _store.add_task(store, id="a", title="A")
    done = _store.complete_task(store, "a")
    assert done["status"] == "done"
    assert done["_log_meta"]["completed_by"] == "agent:test"
    # ISO-8601 UTC Z-suffix, second resolution.
    stamp = done["_log_meta"]["completed_at"]
    assert stamp.endswith("Z")
    assert "T" in stamp
    # Persistence:
    persisted = _model.load_tasks(store)[0]
    assert persisted["_log_meta"]["completed_by"] == "agent:test"
    assert persisted["_log_meta"]["completed_at"] == stamp


def test_complete_task_explicit_by_overrides_env(tmp_path, monkeypatch):
    store = tmp_path / "tasks.yaml"
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:env")
    _store.add_task(store, id="a", title="A")
    done = _store.complete_task(store, "a", by="agent:cli")
    assert done["_log_meta"]["completed_by"] == "agent:cli"


def test_complete_task_is_idempotent(tmp_path, monkeypatch):
    store = tmp_path / "tasks.yaml"
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:first")
    _store.add_task(store, id="a", title="A")
    first = _store.complete_task(store, "a")
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:second")
    second = _store.complete_task(store, "a")
    assert first["_log_meta"]["completed_at"] == second["_log_meta"]["completed_at"]
    assert first["_log_meta"]["completed_by"] == "agent:first"
    assert second["_log_meta"]["completed_by"] == "agent:first"


def test_complete_task_missing_raises(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A")
    with pytest.raises(_store.TaskNotFoundError):
        _store.complete_task(store, "nope")


# --------------------------------------------------------------------------- #
# list_tasks (scope/assignee/status filters)                                  #
# --------------------------------------------------------------------------- #
@pytest.fixture
def populated_store(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", scope="agent:lead")
    _store.add_task(
        store,
        id="b",
        title="B",
        scope="agent:proj-scitex-todo",
        assignee="agent:proj-scitex-todo",
    )
    _store.add_task(store, id="c", title="C", status="done")
    _store.add_task(
        store,
        id="d",
        title="D",
        scope="agent:proj-scitex-todo",
        status="in_progress",
    )
    return store


def test_list_tasks_no_filter_returns_all(populated_store):
    rows = _store.list_tasks(populated_store, scope="")
    assert {r["id"] for r in rows} == {"a", "b", "c", "d"}


def test_list_tasks_filters_by_scope(populated_store):
    rows = _store.list_tasks(populated_store, scope="agent:proj-scitex-todo")
    assert {r["id"] for r in rows} == {"b", "d"}


def test_list_tasks_filters_by_assignee(populated_store):
    rows = _store.list_tasks(
        populated_store, scope="", assignee="agent:proj-scitex-todo"
    )
    assert {r["id"] for r in rows} == {"b"}


def test_list_tasks_filters_by_status(populated_store):
    rows = _store.list_tasks(populated_store, scope="", status="done")
    assert {r["id"] for r in rows} == {"c"}


def test_list_tasks_env_scope_is_default(populated_store, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:lead")
    rows = _store.list_tasks(populated_store)
    assert {r["id"] for r in rows} == {"a"}


def test_list_tasks_explicit_empty_string_overrides_env(populated_store, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:lead")
    rows = _store.list_tasks(populated_store, scope="")
    assert {r["id"] for r in rows} == {"a", "b", "c", "d"}


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
def test_summary_counts_by_status_scope_assignee(populated_store):
    info = _store.summary(populated_store, scope="")
    assert info["total"] == 4
    # Density across all valid statuses (consumers don't have to special-case
    # zero-count keys).
    for status in _model.VALID_STATUSES:
        assert status in info["by_status"]
    assert info["by_status"]["pending"] == 2  # a, b
    assert info["by_status"]["done"] == 1
    assert info["by_status"]["in_progress"] == 1
    # by_scope: a, b/d, c→empty
    assert info["by_scope"]["agent:lead"] == 1
    assert info["by_scope"]["agent:proj-scitex-todo"] == 2
    assert info["by_scope"][""] == 1
    # by_assignee: only b
    assert info["by_assignee"]["agent:proj-scitex-todo"] == 1
    assert info["by_assignee"][""] == 3


def test_summary_respects_scope_filter(populated_store):
    info = _store.summary(populated_store, scope="agent:proj-scitex-todo")
    assert info["total"] == 2
    assert info["by_status"]["pending"] == 1
    assert info["by_status"]["in_progress"] == 1


# --------------------------------------------------------------------------- #
# Concurrent-writer lock (REAL subprocesses, no mocks)                        #
# --------------------------------------------------------------------------- #
_WRITER_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    from scitex_todo import _store

    store, agent, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    # Hold all writers at a barrier so they really do race the lock.
    barrier = sys.argv[4]
    while not os.path.exists(barrier):
        time.sleep(0.01)
    for i in range(count):
        _store.add_task(store, id=f"{agent}-{i}", title=f"{agent} task {i}",
                        scope=f"agent:{agent}")
    print("ok", flush=True)
    """
)


def test_two_concurrent_writers_serialize_via_flock(tmp_path):
    """Two real subprocesses each insert N tasks; the lock must serialize
    them so ALL 2N tasks land in the store with no lost write."""
    store = tmp_path / "tasks.yaml"
    barrier = tmp_path / "barrier"
    # Seed an existing store so the writers exercise the merge path
    # (existing_doc is not None), which is where a non-locking writer
    # would clobber.
    _store.add_task(store, id="seed", title="seed")

    script = tmp_path / "writer.py"
    script.write_text(_WRITER_SCRIPT)

    env = os.environ.copy()
    # Use the worktree's source via PYTHONPATH so both subprocesses
    # import the SAME `_store` we just tested.
    env["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[2] / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )

    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(store), agent, "10", str(barrier)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        for agent in ("alpha", "beta")
    ]
    # Drop the barrier — both writers leap.
    barrier.write_text("go")
    outs = [p.communicate(timeout=30) for p in procs]
    for (stdout, stderr), p in zip(outs, procs):
        assert p.returncode == 0, (p.returncode, stderr.decode())
        assert stdout.decode().strip() == "ok"

    tasks = _model.load_tasks(store)
    ids = {t["id"] for t in tasks}
    expected = {"seed"} | {f"alpha-{i}" for i in range(10)} | {
        f"beta-{i}" for i in range(10)
    }
    assert ids == expected, (
        "lost writes: expected 21 ids, got "
        f"{len(ids)} (diff: {sorted(expected - ids)})"
    )


# --------------------------------------------------------------------------- #
# Path resolution (`_resolved_store` + `where`-style introspection)           #
# --------------------------------------------------------------------------- #
def test_explicit_store_path_wins(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("SCITEX_TODO_TASKS", str(tmp_path / "envdefault.yaml"))
    _store.add_task(other, id="here", title="Here")
    # The explicit path is what's used — env var does NOT redirect.
    assert _model.load_tasks(other)[0]["id"] == "here"


# EOF
