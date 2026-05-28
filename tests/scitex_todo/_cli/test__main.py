#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo root CLI group + core verbs (no mocks; CliRunner)."""

from __future__ import annotations

import json

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


def test_list_tasks_command_prints_resolved_task_ids(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-tasks", "--tasks", store])
    # Assert
    assert "design" in result.output


def test_list_tasks_json_emits_parseable_array(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["list-tasks", "--tasks", store, "--json"])
    ids = [task["id"] for task in json.loads(result.output)]
    # Assert
    assert ids == ["design", "build"]


def test_render_graph_print_mermaid_emits_flowchart_source(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["render-graph", "--tasks", store, "--print-mermaid"])
    # Assert
    assert result.output.startswith("flowchart TB")


def test_render_graph_print_mermaid_includes_dependency_edge(tmp_path):
    # Arrange
    runner = CliRunner()
    store = _store(tmp_path)
    # Act
    result = runner.invoke(main, ["render-graph", "--tasks", store, "--print-mermaid"])
    # Assert
    assert "design --> build" in result.output


def test_version_flag_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--version"])
    # Assert
    assert result.exit_code == 0


def test_help_recursive_json_emits_command_tree():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--help-recursive", "--json"])
    tree = json.loads(result.output)
    # Assert
    assert "render-graph" in tree["commands"]
