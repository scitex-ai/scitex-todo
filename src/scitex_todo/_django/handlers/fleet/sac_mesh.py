#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SAC agent-mesh adapter for the fleet dashboard (Phase 3).

Returns the directed mesh graph: nodes = agents (registered peers +
self-registered comms-nodes) read from ``sac a2a list --json``; edges =
the ``comms_grants`` ACL rows read from ``sac a2a grants --json``.

Why TWO shellouts (and not one ``state.db`` peek)?

- ``sac`` already merges the container Registry + the self-registered
  comms-nodes from the local listen server's ``GET /agents`` endpoint
- ``sac a2a grants`` wraps the canonical ``list_comms_grants`` reader so
  the dashboard sees the SAME rows the operator's terminal does
- Reaching into ``state.db`` directly would re-implement the bearer
  token + schema discovery and risk drifting from upstream over time

Failure mode is uniform — any of these RAISE
:class:`FleetAdapterError`:

- ``sac`` binary missing from ``PATH``
- either ``sac a2a list --json`` or ``sac a2a grants --json`` exits
  non-zero (listen server down, bearer missing, schema mismatch, …)
- ``sac`` returns malformed JSON or an empty body
- the JSON shape is not the expected list-of-objects

The view layer (``mesh_view.py``) catches the adapter error and surfaces
it as HTTP 500 with the message in the body so the FE can render a
single red ``!`` icon with the verbatim adapter message in the tooltip.

Architectural principles (HARD):

- fail-loud / no-silent-fallback
- registry-sourced — read from ``sac a2a list`` + ``sac a2a grants``;
  do NOT cache state in scitex-todo
- NO hardcoded proper nouns — no ``"lead"`` or ``"proj-scitex-todo"``
  literals; every name comes from the registry

Edge ``allow`` semantics:

- A row in ``comms_grants`` is an explicit ALLOW from ``sender →
  target``. That maps to ``edges[i]["allow"] = True``.
- The mesh adapter currently emits ONLY allow-edges because the
  inverse ``comms_blocks`` table has no enumeration CLI yet (sac
  v0.21.11). The shape supports ``allow=False`` so the FE renders
  both, and the deny-edge wiring lands in phase-3.b once the upstream
  exposes a listing surface — see TODO marker below.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from ._errors import FleetAdapterError

# Subprocess timeout for one ``sac a2a ...`` call. Both list + grants
# are local lookups (HTTP to 127.0.0.1 + a sqlite read); 10s is the
# agreed Phase-3 budget matching Phase-2's sac-hosts adapter.
_SAC_TIMEOUT = 10


