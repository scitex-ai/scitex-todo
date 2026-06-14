#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the four extension ports + the default adapters.

No mocks (STX-NM / PA-306). Real tmp paths for the file-sync adapter,
real in-process callbacks for the pubsub adapter, real dict payloads
for the ACL + liveness adapters. AAA pattern; descriptive names; one
core assertion per test where practical.
"""

from __future__ import annotations

import pytest

from scitex_todo._adapters import (
    InProcessPubSub,
    LocalFileSync,
    NullLiveness,
    OpenACL,
)
from scitex_todo._ports import (
    IdentityACLPort,
    LivenessPort,
    NotificationPort,
    TaskSyncPort,
)


# ---------------------------------------------------------------------------
# Protocol conformance — every default adapter satisfies its Protocol via
# isinstance() (the Protocols are @runtime_checkable).
# ---------------------------------------------------------------------------


def test_local_file_sync_satisfies_task_sync_port():
    # Arrange
    sync = LocalFileSync("/tmp/_unused.yaml")
    # Act
    is_port = isinstance(sync, TaskSyncPort)
    # Assert
    assert is_port


def test_in_process_pubsub_satisfies_notification_port():
    # Arrange
    bus = InProcessPubSub()
    # Act
    is_port = isinstance(bus, NotificationPort)
    # Assert
    assert is_port


def test_null_liveness_satisfies_liveness_port():
    # Arrange
    liveness = NullLiveness()
    # Act
    is_port = isinstance(liveness, LivenessPort)
    # Assert
    assert is_port


def test_open_acl_satisfies_identity_acl_port():
    # Arrange
    acl = OpenACL()
    # Act
    is_port = isinstance(acl, IdentityACLPort)
    # Assert
    assert is_port


# ---------------------------------------------------------------------------
# LocalFileSync — load / save / reload_if_changed behaviour.
# ---------------------------------------------------------------------------


def _write_tasks_yaml(tmp_path, body: str):
    p = tmp_path / "tasks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_local_file_sync_load_returns_validated_tasks(tmp_path):
    # Arrange
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    # Act
    tasks = sync.load()
    # Assert
    assert tasks[0]["id"] == "a"


def test_local_file_sync_save_round_trips_through_ruamel(tmp_path):
    # Arrange
    p = _write_tasks_yaml(
        tmp_path,
        "# preserved comment\n"
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    tasks = sync.load()
    tasks[0]["status"] = "done"
    # Act
    sync.save(tasks)
    # Assert — comment must survive the round-trip.
    assert "# preserved comment" in p.read_text()


def test_local_file_sync_reload_detects_external_mutation(tmp_path):
    # Arrange
    import os
    import time

    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()  # baseline mtime captured
    # Act — external write that bumps mtime.
    time.sleep(0.01)  # ensure mtime tick
    os.utime(p, None)
    # Assert
    assert sync.reload_if_changed() is True


def test_local_file_sync_reload_returns_false_when_quiet(tmp_path):
    # Arrange
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()
    # Act
    changed = sync.reload_if_changed()
    # Assert — no external write means reload-detection is a no-op.
    assert changed is False


# ---------------------------------------------------------------------------
# InProcessPubSub — literal + suffix-* glob + idempotent subscribe + handler-
# exception isolation.
# ---------------------------------------------------------------------------


def test_pubsub_literal_channel_match_delivers_to_subscriber():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:x/y", seen.append)
    # Act
    bus.publish("scitex-todo:task:x/y", {"task_id": "x/y"})
    # Assert
    assert seen == [{"task_id": "x/y"}]


def test_pubsub_suffix_glob_matches_every_task_in_project():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:demo/*", seen.append)
    # Act
    bus.publish("scitex-todo:task:demo/foo", {"task_id": "demo/foo"})
    bus.publish("scitex-todo:task:demo/bar", {"task_id": "demo/bar"})
    bus.publish("scitex-todo:task:other/baz", {"task_id": "other/baz"})
    # Assert
    assert [p["task_id"] for p in seen] == ["demo/foo", "demo/bar"]


def test_pubsub_subscribe_is_idempotent():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("a", seen.append)
    bus.subscribe("a", seen.append)  # duplicate
    # Act
    bus.publish("a", {})
    # Assert
    assert len(seen) == 1


def test_pubsub_bad_handler_emits_user_warning():
    """Bad handler emits warnings.warn on publish (TQ007 split — was bundled
    with the other-handlers-still-fire assertion in a single test)."""
    # Arrange
    bus = InProcessPubSub()

    def boom(_):
        raise RuntimeError("intentional")

    bus.subscribe("c", boom)
    # Act
    warn_ctx = pytest.warns(UserWarning)
    # Assert — pytest.warns is the assertion: warning fires on publish.
    with warn_ctx:
        bus.publish("c", {"ok": True})


def test_pubsub_handler_exception_does_not_break_other_handlers():
    # Arrange
    bus = InProcessPubSub()

    def boom(_):
        raise RuntimeError("intentional")

    seen: list[dict] = []
    bus.subscribe("c", boom)
    bus.subscribe("c", seen.append)
    # Act — the bad handler raises (warns via warnings.warn); suppress the
    # warning here since the warning is asserted by the companion test
    # above. Other handlers must still see the event.
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        bus.publish("c", {"ok": True})
    # Assert
    assert seen == [{"ok": True}]


# ---------------------------------------------------------------------------
# NullLiveness + OpenACL — defaults are trivial but must satisfy contracts.
# ---------------------------------------------------------------------------


def test_null_liveness_returns_empty_list():
    # Arrange
    liveness = NullLiveness()
    # Act
    agents = liveness.list_agents()
    # Assert
    assert agents == []


def test_open_acl_allows_read():
    # Arrange
    acl = OpenACL()
    # Act
    allowed = acl.can_read("anyone", {"id": "x"})
    # Assert
    assert allowed is True


def test_open_acl_allows_write_any_field():
    # Arrange
    acl = OpenACL()
    # Act
    allowed = acl.can_write("anyone", {"id": "x"}, "status")
    # Assert
    assert allowed is True
