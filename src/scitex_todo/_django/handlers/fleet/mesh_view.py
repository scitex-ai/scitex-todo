#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view: ``GET /fleet/mesh``.

Returns the directed agent-mesh graph (nodes = registered agents,
edges = ``comms_grants`` ACL rows) for the FE's ``FleetMeshPanel`` to
poll every 30s.

Response shape (200) is whatever
:func:`scitex_todo._django.handlers.fleet.sac_mesh.fetch_mesh` emits,
which today is::

    {
      "agents": [{"name": "...", "scope": "local"|"peer",
                  "status": "online"|"offline"|"unknown"}, ...],
      "edges":  [{"source": "...", "target": "...", "allow": true,
                  "note"?: "..."}, ...],
      "config_path": null,
      "source_versions": {"peers": "...", "grants": "..."}
    }

If the adapter raises (``sac`` not installed, listen server unreachable,
malformed JSON), the view returns HTTP 500 with ``{"error": "<msg>"}``
so the FE can render a single red ``!`` icon with the error in the
tooltip rather than a silently-empty panel.

Method discipline:

- ``GET`` returns 200 / 500 as above.
- Anything else (``POST`` / ``PUT`` / ``DELETE``) returns 405 — the
  endpoint is strictly read-only; mutations go through the ``sac a2a
  grant`` / ``revoke`` CLI, not through scitex-todo.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from ._errors import FleetAdapterError
from .sac_mesh import fetch_mesh

logger = logging.getLogger(__name__)


def fleet_mesh_view(request):
    """Serve ``/fleet/mesh``. See module docstring for the contract."""
    if request.method != "GET":
        return JsonResponse(
            {"error": f"method {request.method} not allowed"},
            status=405,
        )
    try:
        payload = fetch_mesh()
    except FleetAdapterError as exc:
        logger.warning("[fleet/mesh] adapter error: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(payload)


__all__ = ["fleet_mesh_view"]

# EOF
