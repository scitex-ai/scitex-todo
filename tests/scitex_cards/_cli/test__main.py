#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo root CLI group + core verbs (no mocks; CliRunner)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task


def _seed():
    """Seed the harness-provided store with a small dependency pair.

    ``tests/conftest.py`` bootstraps an empty per-test store and pins every
    store-selecting env var at it, so neither this seeder nor the CLI needs
    to be told where the store is.
    """
    add_task(id="design", title="Design", status="done", assignee="agent:test")
    add_task(
        id="build",
        title="Build",
        status="deferred",
        assignee="agent:test",
        depends_on=["design"],
    )


def test_list_tasks_command_prints_resolved_task_ids():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["list-tasks"])
    # Assert
    assert "design" in result.output


def test_list_tasks_json_emits_parseable_array():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["list-tasks", "--json"])
    ids = [task["id"] for task in json.loads(result.output)]
    # Assert
    assert ids == ["design", "build"]


def test_render_graph_print_mermaid_emits_flowchart_source():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["render-graph", "--print-mermaid"])
    # Assert
    assert result.output.startswith("flowchart TB")


def test_render_graph_print_mermaid_includes_dependency_edge():
    # Arrange
    runner = CliRunner()
    _seed()
    # Act
    result = runner.invoke(main, ["render-graph", "--print-mermaid"])
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
