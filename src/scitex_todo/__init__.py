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
#:     from scitex_todo._diagram  import render, render_with_kroki, render_with_mmdc, find_chromium, RenderError
#:     from scitex_todo._diagram import build_mermaid, STATUS_STYLE
#:     from scitex_todo._model   import load_tasks, save_tasks, VALID_STATUSES, TaskValidationError
#:     from scitex_todo._paths   import resolve_tasks_path, bundled_example

# PEP 562 lazy attribute resolution — keeps `import scitex_todo` cold-start
# well under the audit-cli §10 budget (500 ms) by deferring every submodule
# load until the attribute is actually touched. Click tab-completion taps
# `import scitex_todo` once per Tab press, so the savings compound.
#
# Public surface stays identical: every name in ``__all__`` resolves on
# ``scitex_todo.NAME`` access via :func:`__getattr__`, and gets cached in
# ``globals()`` for O(1) repeat lookups.
_LAZY_IMPORTS = {
    "TaskValidationError": ("._model", "TaskValidationError"),
    "ENV_AGENT": ("._store", "ENV_AGENT"),
    "ENV_SCOPE": ("._store", "ENV_SCOPE"),
    "TaskNotFoundError": ("._store", "TaskNotFoundError"),
    "add_task": ("._store", "add_task"),
    "comment_task": ("._store", "comment_task"),
    "complete_task": ("._store", "complete_task"),
    "delete_task": ("._store", "delete_task"),
    "get_task": ("._store", "get_task"),
    "list_tasks": ("._store", "list_tasks"),
    "reopen_task": ("._store", "reopen_task"),
    "resolve_store": ("._store", "resolve_store"),
    "resolve_task": ("._store", "resolve_task"),
    "restore_task": ("._store", "restore_task"),
    "set_edge": ("._store", "set_edge"),
    "summarize_tasks": ("._store", "summarize_tasks"),
    "update_task": ("._store", "update_task"),
}


def __getattr__(name: str):
    """PEP 562 lazy loader — resolve public-API names on first access.

    Imports the source submodule, fetches the attribute, caches it
    into module ``globals()`` so subsequent accesses skip the lookup.
    Unknown names raise ``AttributeError`` per the PEP.
    """
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod_path, attr = target
    value = getattr(importlib.import_module(mod_path, __name__), attr)
    globals()[name] = value
    return value


def __dir__():
    """Make tab-completion / ``dir(scitex_todo)`` see the public surface
    even before any attribute has been touched."""
    return sorted(set(__all__) | set(globals()))


__all__ = [
    "__version__",
    "ENV_AGENT",
    "ENV_SCOPE",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reopen_task",
    "resolve_store",
    "resolve_task",
    "restore_task",
    "set_edge",
    "summarize_tasks",
    "update_task",
]

# EOF
