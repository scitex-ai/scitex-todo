#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test that ``examples/01_render_graph.py`` runs to completion (no mocks).

Runs the real example as a subprocess (so its ``__main__`` guard fires) and
checks the exit code and the mermaid source it prints.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "01_render_graph.py"


def test_render_graph_example_exits_zero():
    # Arrange
    cmd = [sys.executable, str(_EXAMPLE)]
    # Act
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Assert
    assert result.returncode == 0


def test_render_graph_example_prints_flowchart_source():
    # Arrange
    cmd = [sys.executable, str(_EXAMPLE)]
    # Act
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Assert
    assert "flowchart TB" in result.stdout


# EOF
