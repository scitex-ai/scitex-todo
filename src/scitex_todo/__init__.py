#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo: a canonical YAML task store with pluggable adapters.

The task store (YAML, top-level ``tasks:`` list) is the single source of
truth. Adapters render or import it; the mermaid adapter (YAML -> dependency
PNG) ships today. See the project roadmap for org and Web-UI adapters.

Quick Start
-----------
>>> import scitex_todo as todo
>>> tasks = todo.load_tasks("tasks.yaml")        # doctest: +SKIP
>>> src = todo.build_mermaid(tasks)              # doctest: +SKIP
>>> todo.render(src, "tasks.png")                # doctest: +SKIP
'mmdc'
"""

from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v

    try:
        __version__ = _v("scitex-todo")
    except PackageNotFoundError:
        __version__ = "0.0.0+local"
    del _v, PackageNotFoundError
except ImportError:  # pragma: no cover — only on ancient Pythons
    __version__ = "0.0.0+local"

#: Public API — Convention A (audit §6: every public Python API must match a
#: registered MCP tool name 1:1). The MCP tool surface is documented in
#: ``_skills/scitex-todo/05_mcp-tools.md`` and registered in ``_mcp_server.py``.
#:
#: Render / mermaid / paths / model helpers used to be re-exported here.
#: They were moved off the top level (audit §6) but remain importable from
#: their submodules:
#:
#:     from scitex_todo._render  import render, render_with_kroki, render_with_mmdc, find_chromium, RenderError
#:     from scitex_todo._mermaid import build_mermaid, STATUS_STYLE
#:     from scitex_todo._model   import load_tasks, save_tasks, VALID_STATUSES, TaskValidationError
#:     from scitex_todo._paths   import resolve_tasks_path, bundled_example
from ._model import TaskValidationError
from ._store import (
    ENV_AGENT,
    ENV_SCOPE,
    TaskNotFoundError,
    add_comment,
    add_task,
    complete_task,
    list_tasks,
    resolve_store,
    summarize_tasks,
    update_task,
)

__all__ = [
    "__version__",
    "ENV_AGENT",
    "ENV_SCOPE",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_comment",
    "add_task",
    "complete_task",
    "list_tasks",
    "resolve_store",
    "summarize_tasks",
    "update_task",
]

# EOF
