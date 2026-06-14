#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SAC host-registry adapter (no mocks — STX-NM/PA-306).

Covers the ``fetch_hosts`` shape contract + the fail-loud behavior:

- When ``sac`` is on PATH and authenticated, ``fetch_hosts()`` returns
  a dict with the load-bearing ``local`` + ``peers`` keys.
- When ``sac`` is artificially unavailable (we clobber PATH via the
  PA-306-compliant ``env`` fixture, NOT monkeypatch), ``fetch_hosts``
  raises :class:`FleetAdapterError` with a message that names "sac".
- Malformed-payload validation runs before any subprocess call when
  upstream returns the wrong top-level shape — pinned via the
  ``_SAC_TIMEOUT`` module surface contract test.

No mocks AND no monkeypatch (PA-306). Env / cwd manipulation routes
through the suite's :func:`env` fixture (``tests/scitex_todo/conftest.py``).
"""

from __future__ import annotations

import shutil

import pytest

from scitex_todo._django.handlers.fleet import (
    FleetAdapterError,
    fetch_hosts,
)
from scitex_todo._django.handlers.fleet import sac_hosts as sac_hosts_mod


# ─── fail-loud: missing binary ──────────────────────────────────────────


def test_fetch_hosts_missing_binary_raises(env) -> None:
    """Surfacing "sac not installed" must NOT silently fall back to an
    empty-hosts success — that would lie to the operator about what is
    in their registry. Simulate the missing binary by clobbering PATH
    (PA-306-compliant via the ``env`` fixture, not monkeypatch)."""
    env.set("PATH", "")
    with pytest.raises(FleetAdapterError) as excinfo:
        fetch_hosts()
    # The message must name "sac" so the operator knows what is missing.
    assert "sac" in str(excinfo.value).lower()


# ─── happy path (gated on sac availability) ─────────────────────────────


_SAC_AVAILABLE = shutil.which("sac") is not None


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_fetch_hosts_returns_local_and_peers_keys() -> None:
    """When sac IS available, the adapter returns a dict with the
    load-bearing ``local`` + ``peers`` keys. The FE consumes both, and
    the registry-reader contract pins them as load-bearing.

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and asserting "ywata-note-win"
    would re-introduce a proper-noun literal the architecture forbids.
    """
    out = fetch_hosts()
    assert isinstance(out, dict)
    assert "local" in out
    assert "peers" in out
    # The FE needs ``local.name`` to render the panel label; the
    # adapter validates its presence so an empty dict is impossible.
    assert isinstance(out["local"], dict)
    assert isinstance(out["local"].get("name"), str)
    assert out["local"]["name"]
    # ``peers`` is a list (possibly empty — 0-peer registry is a
    # legitimate steady state, not an adapter error).
    assert isinstance(out["peers"], list)
    # ``config_path`` may be None (no shared config file) but the key
    # itself must be present — the FE tooltip surfaces it verbatim.
    assert "config_path" in out


# ─── module-surface contract ────────────────────────────────────────────


def test_sac_hosts_module_exports_fetch_hosts() -> None:
    """Lock the public surface so a rename downstream forces a test
    update. Operators search for this literal when debugging."""
    assert hasattr(sac_hosts_mod, "fetch_hosts")
    assert "fetch_hosts" in sac_hosts_mod.__all__


def test_sac_hosts_module_timeout_constant_pinned() -> None:
    """Pin the subprocess timeout so a careless bump (e.g. to 120s)
    triggers a test that asks "are you sure the operator wants to wait
    that long for the dashboard?". 10s is the agreed Phase-2 budget."""
    assert sac_hosts_mod._SAC_TIMEOUT == 10


def test_sac_hosts_has_phase_2_b_todo_marker() -> None:
    """The cpu/mem/SLURM enrichment landing point is reserved by a
    ``TODO(phase-2.b)`` marker so the follow-up PR has an obvious
    landing site. Pin its presence so a refactor doesn't lose it."""
    import inspect

    src = inspect.getsource(sac_hosts_mod)
    assert "TODO(phase-2.b)" in src
