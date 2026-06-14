#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view: ``GET /fleet/ci-status``.

Returns a JSON document the front-end polls every 30s to render the
CI-status pills strip in the board toolbar.

Response shape (200)::

    {
      "repos": [
        {
          "slug": "owner/name", "branch": "main",
          "head_sha": "abc1234", "overall": "success",
          "checks": [...]
        },
        # per-repo error sub-document:
        {"slug": "owner/missing", "error": "<adapter message>"}
      ],
      "config": {"repos": ["owner/name", "owner/missing"]}
    }

If the config itself can't be loaded (malformed YAML), respond 500 with
``{"error": "<message>"}`` so the front-end can render a single visible
"adapter error" state rather than a silently-empty strip.

The harness contract: ONE bad repo does NOT break the page (caught
per-repo); CONFIG failure DOES (the whole strip is unconfigurable).
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from ._errors import FleetAdapterError
from ._config import fleet_config_load
from .gh_ci import fetch_repo_ci_status

logger = logging.getLogger(__name__)


def fleet_ci_status_view(request):  # noqa: ARG001 — request unused (GET only)
    """Serve ``/fleet/ci-status``. See module docstring for the contract."""
    try:
        config = fleet_config_load()
    except FleetAdapterError as exc:
        logger.warning("[fleet/ci-status] config load failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)

    repos: list[str] = config["fleet"]["ci_status"]["repos"]
    per_repo: list[dict] = []
    for slug in repos:
        try:
            per_repo.append(fetch_repo_ci_status(slug))
        except FleetAdapterError as exc:
            # Per-repo trap — one dead adapter mustn't blank the strip.
            # The FE renders this as a red `!` pill with the message in
            # the tooltip so the operator sees exactly what broke.
            logger.info("[fleet/ci-status] %s -> adapter error: %s", slug, exc)
            per_repo.append({"slug": slug, "error": str(exc)})

    return JsonResponse(
        {
            "repos": per_repo,
            "config": {"repos": repos},
        }
    )


__all__ = ["fleet_ci_status_view"]

# EOF
