#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view tests for ``GET /fleet/mesh``.

No mocks (STX-NM/PA-306) and no monkeypatch (PA-306). Drives the view
via Django's RequestFactory against the real ``fetch_mesh`` adapter;
env / cwd manipulation routes through the suite's :func:`env` fixture
(``tests/scitex_todo/conftest.py``).

Contract pinned here:

  1. Adapter failure (sac missing) returns 500 with ``{"error": "..."}``.
     The error string is the verbatim ``FleetAdapterError`` message —
     the FE renders it in the tooltip so the operator can copy-paste
     and reproduce.
  2. When sac IS available, the endpoint returns 200 with a JSON
     payload carrying the load-bearing ``agents`` + ``edges`` +
     ``config_path`` + ``source_versions`` keys.
  3. The view is GET-only — ``POST`` returns 405.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django.handlers.fleet import fleet_mesh_view  # noqa: E402


# ─── fail-loud path: missing sac ────────────────────────────────────────


def test_mesh_view_returns_500_when_sac_missing(env) -> None:
    """When sac is artificially unavailable (we clobber PATH), the
    adapter raises and the view returns HTTP 500 with the error in the
    body — fail-loud per the harness contract."""
    # Arrange
    env.set("PATH", "")
    request = RequestFactory().get("/fleet/mesh")
    # Act
    response = fleet_mesh_view(request)
    # Assert
    assert response.status_code == 500
    data = json.loads(response.content)
    assert "error" in data
    # The message must name "sac" so the operator knows what is missing.
    assert "sac" in data["error"].lower()


# ─── method discipline ──────────────────────────────────────────────────


def test_mesh_view_rejects_post_with_405() -> None:
    """The endpoint is strictly read-only — mutations route through
    the ``sac a2a grant`` / ``revoke`` CLI, not through scitex-todo.
    POST must come back as 405. No env manipulation needed: the
    method check runs before the adapter call."""
    # Arrange
    request = RequestFactory().post("/fleet/mesh")
    # Act
    response = fleet_mesh_view(request)
    # Assert
    assert response.status_code == 405
    data = json.loads(response.content)
    assert "error" in data
    assert "POST" in data["error"] or "method" in data["error"].lower()


# ─── happy path (gated on sac availability) ─────────────────────────────


def _sac_mesh_functional() -> bool:
    """True iff ``sac a2a list --json`` actually SUCCEEDS — not merely that
    the ``sac`` binary is on PATH.

    A present-but-broken sac (e.g. a CI runner where ``sac`` is installed but
    ``sac a2a list --json`` exits non-zero with a traceback) must SKIP this
    happy-path test, not FAIL it: the 500-on-adapter-error contract is already
    pinned by ``test_mesh_view_returns_500_when_sac_missing``, and a broken sac
    is an ENVIRONMENT gap, not a mesh-view regression. Mirrors
    ``test__gh_ci._gh_authed`` (which runs ``gh auth status``, not just
    ``which gh``)."""
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
    reason="sac not installed or `sac a2a list --json` non-functional",
)
def test_mesh_view_returns_200_with_load_bearing_keys() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``agents`` + ``edges`` + ``config_path`` +
    ``source_versions``.

    We deliberately do NOT assert specific agent names or grant
    counts — the registry is environment-specific and the architecture
    forbids proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/mesh")
    # Act
    response = fleet_mesh_view(request)
    # Assert
    assert response.status_code == 200
    data = json.loads(response.content)
    for key in ("agents", "edges", "config_path", "source_versions"):
        assert key in data, f"missing load-bearing key: {key!r}"
    assert isinstance(data["agents"], list)
    assert isinstance(data["edges"], list)
    assert isinstance(data["source_versions"], dict)
    assert "peers" in data["source_versions"]
    assert "grants" in data["source_versions"]


# EOF
