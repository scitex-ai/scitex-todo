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
    assert isinstance(LocalFileSync("/tmp/_unused.yaml"), TaskSyncPort)


def test_in_process_pubsub_satisfies_notification_port():
    assert isinstance(InProcessPubSub(), NotificationPort)


def test_null_liveness_satisfies_liveness_port():
    assert isinstance(NullLiveness(), LivenessPort)


def test_open_acl_satisfies_identity_acl_port():
    assert isinstance(OpenACL(), IdentityACLPort)


# ---------------------------------------------------------------------------
# LocalFileSync — load / save / reload_if_changed behaviour.
# ---------------------------------------------------------------------------


def _write_tasks_yaml(tmp_path, body: str):
    p = tmp_path / "tasks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_local_file_sync_load_returns_validated_tasks(tmp_path):
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    tasks = sync.load()
    assert tasks[0]["id"] == "a"


def test_local_file_sync_save_round_trips_through_ruamel(tmp_path):
    p = _write_tasks_yaml(
        tmp_path,
        "# preserved comment\n"
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    tasks = sync.load()
    tasks[0]["status"] = "done"
    sync.save(tasks)
    # Comment must survive the round-trip.
    assert "# preserved comment" in p.read_text()


def test_local_file_sync_reload_detects_external_mutation(tmp_path):
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()  # baseline mtime captured

    # External write — bump mtime via a fresh write.
    import os
    import time
    time.sleep(0.01)  # ensure mtime tick
    os.utime(p, None)
    assert sync.reload_if_changed() is True


def test_local_file_sync_reload_returns_false_when_quiet(tmp_path):
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()
    assert sync.reload_if_changed() is False


# ---------------------------------------------------------------------------
# InProcessPubSub — literal + suffix-* glob + idempotent subscribe + handler-
# exception isolation.
# ---------------------------------------------------------------------------


def test_pubsub_literal_channel_match_delivers_to_subscriber():
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:x/y", seen.append)
    bus.publish("scitex-todo:task:x/y", {"task_id": "x/y"})
    assert seen == [{"task_id": "x/y"}]


def test_pubsub_suffix_glob_matches_every_task_in_project():
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:demo/*", seen.append)
    bus.publish("scitex-todo:task:demo/foo", {"task_id": "demo/foo"})
    bus.publish("scitex-todo:task:demo/bar", {"task_id": "demo/bar"})
    bus.publish("scitex-todo:task:other/baz", {"task_id": "other/baz"})
    assert [p["task_id"] for p in seen] == ["demo/foo", "demo/bar"]


def test_pubsub_subscribe_is_idempotent():
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("a", seen.append)
    bus.subscribe("a", seen.append)  # duplicate
    bus.publish("a", {})
    assert len(seen) == 1


def test_pubsub_handler_exception_does_not_break_other_handlers():
    bus = InProcessPubSub()
    def boom(_):
        raise RuntimeError("intentional")
    seen: list[dict] = []
    bus.subscribe("c", boom)
    bus.subscribe("c", seen.append)
    # The bad handler raises (warns via warnings.warn) but other handlers
    # still see the event.
    with pytest.warns(UserWarning):
        bus.publish("c", {"ok": True})
    assert seen == [{"ok": True}]


# ---------------------------------------------------------------------------
# NullLiveness + OpenACL — defaults are trivial but must satisfy contracts.
# ---------------------------------------------------------------------------


def test_null_liveness_returns_empty_list():
    assert NullLiveness().list_agents() == []


def test_open_acl_allows_read():
    assert OpenACL().can_read("anyone", {"id": "x"}) is True


def test_open_acl_allows_write_any_field():
    assert OpenACL().can_write("anyone", {"id": "x"}, "status") is True
