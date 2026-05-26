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

import scitex_todo as todo

if __name__ == "__main__":
    store = todo.bundled_example()
    tasks = todo.load_tasks(store)
    mermaid_src = todo.build_mermaid(tasks)

    print(f"# {len(tasks)} tasks from {store}")
    print(mermaid_src)

# EOF
