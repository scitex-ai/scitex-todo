#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the §1a shell-completion commands (no mocks; CliRunner)."""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main


def test_print_shell_completion_emits_bash_function():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["print-shell-completion", "--shell", "bash"])
    # Assert
    assert "_scitex_cards_completion" in result.output


def test_install_shell_completion_dry_run_changes_nothing():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert "[dry-run]" in result.output


def test_install_shell_completion_dry_run_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["install-shell-completion", "--dry-run"])
    # Assert
    assert result.exit_code == 0
