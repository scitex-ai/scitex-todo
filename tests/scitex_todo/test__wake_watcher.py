#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scitex_todo._wake_watcher (P3b, lead-approved 2026-06-12).

The push side of the self-consuming board loop. Tests cover the diff
predicate + the per-agent debounce. No real HTTP server stood up —
``post=False`` returns the wake records without firing requests; the
``post_wake`` helper is exercised separately with a tiny stdlib
``http.server`` thread.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from scitex_todo._wake_watcher import (
    WatcherState,
    detect_changes,
    post_wake,
)


def _seed(state: WatcherState, tasks: list[dict]) -> None:
    """Push `tasks` through the watcher to seed the snapshot (no wakes)."""
    detect_changes(state, tasks, now=0.0)


class TestSeed:
    def test_first_pass_fires_no_wakes_out(self):
        # Arrange
        state = WatcherState()
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=0.0)
        # Assert
        assert out == []

    def test_first_pass_fires_no_wakes_seeded(self):
        # Arrange
        state = WatcherState()
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=0.0)
        # Assert
        assert state.seeded is True


class TestTaskAdded:
    def test_new_task_fires_wake_len(self):
        # Arrange
        state = WatcherState()
        _seed(state, [])  # empty seed
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=100.0)
        # Assert
        assert len(out) == 1

    def test_new_task_fires_wake_trigger_kind(self):
        # Arrange
        state = WatcherState()
        _seed(state, [])  # empty seed
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=100.0)
        # Assert
        assert out[0].trigger_kind == "task_added"

    def test_new_task_fires_wake_agent(self):
        # Arrange
        state = WatcherState()
        _seed(state, [])  # empty seed
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=100.0)
        # Assert
        assert out[0].agent == "proj-x"

    def test_new_task_fires_wake_task_id(self):
        # Arrange
        state = WatcherState()
        _seed(state, [])  # empty seed
        tasks = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, tasks, now=100.0)
        # Assert
        assert out[0].task_id == "a"

    def test_unassigned_task_does_not_wake(self):
        # Arrange
        state = WatcherState()
        _seed(state, [])
        # Act
        tasks = [{"id": "a", "title": "A", "status": "pending"}]
        # Assert
        assert detect_changes(state, tasks, now=100.0) == []


class TestCommentAdded:
    def test_appended_comment_fires_wake_len(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        cur = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {
                        "ts": "2026-06-12T00:00Z",
                        "author": "lead",
                        "text": "please pick this up",
                    },
                ],
            }
        ]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert len(out) == 1

    def test_appended_comment_fires_wake_trigger_kind(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        cur = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {
                        "ts": "2026-06-12T00:00Z",
                        "author": "lead",
                        "text": "please pick this up",
                    },
                ],
            }
        ]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert out[0].trigger_kind == "comment"

    def test_appended_comment_fires_wake_summary_contains(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        cur = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {
                        "ts": "2026-06-12T00:00Z",
                        "author": "lead",
                        "text": "please pick this up",
                    },
                ],
            }
        ]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert "lead" in out[0].summary


class TestStatusChanged:
    def test_status_flip_fires_wake_len(self):
        # Arrange
        state = WatcherState()
        prev = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        _seed(state, prev)
        cur = [{"id": "a", "title": "A", "status": "in_progress", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert len(out) == 1

    def test_status_flip_fires_wake_trigger_kind(self):
        # Arrange
        state = WatcherState()
        prev = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        _seed(state, prev)
        cur = [{"id": "a", "title": "A", "status": "in_progress", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert out[0].trigger_kind == "status_changed"

    def test_status_flip_fires_wake_summary_contains(self):
        # Arrange
        state = WatcherState()
        prev = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        _seed(state, prev)
        cur = [{"id": "a", "title": "A", "status": "in_progress", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert "pending" in out[0].summary

    def test_status_flip_fires_wake_summary_contains_2(self):
        # Arrange
        state = WatcherState()
        prev = [{"id": "a", "title": "A", "status": "pending", "agent": "proj-x"}]
        _seed(state, prev)
        cur = [{"id": "a", "title": "A", "status": "in_progress", "agent": "proj-x"}]
        # Act
        out = detect_changes(state, cur, now=100.0)
        # Assert
        assert "in_progress" in out[0].summary


class TestDebounce:
    def test_back_to_back_wakes_collapse_per_agent_len(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        # Two comments in quick succession.
        cur1 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [{"author": "lead", "text": "1"}],
            }
        ]
        cur2 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {"author": "lead", "text": "1"},
                    {"author": "lead", "text": "2"},
                ],
            }
        ]
        # Act
        first = detect_changes(state, cur1, now=100.0, min_wake_interval_s=30.0)
        # Assert
        second = detect_changes(state, cur2, now=110.0, min_wake_interval_s=30.0)
        assert len(first) == 1

    def test_back_to_back_wakes_collapse_per_agent_second(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        # Two comments in quick succession.
        cur1 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [{"author": "lead", "text": "1"}],
            }
        ]
        cur2 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {"author": "lead", "text": "1"},
                    {"author": "lead", "text": "2"},
                ],
            }
        ]
        # Act
        first = detect_changes(state, cur1, now=100.0, min_wake_interval_s=30.0)
        # Assert
        second = detect_changes(state, cur2, now=110.0, min_wake_interval_s=30.0)
        assert second == []  # debounced

    def test_wake_after_debounce_window_passes(self):
        # Arrange
        state = WatcherState()
        prev = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [],
            }
        ]
        _seed(state, prev)
        cur1 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [{"author": "lead", "text": "1"}],
            }
        ]
        cur2 = [
            {
                "id": "a",
                "title": "A",
                "status": "pending",
                "agent": "proj-x",
                "comments": [
                    {"author": "lead", "text": "1"},
                    {"author": "lead", "text": "2"},
                ],
            }
        ]
        detect_changes(state, cur1, now=100.0, min_wake_interval_s=30.0)
        # Act
        out = detect_changes(state, cur2, now=200.0, min_wake_interval_s=30.0)
        # Assert
        assert len(out) == 1


# === post_wake — real HTTP round-trip on a localhost ephemeral port ====


class _OkHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args, **_kwargs):  # quiet test output
        pass


@pytest.fixture
def http_server():
    """Stand up a one-shot localhost HTTP server on an ephemeral port."""
    _OkHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


class TestPostWake:
    def test_round_trip_to_local_server_ok(self, http_server):
        # Arrange
        # Act
        ok = post_wake(http_server, {"hello": "world"})
        # Assert
        # Tiny pause for the daemon thread to flush.
        for _ in range(20):
            if _OkHandler.received:
                break
            time.sleep(0.02)
        assert ok is True

    def test_round_trip_to_local_server_received(self, http_server):
        # Arrange
        # Act
        ok = post_wake(http_server, {"hello": "world"})
        # Assert
        # Tiny pause for the daemon thread to flush.
        for _ in range(20):
            if _OkHandler.received:
                break
            time.sleep(0.02)
        assert _OkHandler.received == [{"hello": "world"}]

    def test_returns_false_on_dead_port(self):
        # Pick a port that's almost certainly unbound.
        # Arrange
        # Act
        # Assert
        assert post_wake(1, {"x": 1}, timeout_s=0.2) is False


# EOF
