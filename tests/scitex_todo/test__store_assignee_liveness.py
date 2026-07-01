#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assignee-liveness feature — Slice 2: surface assignee liveness at assign time.

Real round-trips against a ``tmp_path`` YAML store — no mocks (Req STX-NM /
PA-306). Covers that ``add_task`` / ``reassign_task`` / assignee-setting
``update_task`` attach an ``assignee_liveness`` block classifying the
assignee as alive / stale / unknown, and that no block is attached when there
is no assignee to classify (structurally impossible for add_task, which
requires an owner, but exercised for update_task).
"""

from __future__ import annotations

import datetime as _dt

from scitex_todo import _store, _users


def _iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _register_seen(store, name, *, seconds_ago):
    """Register ``name`` and stamp its ``last_seen`` ``seconds_ago`` in the past."""
    user = _users.register_user(kind="agent", names=[name], store=store)
    seen = _iso(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago))
    # Stamp directly via the registry write path (touch_user always stamps
    # "now"; here we need a controlled age, so set it through set-like write).
    users = _users.load_users(store=store)
    for u in users:
        if u.id == user.id:
            u.last_seen = seen
    # Persist the controlled stamp using the same round-trip writer.
    from scitex_todo._users import _store as _users_store

    from scitex_todo._model import _store_lock
    from pathlib import Path

    path = Path(store)
    with _store_lock(path):
        rows = _users_store._load_users_section(path)
        for row in rows:
            if row.get("id") == user.id:
                row["last_seen"] = seen
        _users_store._save_users_unlocked(rows, path)
    return user


# --------------------------------------------------------------------------- #
# add_task attaches assignee_liveness                                         #
# --------------------------------------------------------------------------- #
def test_add_task_alive_assignee(tmp_path):
    store = tmp_path / "tasks.yaml"
    _register_seen(store, "fresh-agent", seconds_ago=60)  # 1 min ago
    result = _store.add_task(
        store,
        id="t1",
        title="T",
        assignee="fresh-agent",
        created_by="agent:test-suite",
    )
    assert "assignee_liveness" in result
    live = result["assignee_liveness"]
    assert live["status"] == "alive"
    assert live["last_seen"] is not None
    assert isinstance(live["age_seconds"], int)


def test_add_task_stale_assignee(tmp_path):
    store = tmp_path / "tasks.yaml"
    _register_seen(store, "old-agent", seconds_ago=3600)  # 1 hour ago
    result = _store.add_task(
        store,
        id="t1",
        title="T",
        assignee="old-agent",
        created_by="agent:test-suite",
    )
    assert result["assignee_liveness"]["status"] == "stale"


def test_add_task_unknown_assignee_unregistered(tmp_path):
    store = tmp_path / "tasks.yaml"
    # Assignee never registered → resolves to None → unknown.
    result = _store.add_task(
        store,
        id="t1",
        title="T",
        assignee="ghost-agent",
        created_by="agent:test-suite",
    )
    live = result["assignee_liveness"]
    assert live["status"] == "unknown"
    assert live["last_seen"] is None
    assert live["age_seconds"] is None


def test_add_task_unknown_assignee_never_seen(tmp_path):
    store = tmp_path / "tasks.yaml"
    # Registered but never touched the store → no last_seen → unknown.
    _users.register_user(kind="agent", names=["idle-agent"], store=store)
    result = _store.add_task(
        store,
        id="t1",
        title="T",
        assignee="idle-agent",
        created_by="agent:test-suite",
    )
    assert result["assignee_liveness"]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# reassign_task / update_task attach assignee_liveness for the new owner      #
# --------------------------------------------------------------------------- #
def test_reassign_task_reports_new_owner_liveness(tmp_path):
    store = tmp_path / "tasks.yaml"
    _register_seen(store, "new-owner", seconds_ago=30)
    _store.add_task(
        store, id="t1", title="T", assignee="orig", created_by="agent:test-suite"
    )
    out = _store.reassign_task(store, "t1", "new-owner", by="agent:test-suite")
    assert out["changed"] is True
    assert out["assignee_liveness"]["status"] == "alive"


def test_update_task_setting_assignee_reports_liveness(tmp_path):
    store = tmp_path / "tasks.yaml"
    _register_seen(store, "u-owner", seconds_ago=30)
    _store.add_task(
        store, id="t1", title="T", assignee="orig", created_by="agent:test-suite"
    )
    merged = _store.update_task(store, "t1", assignee="u-owner")
    assert merged["assignee_liveness"]["status"] == "alive"


def test_update_task_without_assignee_change_has_no_liveness(tmp_path):
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store, id="t1", title="T", assignee="orig", created_by="agent:test-suite"
    )
    merged = _store.update_task(store, "t1", note="just a note")
    assert "assignee_liveness" not in merged

# EOF
