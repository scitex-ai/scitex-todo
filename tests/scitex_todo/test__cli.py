#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo CLI (no mocks; CliRunner + real tmp files)."""

from __future__ import annotations

from click.testing import CliRunner

from scitex_todo._cli import main


def _store(tmp_path):
    """Write a small valid tasks.yaml and return its path string."""
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - {id: design, title: Design, status: done}\n"
        "  - {id: build, title: Build, status: pending, depends_on: [design]}\n",
        encoding="utf-8",
    )
    return str(path)


def test_list_command_prints_resolved_task_ids(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list", "--tasks", store])
    # Assert
    assert "design" in result.output


def test_render_print_mermaid_emits_flowchart_source(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["render", "--tasks", store, "--print-mermaid"])
    # Assert
    assert result.output.startswith("flowchart TB")


def test_render_print_mermaid_includes_dependency_edge(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["render", "--tasks", store, "--print-mermaid"])
    # Assert
    assert "design --> build" in result.output


def test_version_flag_exits_zero(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--version"])
    # Assert
    assert result.exit_code == 0


# EOF
