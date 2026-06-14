#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SAC agent-mesh adapter (no mocks — STX-NM/PA-306).

Covers the ``fetch_mesh`` shape contract + the fail-loud behavior:

- When ``sac`` is on PATH and the listen server is reachable,
  ``fetch_mesh()`` returns a dict with the load-bearing ``agents`` +
  ``edges`` + ``config_path`` + ``source_versions`` keys.
- When ``sac`` is artificially unavailable (we clobber PATH via the
  PA-306-compliant ``env`` fixture, NOT monkeypatch),
  ``fetch_mesh`` raises :class:`FleetAdapterError` with a message that
  names "sac".
- The module-surface contract + the ``_SAC_TIMEOUT`` constant are
  pinned so a careless bump triggers a failing test.

No mocks AND no monkeypatch (PA-306). Env / cwd manipulation routes
through the suite's :func:`env` fixture (``tests/scitex_todo/conftest.py``).
"""

from __future__ import annotations

import shutil

import pytest

from scitex_todo._django.handlers.fleet import (
    FleetAdapterError,
    fetch_mesh,
)
from scitex_todo._django.handlers.fleet import sac_mesh as sac_mesh_mod


# ─── fail-loud: missing binary ──────────────────────────────────────────


def test_fetch_mesh_missing_binary_raises(env) -> None:
    """Surfacing "sac not installed" must NOT silently fall back to an
    empty-mesh success — that would lie to the operator about what is
    on their mesh. Simulate the missing binary by clobbering PATH
    (PA-306-compliant via the ``env`` fixture, not monkeypatch)."""
    env.set("PATH", "")
    with pytest.raises(FleetAdapterError) as excinfo:
        fetch_mesh()
    # The message must name "sac" so the operator knows what is missing.
    assert "sac" in str(excinfo.value).lower()


# ─── happy path (gated on sac availability) ─────────────────────────────


_SAC_AVAILABLE = shutil.which("sac") is not None


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_fetch_mesh_returns_load_bearing_keys() -> None:
    """When sac IS available, the adapter returns a dict with the
    load-bearing ``agents`` + ``edges`` + ``config_path`` +
    ``source_versions`` keys. The FE consumes all four.

    We deliberately do NOT assert specific agent names or grant
    counts — the registry is environment-specific and asserting
    proper-noun literals would re-introduce a smell the architecture
    forbids.
    """
    out = fetch_mesh()
    assert isinstance(out, dict)
    for key in ("agents", "edges", "config_path", "source_versions"):
        assert key in out, f"missing load-bearing key: {key!r}"
    assert isinstance(out["agents"], list)
    assert isinstance(out["edges"], list)
    # ``source_versions`` is a dict with ``peers`` + ``grants`` —
    # surfaced in the FE tooltip so the operator can see WHICH source
    # the panel is reading from. Pin both keys.
    assert isinstance(out["source_versions"], dict)
    assert "peers" in out["source_versions"]
    assert "grants" in out["source_versions"]


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_fetch_mesh_agent_rows_have_required_fields() -> None:
    """Each agent row must carry ``name`` (string) + ``scope`` (one of
    ``local`` / ``peer``) + ``status`` (one of the enum values). The
    FE renders these fields directly; missing keys would break the
    panel silently."""
    out = fetch_mesh()
    for row in out["agents"]:
        assert isinstance(row, dict)
        assert isinstance(row.get("name"), str)
        assert row["name"], "agent name must be non-empty"
        assert row.get("scope") in ("local", "peer")
        assert row.get("status") in ("online", "offline", "unknown")


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_fetch_mesh_edge_rows_have_required_fields() -> None:
    """Each edge row must carry ``source`` + ``target`` (both strings)
    + ``allow`` (bool). The FE picks the CSS token off ``allow``."""
    out = fetch_mesh()
    for edge in out["edges"]:
        assert isinstance(edge, dict)
        assert isinstance(edge.get("source"), str)
        assert isinstance(edge.get("target"), str)
        assert isinstance(edge.get("allow"), bool)


# ─── module-surface contract ────────────────────────────────────────────


def test_sac_mesh_module_exports_fetch_mesh() -> None:
    """Lock the public surface so a rename downstream forces a test
    update. Operators search for this literal when debugging."""
    assert hasattr(sac_mesh_mod, "fetch_mesh")
    assert "fetch_mesh" in sac_mesh_mod.__all__


def test_sac_mesh_module_timeout_constant_pinned() -> None:
    """Pin the subprocess timeout so a careless bump (e.g. to 120s)
    triggers a test that asks "are you sure the operator wants to wait
    that long for the dashboard?". 10s is the agreed Phase-3 budget,
    matching the Phase-2 hosts adapter."""
    assert sac_mesh_mod._SAC_TIMEOUT == 10


def test_sac_mesh_has_phase_3_b_todo_markers() -> None:
    """Three Phase 3.b landing points are reserved:

    - deny-edges (once ``sac a2a blocks --list`` ships)
    - hard offline status (once ``sac a2a list`` exposes a freshness
      threshold)
    - state.db config_path (once ``sac`` surfaces it)

    Pin their presence so a refactor doesn't lose the follow-up
    markers.
    """
    import inspect

    src = inspect.getsource(sac_mesh_mod)
    # At least one ``TODO(phase-3.b)`` marker must be present —
    # tightening to a count is brittle if a future cleanup consolidates.
    assert "TODO(phase-3.b)" in src


# ─── normalisation helpers ──────────────────────────────────────────────


def test_normalise_agents_dedupes_by_name() -> None:
    """Duplicate names in the raw peer list collapse to one node so
    the radial layout doesn't draw two circles at the same point. The
    first occurrence wins (which matches sac's own list ordering)."""
    raw = [
        {"name": "a", "kind": "comms-node", "updated_at": 1},
        {"name": "a", "kind": "comms-node", "updated_at": 2},
        {"name": "b", "pid": 1234, "config": "/path"},
    ]
    out = sac_mesh_mod._normalise_agents(raw)
    assert [r["name"] for r in out] == ["a", "b"]
    # Container Registry rows (``pid`` or ``config``) become local;
    # ``kind: comms-node`` becomes peer.
    assert out[0]["scope"] == "peer"
    assert out[1]["scope"] == "local"


def test_normalise_agents_skips_rows_without_name() -> None:
    """Defensive: rows without a name can't be rendered. They're
    skipped silently rather than raising — a schema bump upstream
    shouldn't blank the whole panel for one bad row."""
    raw = [
        {"kind": "comms-node"},  # no name
        {"name": "", "kind": "comms-node"},  # empty name
        {"name": "ok", "kind": "comms-node", "updated_at": 1},
    ]
    out = sac_mesh_mod._normalise_agents(raw)
    assert [r["name"] for r in out] == ["ok"]
    assert out[0]["status"] == "online"


def test_normalise_edges_maps_grants_to_allow_edges() -> None:
    """Each grant row → one allow-edge. The audit note is surfaced
    verbatim under ``edges[i]['note']`` so the FE tooltip can display
    it without a second roundtrip."""
    raw = [
        {"sender": "a", "target": "b", "note": "ticket-PA-1"},
        {"sender": "b", "target": "c"},
    ]
    out = sac_mesh_mod._normalise_edges(raw)
    assert len(out) == 2
    assert out[0] == {
        "source": "a",
        "target": "b",
        "allow": True,
        "note": "ticket-PA-1",
    }
    assert out[1] == {"source": "b", "target": "c", "allow": True}


def test_normalise_edges_skips_malformed_rows() -> None:
    """Defensive: rows missing ``sender`` or ``target`` can't be drawn.
    Skipped rather than raising — same fail-soft-on-bad-row rationale
    as ``_normalise_agents``."""
    raw = [
        {"sender": "a"},  # no target
        {"target": "b"},  # no sender
        {"sender": "", "target": "x"},  # empty sender
        {"sender": "ok", "target": "fine"},
    ]
    out = sac_mesh_mod._normalise_edges(raw)
    assert len(out) == 1
    assert out[0]["source"] == "ok"
    assert out[0]["target"] == "fine"

# EOF
