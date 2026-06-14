#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SAC host-registry adapter for the fleet dashboard (Phase 2).

For the local host + any registered peers, returns a small JSON-friendly
summary of the registry by shelling out to ``sac host list --json`` —
the same binary the operator uses interactively, so config / scope /
alias resolution comes for free.

Why ``sac host`` and not parsing the registry file directly?

- ``sac`` already handles ``~/.config/scitex/hosts.yaml`` discovery,
  schema migration, and per-scope merging (local vs. shared)
- It transparently surfaces the active config path so the FE can show
  exactly which file is in play
- The CLI is the operator's canonical entry point — calling the same
  binary means the dashboard never disagrees with their terminal

Failure mode is uniform — any of these RAISE
:class:`FleetAdapterError`:

- ``sac`` binary missing from ``PATH``
- ``sac host list --json`` exits non-zero (config malformed, registry
  schema mismatch, …)
- ``sac`` returns malformed JSON or an empty payload

The view layer (``hosts_view.py``) catches the adapter error and
surfaces it as an HTTP 500 with the message in the body so the FE can
render a single red ``!`` icon with the error in the tooltip.

Architectural principles (HARD):

- fail-loud / no-silent-fallback
- registry-sourced — read from ``sac host list --json``; do NOT cache
  state in scitex-todo
- NO hardcoded proper nouns — no ``"ywata-note-win"`` or ``"spartan"``
  literals; everything comes from the registry
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from ._errors import FleetAdapterError

# Subprocess timeout for one ``sac host list`` call. The CLI walks at
# most a handful of small YAML files + the local interface list, so 10s
# is generous; in practice it returns in ~50ms on a healthy box.
_SAC_TIMEOUT = 10


def _sac_binary() -> str:
    """Locate ``sac``; raise FleetAdapterError if absent.

    The fail-loud contract: surfacing "sac not installed" must NOT
    silently fall back to an empty-hosts payload — that would lie to
    the operator about what's in their registry.
    """
    exe = shutil.which("sac")
    if exe is None:
        raise FleetAdapterError(
            "sac CLI not found on PATH — install scitex-agent-container "
            "to enable the fleet host-geometry panel."
        )
    return exe


def _sac_json(args: list[str]) -> Any:
    """Run ``sac`` with ``args``, parse stdout as JSON, raise on any
    failure mode (non-zero exit, timeout, malformed JSON, empty body).

    The error message includes the command and a trimmed stderr so the
    operator can copy-paste and reproduce.
    """
    exe = _sac_binary()
    cmd = [exe, *args]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_SAC_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FleetAdapterError(
            f"sac call timed out after {_SAC_TIMEOUT}s: {' '.join(cmd)}"
        ) from exc
    except OSError as exc:
        raise FleetAdapterError(
            f"sac call failed to start: {' '.join(cmd)}: {exc}"
        ) from exc

    if proc.returncode != 0:
        stderr_excerpt = (proc.stderr or "").strip().splitlines()
        excerpt = " | ".join(stderr_excerpt[:3]) or "(no stderr)"
        raise FleetAdapterError(
            f"sac exited {proc.returncode} for {' '.join(cmd)}: {excerpt}"
        )

    out = (proc.stdout or "").strip()
    if not out:
        raise FleetAdapterError(
            f"sac returned empty body for {' '.join(cmd)}"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise FleetAdapterError(
            f"sac returned malformed JSON for {' '.join(cmd)}: {exc}"
        ) from exc


def fetch_hosts() -> dict[str, Any]:
    """Fetch the host-registry snapshot for the local host + peers.

    Returns a dict shaped like::

        {
          "config_path": "<path or null>",
          "local": {
            "name": "<hostname>",
            "scope": "local",
            "aliases": {},
            "interfaces": [{"iface","addr","family"}, ...],
          },
          "peers": [<peer rows>],
        }

    Raises :class:`FleetAdapterError` on any upstream / parsing failure;
    the view layer catches it and returns HTTP 500 with the message in
    the body so the FE can render a red ``!`` icon with the verbatim
    adapter message in the tooltip.

    The returned shape is whatever ``sac host list --json`` emits today —
    we deliberately do NOT remap field names or fabricate missing keys
    here, so the FE adapts to upstream changes without a Python-side
    rebuild.

    .. note::
       TODO(phase-2.b) — enrich each row with cpu / mem / SLURM
       reservations once ``sac host`` ships those fields. The mesh-status
       panel (Phase 2.c) will join this payload with the per-peer mesh
       reachability map. Until then the FE renders interface + peer
       counts only.
    """
    payload = _sac_json(["host", "list", "--json"])

    if not isinstance(payload, dict):
        raise FleetAdapterError(
            f"unexpected payload shape from `sac host list --json`: "
            f"expected object, got {type(payload).__name__}"
        )

    # The local sub-document is the load-bearing contract — without it
    # the FE has nothing to render. We surface its absence as fail-loud
    # rather than guessing a hostname from ``socket.gethostname()``
    # (which would diverge from sac's view of the world).
    local = payload.get("local")
    if not isinstance(local, dict):
        raise FleetAdapterError(
            f"`sac host list --json` returned no ``local`` object — "
            f"sac may be mis-configured: keys={list(payload.keys())!r}"
        )
    if not local.get("name"):
        raise FleetAdapterError(
            f"`sac host list --json` ``local`` object has no ``name`` — "
            f"sac may be mis-configured: {local!r}"
        )

    # Peers is optional (a fresh install has zero peers). Default to an
    # empty list — a 0-peer registry is a legitimate steady state, not
    # an adapter error.
    peers = payload.get("peers")
    if peers is None:
        peers = []
    if not isinstance(peers, list):
        raise FleetAdapterError(
            f"`sac host list --json` ``peers`` is not a list: "
            f"{type(peers).__name__}"
        )

    return {
        "config_path": payload.get("config_path"),
        "local": local,
        "peers": peers,
    }


__all__ = ["fetch_hosts"]

# EOF
