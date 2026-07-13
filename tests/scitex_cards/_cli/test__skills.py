#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a `skills` group (list / get) over the bundled skills."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main


def test_skills_list_includes_installation_leaf():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "list"])
    # Assert
    assert "01_installation" in result.output


def test_skills_list_json_is_parseable():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "list", "--json"])
    names = {entry["name"] for entry in json.loads(result.output)}
    # Assert
    assert "02_quick-start" in names


def test_skills_get_prints_skill_body():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "get", "02_quick-start"])
    # Assert
    assert "Quick Start" in result.output


def test_skills_get_unknown_name_exits_nonzero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "get", "does-not-exist"])
    # Assert
    assert result.exit_code != 0
