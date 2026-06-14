#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the mermaid adapter (no mocks; plain in-memory task dicts)."""

from __future__ import annotations

from scitex_todo._mermaid import build_mermaid


def test_build_mermaid_starts_with_flowchart_header():
    # Arrange
    tasks = [{"id": "a", "title": "Alpha", "status": "done"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert src.startswith("flowchart TB")


def test_build_mermaid_emits_depends_on_arrow_edge():
    # Arrange
    tasks = [
        {"id": "design", "title": "Design", "status": "done"},
        {
            "id": "build",
            "title": "Build",
            "status": "pending",
            "depends_on": ["design"],
        },
    ]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "design --> build" in src


def test_build_mermaid_emits_blocks_inhibition_edge():
    # Arrange
    tasks = [
        {"id": "ci", "title": "Flaky CI", "status": "failed", "blocks": ["tests"]},
        {"id": "tests", "title": "Tests", "status": "pending"},
    ]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "ci -- blocks --x tests" in src


def test_build_mermaid_includes_goal_gold_color():
    # Arrange
    tasks = [{"id": "north", "title": "North Star", "status": "goal"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "classDef goal fill:#ffe082" in src


def test_build_mermaid_assigns_goal_class_to_member():
    # Arrange
    tasks = [{"id": "north", "title": "North Star", "status": "goal"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "class north goal" in src


def test_build_mermaid_skips_unknown_depends_on_target():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "done", "depends_on": ["ghost"]}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "ghost -->" not in src


def test_build_mermaid_renders_note_under_title():
    # Arrange
    tasks = [{"id": "a", "title": "A", "status": "blocked", "note": "waiting"}]
    # Act
    src = build_mermaid(tasks)
    # Assert
    assert "A<br/>(waiting)" in src


# EOF
