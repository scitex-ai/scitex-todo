#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handler package -- thin Django wrappers around the scitex-todo Python API.

Exports the ``HANDLERS`` dict consumed by the catch-all dispatcher in
``views.api_dispatch``. Each handler takes ``(request, board)`` and returns a
``JsonResponse``.
"""

from .graph import handle_graph, handle_ping, handle_tasks

# endpoint string -> handler function
HANDLERS = {
    "graph": handle_graph,
    "tasks": handle_tasks,
    "ping": handle_ping,
}

# Endpoints that work without a loaded board (health checks).
NO_BOARD_ENDPOINTS = {"ping"}

__all__ = ["HANDLERS", "NO_BOARD_ENDPOINTS"]

# EOF
