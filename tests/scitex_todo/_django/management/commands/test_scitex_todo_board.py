#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex_todo_board`` management command (no mocks).

Mirrors ``src/scitex_todo/_django/management/commands/scitex_todo_board.py``.
Exercises the real ``Command`` argument parser (no server is started — we only
assert the CLI contract), so the board's ``--tasks`` / ``--port`` / ``--no-browser``
options stay stable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from scitex_todo._django.management.commands.scitex_todo_board import (  # noqa: E402
    Command,
)


def _parse(argv):
    """Build the command's real argparse parser and parse ``argv``."""
    parser = Command().create_parser("manage.py", "scitex_todo_board")
    return parser.parse_args(argv)


def test_board_command_defaults_port_to_8051():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.port == 8051


def test_board_command_defaults_tasks_to_empty_string():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.tasks == ""


def test_board_command_defaults_no_browser_false():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.no_browser is False


def test_board_command_parses_custom_port():
    # Arrange
    argv = ["--port", "9090"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.port == 9090


def test_board_command_parses_tasks_path():
    # Arrange
    argv = ["--tasks", "/tmp/tasks.yaml"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.tasks == "/tmp/tasks.yaml"


def test_board_command_no_browser_flag_sets_true():
    # Arrange
    argv = ["--no-browser"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.no_browser is True


def test_board_command_help_mentions_board():
    # Arrange
    command = Command()
    # Act
    help_text = command.help
    # Assert
    assert "board" in help_text.lower()


# EOF
