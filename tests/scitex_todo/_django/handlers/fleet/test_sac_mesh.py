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
import subprocess

import pytest

from scitex_todo._django.handlers.fleet import (
    FleetAdapterError,
    fetch_mesh,
)
from scitex_todo._django.handlers.fleet import sac_mesh as sac_mesh_mod


# ─── fail-loud: missing binary ──────────────────────────────────────────


def test_fetch_mesh_missing_binary_raises_raises_fleetadaptererror(env) -> None:
    """Surfacing "sac not installed" must NOT silently fall back to an
    empty-mesh success — that would lie to the operator about what is
    on their mesh. Simulate the missing binary by clobbering PATH
    (PA-306-compliant via the ``env`` fixture, not monkeypatch)."""
    # Arrange
    env.set("PATH", "")
    # Act
    # Assert
    # The message must name "sac" so the operator knows what is missing.
    with pytest.raises(FleetAdapterError, match="(?i)sac"):
        fetch_mesh()


# ─── happy path (gated on sac availability) ─────────────────────────────


def _sac_mesh_functional() -> bool:
    """True iff ``sac a2a list --json`` actually SUCCEEDS in this env.

    ``shutil.which("sac")`` only proves the binary is on PATH — on the
    self-hosted CI runner sac IS installed but ``sac a2a list`` exits 1
    ("no listen bearer token"), so a presence check let these tests run
    and fail. Probe the real command (the one ``_fetch_peers`` shells)
    and skip unless it returns 0. Mirrors test__mesh_view.py (#218)."""
    exe = shutil.which("sac")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "a2a", "list", "--json"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_SAC_FUNCTIONAL = _sac_mesh_functional()


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_returns_a_dict() -> None:
    """When sac IS available, the adapter returns a dict.

    We deliberately do NOT assert specific agent names or grant
    counts — the registry is environment-specific and asserting
    proper-noun literals would re-introduce a smell the architecture
    forbids.
    """
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert
    assert isinstance(out, dict)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_has_all_load_bearing_keys() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert — the FE consumes all four keys.
    assert {"agents", "edges", "config_path", "source_versions"} <= set(out)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_agents_value_is_a_list() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert
    assert isinstance(out["agents"], list)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_edges_value_is_a_list() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert
    assert isinstance(out["edges"], list)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_source_versions_is_a_dict() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert
    assert isinstance(out["source_versions"], dict)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_source_versions_has_peers_key() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert — surfaced in the FE tooltip so the operator sees the source.
    assert "peers" in out["source_versions"]


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_source_versions_has_grants_key() -> None:
    # Arrange
    # Act
    out = fetch_mesh()
    # Assert
    assert "grants" in out["source_versions"]


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_agent_rows_are_dicts() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    rows = out["agents"]
    # Assert
    assert all(isinstance(row, dict) for row in rows)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_agent_rows_have_non_empty_name() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    rows = out["agents"]
    # Assert — the FE renders ``name`` directly.
    assert all(isinstance(row.get("name"), str) and row["name"] for row in rows)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_agent_rows_have_valid_scope() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    rows = out["agents"]
    # Assert
    assert all(row.get("scope") in ("local", "peer") for row in rows)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_agent_rows_have_valid_status() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    rows = out["agents"]
    # Assert
    assert all(row.get("status") in ("online", "offline", "unknown") for row in rows)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_edge_rows_are_dicts() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    edges = out["edges"]
    # Assert
    assert all(isinstance(edge, dict) for edge in edges)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_edge_rows_have_string_source() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    edges = out["edges"]
    # Assert
    assert all(isinstance(edge.get("source"), str) for edge in edges)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_edge_rows_have_string_target() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    edges = out["edges"]
    # Assert
    assert all(isinstance(edge.get("target"), str) for edge in edges)


@pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason="sac a2a list --json non-functional here (not installed / no listen bearer token)",
)
def test_fetch_mesh_edge_rows_have_bool_allow() -> None:
    # Arrange
    out = fetch_mesh()
    # Act
    edges = out["edges"]
    # Assert — the FE picks the CSS token off ``allow``.
    assert all(isinstance(edge.get("allow"), bool) for edge in edges)


