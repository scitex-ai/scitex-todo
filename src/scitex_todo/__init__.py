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

from ._mermaid import STATUS_STYLE, build_mermaid
from ._model import VALID_STATUSES, TaskValidationError, load_tasks, save_tasks
from ._paths import bundled_example, resolve_tasks_path
from ._render import (
    RenderError,
    find_chromium,
    render,
    render_with_kroki,
    render_with_mmdc,
)
from ._store import (
    ENV_AGENT,
    ENV_SCOPE,
    TaskNotFoundError,
    add_task,
    complete_task,
    list_tasks,
    summary,
    update_task,
)

__all__ = [
    "__version__",
    "ENV_AGENT",
    "ENV_SCOPE",
    "STATUS_STYLE",
    "VALID_STATUSES",
    "RenderError",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "build_mermaid",
    "bundled_example",
    "complete_task",
    "find_chromium",
    "list_tasks",
    "load_tasks",
    "render",
    "render_with_kroki",
    "render_with_mmdc",
    "resolve_tasks_path",
    "save_tasks",
    "summary",
    "update_task",
]

# EOF
