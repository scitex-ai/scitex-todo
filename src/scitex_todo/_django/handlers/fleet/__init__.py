#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-dashboard adapters — registry-reader pattern (Phase 0 harness).

Phase 1 ships the CI-status pills strip: each watched repo's GitHub CI
state read live from ``gh`` (the upstream is the truth — we do not cache
state in scitex-todo). The same harness will host hosts / mesh / timing
/ chat panels in subsequent waves.

Design principles (HARD):

- fail-loud — adapters RAISE :class:`FleetAdapterError` on missing,
  malformed, or unreachable data. No silent fallback to defaults.
- registry-sourced — read authoritative state (GitHub CI here), never
  duplicate it locally.
- NO hardcoded proper nouns — the watched-repo list is config-driven
  (``~/.scitex/todo/dashboard.yaml`` or env
  ``SCITEX_TODO_FLEET_CI_REPOS=foo,bar``).

Public API (this ``__init__``):

- :class:`FleetAdapterError`
- :func:`fleet_config_load`   — read the dashboard config (no raise on
  absence; raises on malformed YAML)
- :func:`fetch_repo_ci_status` — fetch one repo's CI summary via ``gh``
"""

from ._config import fleet_config_load
from ._errors import FleetAdapterError
from .ci_status_view import fleet_ci_status_view
from .gh_ci import fetch_repo_ci_status

__all__ = [
    "FleetAdapterError",
    "fleet_config_load",
    "fetch_repo_ci_status",
    "fleet_ci_status_view",
]

# EOF