# ─── module-surface contract ────────────────────────────────────────────


def test_sac_mesh_module_exports_fetch_mesh_hasattr() -> None:
    """Lock the public surface so a rename downstream forces a test
    update. Operators search for this literal when debugging."""
    # Arrange
    # Act
    # Assert
    assert hasattr(sac_mesh_mod, "fetch_mesh")


def test_sac_mesh_module_exports_fetch_mesh_all_contains() -> None:
    """Lock the public surface so a rename downstream forces a test
    update. Operators search for this literal when debugging."""
    # Arrange
    # Act
    # Assert
    assert "fetch_mesh" in sac_mesh_mod.__all__


def test_sac_mesh_module_timeout_constant_pinned() -> None:
    """Pin the subprocess timeout so a careless bump (e.g. to 120s)
    triggers a test that asks "are you sure the operator wants to wait
    that long for the dashboard?". 10s is the agreed Phase-3 budget,
    matching the Phase-2 hosts adapter."""
    # Arrange
    # Act
    # Assert
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
    # Arrange
    import inspect

    # Act
    src = inspect.getsource(sac_mesh_mod)
    # At least one ``TODO(phase-3.b)`` marker must be present —
    # tightening to a count is brittle if a future cleanup consolidates.
    # Assert
    assert "TODO(phase-3.b)" in src


# ─── normalisation helpers ──────────────────────────────────────────────


def test_normalise_agents_dedupes_by_name_case_1() -> None:
    """Duplicate names in the raw peer list collapse to one node so
    the radial layout doesn't draw two circles at the same point. The
    first occurrence wins (which matches sac's own list ordering)."""
    # Arrange
    raw = [
        {"name": "a", "kind": "comms-node", "updated_at": 1},
        {"name": "a", "kind": "comms-node", "updated_at": 2},
        {"name": "b", "pid": 1234, "config": "/path"},
    ]
    # Act
    out = sac_mesh_mod._normalise_agents(raw)
    # Assert
    # Container Registry rows (``pid`` or ``config``) become local;
    # ``kind: comms-node`` becomes peer.
    assert [r["name"] for r in out] == ["a", "b"]


def test_normalise_agents_dedupes_by_name_scope() -> None:
    """Duplicate names in the raw peer list collapse to one node so
    the radial layout doesn't draw two circles at the same point. The
    first occurrence wins (which matches sac's own list ordering)."""
    # Arrange
    raw = [
        {"name": "a", "kind": "comms-node", "updated_at": 1},
        {"name": "a", "kind": "comms-node", "updated_at": 2},
        {"name": "b", "pid": 1234, "config": "/path"},
    ]
    # Act
    out = sac_mesh_mod._normalise_agents(raw)
    # Assert
    # Container Registry rows (``pid`` or ``config``) become local;
    # ``kind: comms-node`` becomes peer.
    assert out[0]["scope"] == "peer"


def test_normalise_agents_dedupes_by_name_scope_2() -> None:
    """Duplicate names in the raw peer list collapse to one node so
    the radial layout doesn't draw two circles at the same point. The
    first occurrence wins (which matches sac's own list ordering)."""
    # Arrange
    raw = [
        {"name": "a", "kind": "comms-node", "updated_at": 1},
        {"name": "a", "kind": "comms-node", "updated_at": 2},
        {"name": "b", "pid": 1234, "config": "/path"},
    ]
    # Act
    out = sac_mesh_mod._normalise_agents(raw)
    # Assert
    # Container Registry rows (``pid`` or ``config``) become local;
    # ``kind: comms-node`` becomes peer.
    assert out[1]["scope"] == "local"


