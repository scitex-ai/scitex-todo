#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view: ``GET /fleet/hosts``.

Returns a JSON document the front-end polls every 30s (same cadence as
the CI-status pills strip) to render the host-geometry panel next to
the existing CI pills.

Response shape (200) is whatever
:func:`scitex_todo._django.handlers.fleet.sac_hosts.fetch_hosts` emits,
which today is::

    {
      "config_path": "<path or null>",
      "local": {
        "name": "<hostname>",
        "scope": "local",
        "aliases": {},
        "interfaces": [{"iface","addr","family"}, ...]
      },
      "peers": [<peer rows>]
    }

If the adapter raises (``sac`` not installed, non-zero exit, malformed
JSON, missing ``local.name``), the view returns HTTP 500 with
``{"error": "<message>"}`` so the front-end can render a single visible
red ``!`` icon with the error in the tooltip rather than a silently-empty
panel.

The harness contract differs from the CI-pills strip: the CI strip has
N pills (one per repo) so per-pill errors are caught individually; the
host-geometry panel renders ONE box (the local host + a peer count), so
adapter failure blanks the box and surfaces as a 500. Same fail-loud
spirit, different cardinality.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from ._errors import FleetAdapterError
from .sac_hosts import fetch_hosts

logger = logging.getLogger(__name__)


def fleet_hosts_view(request):  # noqa: ARG001 — request unused (GET only)
    """Serve ``/fleet/hosts``. See module docstring for the contract."""
    try:
        payload = fetch_hosts()
    except FleetAdapterError as exc:
        logger.warning("[fleet/hosts] adapter error: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(payload)


__all__ = ["fleet_hosts_view"]

# EOF
