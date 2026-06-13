#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `close` CLI verb (CliRunner; no mocks).

Verifies the close-with-reason verb that fills the lead-confirmed gap:
``delete`` drops context, the closed-enum status set lacks a ``"closed"``
slot, so close composes ``comment_task`` + ``update_task(status=deferred)``
+ ``_log_meta.closed_{at,by}`` to preserve the reason on the card.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_todo import _model
from scitex_todo._cli import main


def _store_path(tmp_path) -> str:
    return str(tmp_path / "tasks.yaml")


# --------------------------------------------------------------------------- #
# close                                                                       #
# --------------------------------------------------------------------------- #
def test_close_persists_comment_and_status_deferred(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "superseded", "--tasks", store]
    )
    # Assert
    assert result.exit_code == 0, result.output
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["status"] == "deferred"
    assert on_disk["comments"][0]["text"] == "[CLOSED] superseded"
    assert on_disk["_log_meta"]["closed_at"]
    assert on_disk["_log_meta"]["closed_by"] == "agent:cli-test"


def test_close_missing_reason_is_usage_error(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    result = runner.invoke(main, ["close", "a", "--tasks", store])
    # Assert
    assert result.exit_code == 2 and "Traceback" not in result.output


def test_close_empty_reason_is_usage_error(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "a", "--reason", "   ", "--tasks", store]
    )
    # Assert
    assert result.exit_code == 2


def test_close_unknown_id_nonzero_no_traceback(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    result = runner.invoke(
        main, ["close", "no-such-id", "--reason", "x", "--tasks", store]
    )
    # Assert
    assert result.exit_code != 0 and "Traceback" not in result.output


def test_close_by_override_flows_into_comment_and_log_meta(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:env")
    # Act
    runner.invoke(
        main,
        [
            "close", "a",
            "--reason", "manual close",
            "--by", "agent:explicit",
            "--tasks", store,
        ],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["comments"][0]["author"] == "agent:explicit"
    assert on_disk["_log_meta"]["closed_by"] == "agent:explicit"


def test_close_dry_run_does_not_mutate(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    runner.invoke(
        main,
        ["close", "a", "--reason", "ghost", "--tasks", store, "--dry-run"],
    )
    # Assert
    on_disk = _model.load_tasks(store)[0]
    assert on_disk.get("status") == "pending"
    assert on_disk.get("comments", []) == []
    assert "closed_at" not in (on_disk.get("_log_meta") or {})


def test_close_json_emits_structured_payload(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    env.set("SCITEX_TODO_AGENT", "agent:cli-test")
    # Act
    result = runner.invoke(
        main,
        ["close", "a", "--reason", "json-shape", "--tasks", store, "--json"],
    )
    # Assert
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["task_id"] == "a"
    assert payload["status"] == "deferred"
    assert payload["reason"] == "json-shape"
    assert payload["comment"]["text"] == "[CLOSED] json-shape"
    assert payload["closed_by"] == "agent:cli-test"
    assert payload["closed_at"]


# EOF
