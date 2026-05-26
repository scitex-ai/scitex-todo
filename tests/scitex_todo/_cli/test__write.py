#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Phase-1 mutation/admin CLI verbs (CliRunner; no mocks).

Verbs covered:
    add / update / done / list / summary / where / init / sync (stub)
    mcp doctor / mcp install / mcp list-tools (fallback fastmcp-missing path)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_todo import _model, _store
from scitex_todo._cli import main


def _store_path(tmp_path) -> str:
    """Path string to a fresh empty store under tmp_path/.scitex/todo/."""
    return str(tmp_path / "tasks.yaml")


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #
def test_add_writes_a_new_task(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    result = runner.invoke(
        main,
        ["add", "design", "Design phase", "--tasks", store,
         "--scope", "agent:test", "--priority", "1"],
    )
    assert result.exit_code == 0, result.output
    assert "added design" in result.output
    tasks = _model.load_tasks(store)
    assert tasks[0]["id"] == "design"
    assert tasks[0]["scope"] == "agent:test"
    assert tasks[0]["priority"] == 1


def test_add_json_emits_inserted_dict(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    result = runner.invoke(
        main,
        ["add", "a", "A", "--tasks", store, "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["id"] == "a"
    assert payload["status"] == "pending"


def test_add_duplicate_id_errors(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    result = runner.invoke(main, ["add", "a", "A again", "--tasks", store])
    assert result.exit_code != 0
    assert "duplicate" in result.output.lower()


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #
def test_update_changes_status_and_priority(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store, "--priority", "10"])
    result = runner.invoke(
        main,
        ["update", "a", "--tasks", store, "--status", "in_progress", "--priority", "1"],
    )
    assert result.exit_code == 0, result.output
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["status"] == "in_progress"
    assert on_disk["priority"] == 1


def test_update_empty_scope_clears_field(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(
        main, ["add", "a", "A", "--tasks", store, "--scope", "agent:initial"]
    )
    result = runner.invoke(main, ["update", "a", "--tasks", store, "--scope", ""])
    assert result.exit_code == 0, result.output
    assert "scope" not in _model.load_tasks(store)[0]


def test_update_no_fields_errors(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    result = runner.invoke(main, ["update", "a", "--tasks", store])
    assert result.exit_code != 0
    assert "no fields" in result.output.lower()


def test_update_missing_id_errors(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    result = runner.invoke(
        main, ["update", "nope", "--tasks", store, "--status", "done"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
def test_done_stamps_completion_meta(tmp_path, monkeypatch):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:cli-test")
    result = runner.invoke(main, ["done", "a", "--tasks", store])
    assert result.exit_code == 0, result.output
    assert "done a" in result.output
    on_disk = _model.load_tasks(store)[0]
    assert on_disk["status"] == "done"
    assert on_disk["_log_meta"]["completed_by"] == "agent:cli-test"
    assert on_disk["_log_meta"]["completed_at"].endswith("Z")


def test_done_by_overrides_env(tmp_path, monkeypatch):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:env")
    result = runner.invoke(
        main, ["done", "a", "--tasks", store, "--by", "agent:explicit"]
    )
    assert result.exit_code == 0, result.output
    assert _model.load_tasks(store)[0]["_log_meta"]["completed_by"] == "agent:explicit"


# --------------------------------------------------------------------------- #
# list (extended w/ filters)                                                  #
# --------------------------------------------------------------------------- #
def test_list_filters_by_scope(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store, "--scope", "agent:lead"])
    runner.invoke(
        main, ["add", "b", "B", "--tasks", store, "--scope", "agent:proj-scitex-todo"]
    )
    result = runner.invoke(
        main,
        ["list", "--tasks", store, "--scope", "agent:proj-scitex-todo", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output.strip())
    assert {r["id"] for r in rows} == {"b"}


def test_list_env_scope_default(tmp_path, monkeypatch):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store, "--scope", "agent:lead"])
    runner.invoke(main, ["add", "b", "B", "--tasks", store, "--scope", "agent:other"])
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:lead")
    result = runner.invoke(main, ["list", "--tasks", store, "--json"])
    rows = json.loads(result.output.strip())
    assert {r["id"] for r in rows} == {"a"}


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
def test_summary_emits_counts(tmp_path):
    runner = CliRunner()
    store = _store_path(tmp_path)
    runner.invoke(main, ["add", "a", "A", "--tasks", store])
    runner.invoke(main, ["add", "b", "B", "--tasks", store, "--status", "done"])
    result = runner.invoke(main, ["summary", "--tasks", store, "--json"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.output.strip())
    assert info["total"] == 2
    assert info["by_status"]["done"] == 1
    assert info["by_status"]["pending"] == 1


# --------------------------------------------------------------------------- #
# where                                                                       #
# --------------------------------------------------------------------------- #
def test_where_prints_resolution_chain(tmp_path, monkeypatch):
    runner = CliRunner()
    store = _store_path(tmp_path)
    Path(store).write_text("tasks: []\n", encoding="utf-8")
    monkeypatch.delenv("SCITEX_TODO_TASKS", raising=False)
    result = runner.invoke(main, ["where", "--tasks", store, "--json"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.output.strip())
    assert info["resolved"] == store
    assert info["exists"] is True


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #
def test_init_shared_creates_empty_store(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path / "fake-home"))
    result = runner.invoke(main, ["init", "--shared"])
    assert result.exit_code == 0, result.output
    assert "created" in result.output
    expected = tmp_path / "fake-home" / "todo" / "tasks.yaml"
    assert expected.exists()
    # idempotent
    again = runner.invoke(main, ["init", "--shared"])
    assert "no-op" in again.output


def test_init_project_outside_git_errors(tmp_path, monkeypatch):
    """`--project` outside a git repo must error rather than silently picking
    a wrong directory."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)  # bare directory, no .git
    result = runner.invoke(main, ["init", "--project"])
    assert result.exit_code != 0
    assert "git repo" in result.output.lower()


# --------------------------------------------------------------------------- #
# sync (Phase-1 stub)                                                         #
# --------------------------------------------------------------------------- #
def test_sync_dry_run_prints_plan(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "PHASE-1 STUB" in result.output
    assert "git" in result.output


def test_sync_apply_errors_in_phase_1(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["sync", "--apply"])
    assert result.exit_code != 0
    assert "Phase 1" in result.output or "Phase 2" in result.output


# --------------------------------------------------------------------------- #
# mcp subgroup — env-dependent (graceful in both [mcp] installed / missing)   #
# --------------------------------------------------------------------------- #
def test_mcp_install_prints_claude_code_snippet():
    """`mcp install` is fastmcp-independent — it emits the snippet text."""
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "install"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "mcpServers" in payload
    assert "scitex-todo" in payload["mcpServers"]


def test_mcp_doctor_status_is_critical_when_fastmcp_missing():
    """When fastmcp isn't installed, `doctor --json` reports `critical` +
    an install hint, and exits non-zero. We don't mock — we just check
    the actual environment's response. If fastmcp IS installed, the
    status will be `ok` and we skip this assertion."""
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "doctor", "--json"])
    info = json.loads(result.output.splitlines()[-1])
    if info["fastmcp"] is None:
        assert info["status"] == "critical"
        assert "mcp" in (info["hint"] or "").lower()
        assert result.exit_code == 2
    else:
        # fastmcp IS installed: doctor should be ok with non-zero tool count.
        assert info["status"] in ("ok", "degraded")
        assert info["tools"] >= 1


# EOF
