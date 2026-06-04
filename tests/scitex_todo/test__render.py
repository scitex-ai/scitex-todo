#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the renderer (env-gated; no mocks)."""

from __future__ import annotations

import shutil

import pytest

from scitex_todo._mermaid import build_mermaid
from scitex_todo._render import find_chromium, render


def test_find_chromium_returns_path_or_none():
    # Arrange
    expected_types = (str, type(None))
    # Act
    result = find_chromium()
    # Assert
    assert isinstance(result, expected_types)


@pytest.mark.skipif(
    shutil.which("mmdc") is None or find_chromium() is None,
    reason="mmdc + a puppeteer/playwright chromium are required for a real render",
)
def test_render_with_mmdc_produces_nonempty_png(tmp_path):
    # Arrange
    src = build_mermaid([{"id": "a", "title": "Alpha", "status": "done"}])
    out = tmp_path / "out.png"
    # Act
    render(src, out)
    # Assert
    assert out.stat().st_size > 0


# EOF
