#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-dashboard adapters — registry-reader pattern (Phase 0 harness).

Phase 1 shipped the CI-status pills strip: each watched repo's GitHub CI
state read live from ``gh``. Phase 2 (this wave) adds the
host-geometry panel: the local host + registered peers read live from
``sac host list --json`` (upstream is the truth — we do not cache state
in scitex-todo). The same harness will host mesh / timing / chat panels
in subsequent waves.

Design principles (HARD):

- fail-loud — adapters RAISE :class:`FleetAdapterError` on missing,
  malformed, or unreachable data. No silent fallback to defaults.
- registry-sourced — read authoritative state (GitHub CI / sac hosts),
  never duplicate it locally.
- NO hardcoded proper nouns — the watched-repo list is config-driven
  (``~/.scitex/todo/dashboard.yaml`` or env
  ``SCITEX_TODO_FLEET_CI_REPOS=foo,bar``); the host list comes straight
  from ``sac`` so there is no proper-noun literal anywhere in this
  package.

Public API (this ``__init__``):

- :class:`FleetAdapterError`
- :func:`fleet_config_load`   — read the dashboard config (no raise on
  absence; raises on malformed YAML)
- :func:`fetch_repo_ci_status` — fetch one repo's CI summary via ``gh``
- :func:`fleet_ci_status_view` — Django view for ``/fleet/ci-status``
- :func:`fetch_hosts`          — fetch the local + peer host registry
  via ``sac host list --json``
- :func:`fleet_hosts_view`     — Django view for ``/fleet/hosts``
"""

from ._config import fleet_config_load
from ._errors import FleetAdapterError
from .ci_status_view import fleet_ci_status_view
from .gh_ci import fetch_repo_ci_status
from .hosts_view import fleet_hosts_view
from .mesh_view import fleet_mesh_view
from .sac_hosts import fetch_hosts
from .sac_mesh import fetch_mesh
from .timing import compute_timing, task_durations
from .timing_view import fleet_timing_view

__all__ = [
    "FleetAdapterError",
    "compute_timing",
    "fetch_hosts",
    "fetch_mesh",
    "fetch_repo_ci_status",
    "fleet_ci_status_view",
    "fleet_config_load",
    "fleet_hosts_view",
    "fleet_mesh_view",
    "fleet_timing_view",
    "task_durations",
]

# EOF
