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


def test_hosts_view_returns_500_when_sac_missing_status_code(env) -> None:
    """When sac is artificially unavailable (we clobber PATH), the
    adapter raises and the view returns HTTP 500 with the error in the
    body — fail-loud per the harness contract."""
    # Arrange
    env.set("PATH", "")
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # The message must name "sac" so the operator knows what is missing.
    assert response.status_code == 500

def test_hosts_view_returns_500_when_sac_missing_data_contains(env) -> None:
    """When sac is artificially unavailable (we clobber PATH), the
    adapter raises and the view returns HTTP 500 with the error in the
    body — fail-loud per the harness contract."""
    # Arrange
    env.set("PATH", "")
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # The message must name "sac" so the operator knows what is missing.
    assert "error" in data

def test_hosts_view_returns_500_when_sac_missing_lower_contains(env) -> None:
    """When sac is artificially unavailable (we clobber PATH), the
    adapter raises and the view returns HTTP 500 with the error in the
    body — fail-loud per the harness contract."""
    # Arrange
    env.set("PATH", "")
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # The message must name "sac" so the operator knows what is missing.
    assert "sac" in data["error"].lower()


# ─── happy path (gated on sac availability) ─────────────────────────────


_SAC_AVAILABLE = shutil.which("sac") is not None


@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_status_code() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert response.status_code == 200

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_data_contains() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert "local" in data

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_data_contains_2() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert "peers" in data

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_isinstance() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert isinstance(data["local"], dict)

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_isinstance_2() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert isinstance(data["local"].get("name"), str)

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_name() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert data["local"]["name"]

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_isinstance_3() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert isinstance(data["peers"], list)

@pytest.mark.skipif(
    not _SAC_AVAILABLE, reason="sac CLI not installed on PATH"
)
def test_hosts_view_returns_200_with_local_and_peers_data_contains_3() -> None:
    """When sac is available, the view returns 200 with the adapter
    payload shape: ``local`` (carrying ``name``) + ``peers`` (a list).

    We deliberately do NOT assert specific hostnames or peer counts —
    the registry is environment-specific and the architecture forbids
    proper-noun literals here.
    """
    # Arrange
    request = RequestFactory().get("/fleet/hosts")
    # Act
    response = fleet_hosts_view(request)
    # Assert
    data = json.loads(response.content)
    # ``config_path`` is part of the FE tooltip contract — pin its
    # presence (value may be null for a fresh install with no shared
    # config file).
    assert "config_path" in data
