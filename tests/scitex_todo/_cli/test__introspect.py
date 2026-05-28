#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a introspection commands (list-python-apis, mcp list-tools)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_todo._cli import main


def test_list_python_apis_lists_public_surface():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["list-python-apis"])
    # Assert
    assert "build_mermaid" in result.output


def test_list_python_apis_json_is_parseable():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["list-python-apis", "--json"])
    names = {entry["name"] for entry in json.loads(result.output)}
    # Assert
    assert "load_tasks" in names


def test_list_python_apis_verbose_ladder_is_monotonic():
    # Arrange
    runner = CliRunner()
    # Act
    counts = [
        len([ln for ln in runner.invoke(main, ["list-python-apis", *flag]).output.splitlines() if ln.strip()])
        for flag in ([], ["-v"], ["-vv"], ["-vvv"])
    ]
    # Assert
    assert counts == sorted(counts)


def test_mcp_list_tools_json_emits_empty_array():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["mcp", "list-tools", "--json"])
    # Assert
    assert json.loads(result.output) == []
