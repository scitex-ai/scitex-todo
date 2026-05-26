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


def test_mcp_subgroup_required_verbs_are_present():
    """Phase 1 ships a real MCP server (`scitex_todo._mcp_server`). The
    `mcp` subgroup is now owned by `_cli/_mcp.py` and must expose the §3
    required four verbs (start / doctor / list-tools / install). The old
    "no MCP yet → empty array" stub that used to live in `_introspect.py`
    has been removed."""
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "--help"])
    assert result.exit_code == 0, result.output
    for verb in ("start", "doctor", "list-tools", "install"):
        assert verb in result.output, f"`mcp {verb}` missing from --help"
