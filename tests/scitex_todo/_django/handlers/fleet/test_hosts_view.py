#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view tests for ``GET /fleet/hosts``.

No mocks (STX-NM/PA-306) and no monkeypatch (PA-306). Drives the view
via Django's RequestFactory against the real ``fetch_hosts`` adapter;
env / cwd manipulation routes through the suite's :func:`env` fixture
(``tests/scitex_todo/conftest.py``).

Contract pinned here:

  1. Adapter failure (sac missing) returns 500 with ``{"error": "..."}``.
     The error string is the verbatim ``FleetAdapterError`` message —
     the FE renders it in the tooltip so the operator can copy-paste
     and reproduce.
  2. When sac IS available, the endpoint returns 200 with a JSON
     payload carrying the load-bearing ``local`` + ``peers`` keys.
"""

from __future__ import annotations

import json
import shutil

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django.handlers.fleet import fleet_hosts_view  # noqa: E402


# ─── fail-loud path: missing sac ────────────────────────────────────────


def test_hosts_view_returns_500_when_sac_missing(env) -> None:
    """When sac is artificially unavailable (we clobber PATH), the
    adapter raises and the view returns HTTP 500 with the error in the
    body — fail-loud per the harness contract."""
    env.set("PATH", "")
    request = RequestFactory().get("/fleet/hosts")
    response = fleet_hosts_view(request)
    assert response.status_code == 500
    data = json.loads(response.content)
    assert "error" in data
    # The message must name "sac" so the operator knows what is missing.
    assert "sac" in data["error"].lower()


# ─── happy path (gated on sac availability) ─────────────────────────────


_SAC_AVAILABLE = shutil.which("sac") is not None


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    request = RequestFactory().get("/fleet/hosts")
    response = fleet_hosts_view(request)
    assert response.status_code == 200
    data = json.loads(response.content)
    assert "local" in data
    assert "peers" in data
    assert isinstance(data["local"], dict)
    assert isinstance(data["local"].get("name"), str)
    assert data["local"]["name"]
    assert isinstance(data["peers"], list)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert "config_path" in data
