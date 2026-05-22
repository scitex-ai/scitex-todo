#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the board AppConfig (real import, no mocks)."""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from scitex_todo._django.apps import ScitexTodoConfig  # noqa: E402


def test_app_config_uses_board_label():
    # Arrange
    config = ScitexTodoConfig
    # Act
    label = config.label
    # Assert
    assert label == "scitex_todo_board"


def test_app_config_points_at_django_package():
    # Arrange
    config = ScitexTodoConfig
    # Act
    name = config.name
    # Assert
    assert name == "scitex_todo._django"


# EOF
