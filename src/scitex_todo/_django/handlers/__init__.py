#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handler package -- thin Django wrappers around the scitex-todo Python API.

Exports the ``HANDLERS`` dict consumed by the catch-all dispatcher in
``views.api_dispatch``. Each handler takes ``(request, board)`` and returns a
``JsonResponse``.
"""

from .crud import (
    handle_comment,
    handle_create,
    handle_delete,
    handle_edge,
    handle_restore,
    handle_update,
)
from .graph import handle_graph, handle_ping, handle_rev, handle_tasks
from .nudge import handle_nudge
from .priority import handle_priority
from .reopen import handle_reopen
from .resolve import handle_resolve

# endpoint string -> handler function
HANDLERS = {
    "graph": handle_graph,
    "tasks": handle_tasks,
    "ping": handle_ping,
    "rev": handle_rev,
    "priority": handle_priority,
    "create": handle_create,
    "update": handle_update,
    "delete": handle_delete,
    "comment": handle_comment,
    "edge": handle_edge,
    "restore": handle_restore,
    "resolve": handle_resolve,
    "reopen": handle_reopen,
    "nudge": handle_nudge,
}

# Endpoints that work without a loaded board (health checks).
NO_BOARD_ENDPOINTS = {"ping"}

__all__ = ["HANDLERS", "NO_BOARD_ENDPOINTS"]

# EOF