def _sac_binary() -> str:
    """Locate ``sac``; raise FleetAdapterError if absent.

    The fail-loud contract: surfacing "sac not installed" must NOT
    silently fall back to an empty-mesh payload — that would lie to
    the operator about what is on their mesh.
    """
    exe = shutil.which("sac")
    if exe is None:
        raise FleetAdapterError(
            "sac CLI not found on PATH — install scitex-agent-container "
            "to enable the fleet agent-mesh panel."
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


# ─── individual fetchers ────────────────────────────────────────────────


def _fetch_peers() -> list[dict[str, Any]]:
    """Return the raw peer list from ``sac a2a list --json``.

    Rows are a mix of container Registry agents (have ``config`` /
    ``pid``) and self-registered comms-nodes (have ``kind == "comms-
    node"``). The mesh adapter normalises that into a uniform
    ``{name, scope}`` dict downstream — this fetcher returns the raw
    payload so callers can keep the upstream-truth contract.
    """
    payload = _sac_json(["a2a", "list", "--json"])
    if not isinstance(payload, list):
        raise FleetAdapterError(
            f"unexpected payload shape from `sac a2a list --json`: "
            f"expected array, got {type(payload).__name__}"
        )
    return payload


def _fetch_grants() -> list[dict[str, Any]]:
    """Return the raw grants list from ``sac a2a grants --json``.

    An empty list ``[]`` is a legitimate steady state (a fresh install
    has no grants); the adapter does NOT raise on that. Each row has at
    least ``sender`` + ``target`` keys; ``note`` is optional and
    surfaced verbatim to the FE for tooltips.
    """
    payload = _sac_json(["a2a", "grants", "--json"])
    if not isinstance(payload, list):
        raise FleetAdapterError(
            f"unexpected payload shape from `sac a2a grants --json`: "
            f"expected array, got {type(payload).__name__}"
        )
    return payload


# ─── normalisation ──────────────────────────────────────────────────────


def _normalise_agents(
    peers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project ``sac a2a list`` rows into the FE's ``agents`` shape.

    Output rows carry:
      - ``name``    — required; rows without a name are skipped (the FE
                       can't render an unnamed node).
      - ``scope``   — ``"local"`` for rows that look like container
                       Registry agents on this host (have a ``pid`` or
                       a ``config``), ``"peer"`` otherwise (comms-nodes
                       advertised by other hosts).
      - ``status``  — ``"online"`` if the row carries a recent
                       ``updated_at`` / ``started_at`` heartbeat, else
                       ``"unknown"``. We don't have a hard offline
                       signal in v0.21.11 — that lands in phase-3.b.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in peers:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        # Container Registry agents have a ``pid`` (sac agents start)
        # or an explicit ``config`` (spec.yaml path). Comms-nodes are
        # tagged via ``kind == "comms-node"``.
        scope: str
        if row.get("kind") == "comms-node":
            scope = "peer"
        elif row.get("pid") or row.get("config"):
            scope = "local"
        else:
            scope = "peer"
        # Heartbeat freshness — present in BOTH row flavors. We don't
        # interpret the timestamp here (the FE has the wall clock and
        # can render a relative "last seen" if it wants); we just
        # report whether SAC believes the row is fresh.
        has_heartbeat = (
            row.get("updated_at") is not None
            or row.get("started_at") is not None
            or row.get("registered_at") is not None
        )
        status = "online" if has_heartbeat else "unknown"
        out.append({"name": name, "scope": scope, "status": status})
    return out


def _normalise_edges(
    grants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project ``sac a2a grants`` rows into the FE's ``edges`` shape.

    Each grant row maps to ONE allow-edge ``{source, target, allow:
    true}``. Rows without both ``sender`` + ``target`` are skipped
    (defensive — the canonical reader emits a strict shape, but a
    schema bump upstream shouldn't blank the whole panel).

    The ``note`` audit annotation is surfaced verbatim under
    ``edges[i]["note"]`` so the FE tooltip can show "allowed by ticket
    PA-512" etc. without a second roundtrip.
    """
    out: list[dict[str, Any]] = []
    for row in grants:
        if not isinstance(row, dict):
            continue
        sender = row.get("sender")
        target = row.get("target")
        if not isinstance(sender, str) or not sender:
            continue
        if not isinstance(target, str) or not target:
            continue
        edge: dict[str, Any] = {
            "source": sender,
            "target": target,
            "allow": True,
        }
        note = row.get("note")
        if isinstance(note, str) and note:
            edge["note"] = note
        out.append(edge)
    return out


# ─── public surface ─────────────────────────────────────────────────────


def fetch_mesh() -> dict[str, Any]:
    """Fetch the agent-mesh snapshot for the local listen server.

    Returns a dict shaped like::

        {
          "agents": [{"name": "...", "scope": "local"|"peer",
                      "status": "online"|"offline"|"unknown"}, ...],
          "edges":  [{"source": "...", "target": "...", "allow": true,
                      "note"?: "..."}, ...],
          "config_path": null,
          "source_versions": {
            "peers":  "sac a2a list --json",
            "grants": "sac a2a grants --json",
          },
        }

    Raises :class:`FleetAdapterError` on any upstream / parsing failure;
    the view layer catches it and returns HTTP 500 with the message in
    the body so the FE can render a red ``!`` icon with the verbatim
    adapter message in the tooltip.

    .. note::
       TODO(phase-3.b) — wire deny-edges once ``sac a2a blocks --list``
       (or equivalent) ships. The shape already supports ``allow:
       false`` so the FE rendering stays unchanged.
    .. note::
       TODO(phase-3.b) — derive a hard offline signal from the
       last-heartbeat delta once ``sac a2a list`` surfaces a freshness
       threshold; for now ``status`` is ``online`` whenever the row
       carries any heartbeat at all.
    .. note::
       TODO(phase-3.b) — emit ``config_path`` once ``sac`` surfaces the
       state.db path on ``sac a2a list``; today the path is implicit
       (state.db location is sac-controlled), so we report null and
       the FE tooltip omits the line.
    """
    peers_raw = _fetch_peers()
    grants_raw = _fetch_grants()

    agents = _normalise_agents(peers_raw)
    edges = _normalise_edges(grants_raw)

    # Defensive: edges that reference an unknown agent are still
    # rendered (the operator may have granted ahead of registration);
    # we surface a synthetic agent node with scope=peer so the FE
    # always has something to point the edge at.
    known_names = {a["name"] for a in agents}
    for edge in edges:
        for endpoint_name in (edge["source"], edge["target"]):
            if endpoint_name not in known_names:
                agents.append(
                    {
                        "name": endpoint_name,
                        "scope": "peer",
                        "status": "unknown",
                    }
                )
                known_names.add(endpoint_name)

    return {
        "agents": agents,
        "edges": edges,
        # TODO(phase-3.b): wire state.db path once sac surfaces it.
        "config_path": None,
        "source_versions": {
            "peers": "sac a2a list --json",
            "grants": "sac a2a grants --json",
        },
    }


__all__ = ["fetch_mesh"]

# EOF
