#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for `scitex-todo mcp install --apply` (fleet P3a).

The print-only path is already covered elsewhere; this module pins
the new --apply behavior. CliRunner against a tmp ``.mcp.json`` (no
mocks — STX-NM / PA-306). One assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_todo._cli import main


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# === apply creates target when absent =======================================


def test_apply_creates_file_when_absent(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_apply_writes_scitex_todo_entry(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    data = _read_json(target)
    # Assert
    assert "scitex-todo" in data.get("mcpServers", {})


def test_apply_writes_correct_command_args(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    entry = _read_json(target)["mcpServers"]["scitex-todo"]
    # Assert
    assert entry == {"command": "scitex-todo", "args": ["mcp", "start"]}


# === apply preserves sibling entries (fleet-friendly) =======================


def test_apply_preserves_sibling_server_entry(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-bin"}}}),
        encoding="utf-8",
    )
    runner = CliRunner()
    # Act
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Assert
    assert "other" in _read_json(target)["mcpServers"]


# === idempotence ============================================================


def test_apply_twice_is_idempotent(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert "noop" in result.output


# === dry-run does NOT write =================================================


def test_apply_dry_run_does_not_create_file(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    runner.invoke(
        main,
        ["mcp", "install", "--apply", "--to", str(target), "--dry-run"],
    )
    # Assert
    assert not target.exists()


def test_apply_dry_run_prints_action_marker(tmp_path):
    # Arrange
    runner = CliRunner()
    target = tmp_path / ".mcp.json"
    # Act
    result = runner.invoke(
        main,
        ["mcp", "install", "--apply", "--to", str(target), "--dry-run"],
    )
    # Assert
    assert "dry-run" in result.output


# === backup on overwrite ====================================================


def test_apply_backs_up_existing_file(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8"
    )
    runner = CliRunner()
    # Act
    runner.invoke(main, ["mcp", "install", "--apply", "--to", str(target), "-y"])
    # Assert
    assert (tmp_path / ".mcp.json.bak").exists()


# === bad target file =======================================================


def test_apply_fails_on_invalid_json_target(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text("{ not valid json", encoding="utf-8")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code != 0


def test_apply_fails_on_non_object_root(tmp_path):
    # Arrange
    target = tmp_path / ".mcp.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["mcp", "install", "--apply", "--to", str(target), "-y"]
    )
    # Assert
    assert result.exit_code != 0


# === print-only path back-compat ===========================================


def test_print_only_still_emits_snippet(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "install"])
    # Assert
    assert "scitex-todo" in result.output


# EOF