def test_normalise_agents_skips_rows_without_name_case_1() -> None:
    """Defensive: rows without a name can't be rendered. They're
    skipped silently rather than raising — a schema bump upstream
    shouldn't blank the whole panel for one bad row."""
    # Arrange
    raw = [
        {"kind": "comms-node"},  # no name
        {"name": "", "kind": "comms-node"},  # empty name
        {"name": "ok", "kind": "comms-node", "updated_at": 1},
    ]
    # Act
    out = sac_mesh_mod._normalise_agents(raw)
    # Assert
    assert [r["name"] for r in out] == ["ok"]


def test_normalise_agents_skips_rows_without_name_status() -> None:
    """Defensive: rows without a name can't be rendered. They're
    skipped silently rather than raising — a schema bump upstream
    shouldn't blank the whole panel for one bad row."""
    # Arrange
    raw = [
        {"kind": "comms-node"},  # no name
        {"name": "", "kind": "comms-node"},  # empty name
        {"name": "ok", "kind": "comms-node", "updated_at": 1},
    ]
    # Act
    out = sac_mesh_mod._normalise_agents(raw)
    # Assert
    assert out[0]["status"] == "online"


def test_normalise_edges_maps_grants_to_allow_edges_len() -> None:
    """Each grant row → one allow-edge. The audit note is surfaced
    verbatim under ``edges[i]['note']`` so the FE tooltip can display
    it without a second roundtrip."""
    # Arrange
    raw = [
        {"sender": "a", "target": "b", "note": "ticket-PA-1"},
        {"sender": "b", "target": "c"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert len(out) == 2


def test_normalise_edges_maps_grants_to_allow_edges_out() -> None:
    """Each grant row → one allow-edge. The audit note is surfaced
    verbatim under ``edges[i]['note']`` so the FE tooltip can display
    it without a second roundtrip."""
    # Arrange
    raw = [
        {"sender": "a", "target": "b", "note": "ticket-PA-1"},
        {"sender": "b", "target": "c"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert out[0] == {
        "source": "a",
        "target": "b",
        "allow": True,
        "note": "ticket-PA-1",
    }


def test_normalise_edges_maps_grants_to_allow_edges_out_2() -> None:
    """Each grant row → one allow-edge. The audit note is surfaced
    verbatim under ``edges[i]['note']`` so the FE tooltip can display
    it without a second roundtrip."""
    # Arrange
    raw = [
        {"sender": "a", "target": "b", "note": "ticket-PA-1"},
        {"sender": "b", "target": "c"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert out[1] == {"source": "b", "target": "c", "allow": True}


def test_normalise_edges_skips_malformed_rows_len() -> None:
    """Defensive: rows missing ``sender`` or ``target`` can't be drawn.
    Skipped rather than raising — same fail-soft-on-bad-row rationale
    as ``_normalise_agents``."""
    # Arrange
    raw = [
        {"sender": "a"},  # no target
        {"target": "b"},  # no sender
        {"sender": "", "target": "x"},  # empty sender
        {"sender": "ok", "target": "fine"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert len(out) == 1


def test_normalise_edges_skips_malformed_rows_source() -> None:
    """Defensive: rows missing ``sender`` or ``target`` can't be drawn.
    Skipped rather than raising — same fail-soft-on-bad-row rationale
    as ``_normalise_agents``."""
    # Arrange
    raw = [
        {"sender": "a"},  # no target
        {"target": "b"},  # no sender
        {"sender": "", "target": "x"},  # empty sender
        {"sender": "ok", "target": "fine"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert out[0]["source"] == "ok"


def test_normalise_edges_skips_malformed_rows_target() -> None:
    """Defensive: rows missing ``sender`` or ``target`` can't be drawn.
    Skipped rather than raising — same fail-soft-on-bad-row rationale
    as ``_normalise_agents``."""
    # Arrange
    raw = [
        {"sender": "a"},  # no target
        {"target": "b"},  # no sender
        {"sender": "", "target": "x"},  # empty sender
        {"sender": "ok", "target": "fine"},
    ]
    # Act
    out = sac_mesh_mod._normalise_edges(raw)
    # Assert
    assert out[0]["target"] == "fine"


# EOF
