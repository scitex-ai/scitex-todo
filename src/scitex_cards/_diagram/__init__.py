# -*- coding: utf-8 -*-
"""Diagram pipeline — mermaid-source generation + image rendering.

Groups the two diagram modules under one subpackage so the flat
``src/scitex_cards/`` root stays under the audit's file-count threshold
(PS-108b). The public surface is unchanged: callers import the same
names, now via ``from scitex_cards._diagram import ...``.
"""

from ._mermaid import STATUS_STYLE, build_mermaid
from ._render import (
    RenderError,
    find_chromium,
    render,
    render_with_kroki,
    render_with_mmdc,
)

__all__ = [
    "STATUS_STYLE",
    "build_mermaid",
    "RenderError",
    "find_chromium",
    "render",
    "render_with_kroki",
    "render_with_mmdc",
]
