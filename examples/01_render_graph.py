#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Example: bundled YAML task store -> mermaid dependency-graph source.

Loads the generic example store that ships inside the wheel, builds its
mermaid ``flowchart TB`` source, and prints it. Rendering to PNG
(``scitex-todo render-graph``) additionally needs ``mmdc`` or ``kroki.io``;
this example stops at the mermaid source so it runs fully offline with no
dependencies beyond ``scitex-todo`` itself.

Run:
    python 01_render_graph.py
"""

from __future__ import annotations

from scitex_todo._mermaid import build_mermaid
from scitex_todo._model import load_tasks
from scitex_todo._paths import bundled_example

if __name__ == "__main__":
    store = bundled_example()
    tasks = load_tasks(store)
    mermaid_src = build_mermaid(tasks)

    print(f"# {len(tasks)} tasks from {store}")
    print(mermaid_src)

# EOF
