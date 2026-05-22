#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Graph + tasks handlers: thin adapters over the scitex-todo Python API.

Zero task logic here — everything delegates to ``scitex_todo``: the store is
resolved + loaded by ``services.get_board`` (which calls ``resolve_tasks_path``
and ``load_tasks``), the mermaid source comes from ``build_mermaid``, and node
colors come from ``STATUS_STYLE``.
"""

from django.http import JsonResponse


def _status_colors() -> dict:
    """Single-source the status -> color map from the core package.

    ``STATUS_STYLE`` maps status -> (fill, stroke, dasharray). The board only
    needs fill + stroke + dashed flag, so project that into a small JSON dict.
    """
    from scitex_todo import STATUS_STYLE

    return {
        status: {"fill": fill, "stroke": stroke, "dashed": bool(dash)}
        for status, (fill, stroke, dash) in STATUS_STYLE.items()
    }


def _build_graph(board) -> dict:
    """Build the {nodes, edges, status_colors, ...} payload from a board."""
    from scitex_todo import build_mermaid

    ids = {t["id"] for t in board.tasks}

    nodes = [
        {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t.get("priority"),
            "note": t.get("note"),
            "repo": t.get("repo"),
            # `parent` is the nesting field that drives the frontend
            # drill-down: children of a node N are tasks whose
            # `parent == N.id`. Emit it verbatim; if it points to an unknown
            # id the frontend treats this task as top-level (same lenient
            # stance as edges to unknown ids).
            "parent": t.get("parent"),
        }
        for t in board.tasks
    ]

    edges = []
    for t in board.tasks:
        tid = t["id"]
        for dep in t.get("depends_on", []) or []:
            if dep in ids:
                edges.append({"source": dep, "target": tid, "kind": "depends_on"})
        for target in t.get("blocks", []) or []:
            if target in ids:
                edges.append({"source": tid, "target": target, "kind": "blocks"})

    return {
        "nodes": nodes,
        "edges": edges,
        "status_colors": _status_colors(),
        "mermaid": build_mermaid(board.tasks),
        "store_path": str(board.store_path),
        "task_count": len(board.tasks),
    }


def handle_graph(request, board):
    """GET graph -> structured nodes + edges + status colors (+ mermaid)."""
    return JsonResponse(_build_graph(board))


def handle_tasks(request, board):
    """GET tasks -> the raw validated task list (for grids / debugging)."""
    return JsonResponse(
        {"tasks": list(board.tasks), "store_path": str(board.store_path)}
    )


def handle_ping(request, board):
    """GET ping -> health check (no store needed)."""
    return JsonResponse({"status": "ok"})


# EOF
