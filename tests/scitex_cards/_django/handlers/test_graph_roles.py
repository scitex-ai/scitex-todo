#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the USER-role fields on each GET /graph node payload.

scitex-cards's entity is the USER (an agent is just ``user.kind=agent``).
The board's detail drawer renders a ROLES section — Creator / Assignee /
Collaborators / Subscribers — straight off the ``/graph`` node dict, so
the node payload MUST emit those fields. This covers the wire contract:

  * ``created_by`` is forwarded verbatim (None on legacy rows that
    predate the field — the FE then falls back to the earliest comment
    author, else "—")
  * ``collaborators`` / ``subscribers`` are always emitted as lists
    (empty list when absent) so the FE can render "—" without
    null-checks
  * ``agent`` (the assignee in user terms) keeps being emitted

Real ``RequestFactory`` GET against a tmp ``tasks.yaml`` — no mocks
(STX-NM / PA-306). Mirrors the pattern in ``test_graph_fleet.py``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402


def _store_text() -> str:
    """Two tasks: one fully-roled, one legacy (no role fields at all)."""
    return (
        "tasks:\n"
        "  - id: roled\n"
        "    title: 'fully roled card'\n"
        "    status: pending\n"
        "    agent: agent:assignee\n"
        "    created_by: agent:creator\n"
        "    collaborators:\n"
        "      - agent:collab-a\n"
        "      - agent:collab-b\n"
        "    subscribers:\n"
        "      - agent:sub-a\n"
        "  - id: legacy\n"
        "    title: 'legacy card with no role fields'\n"
        "    status: pending\n"
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_store_text(), encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _nodes_by_id(store_path: str) -> dict:
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    assert response.status_code == 200
    payload = json.loads(response.content)
    return {n["id"]: n for n in payload["nodes"]}


def test_node_emits_created_by(store):
    # Arrange / Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["created_by"] == "agent:creator"


def test_node_emits_collaborators_list(store):
    # Arrange / Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["collaborators"] == ["agent:collab-a", "agent:collab-b"]


def test_node_emits_subscribers_list(store):
    # Arrange / Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["subscribers"] == ["agent:sub-a"]


def test_node_still_emits_assignee_agent(store):
    # Arrange / Act
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["roled"]["agent"] == "agent:assignee"


def test_legacy_node_created_by_is_none(store):
    # Arrange / Act — legacy card predates created_by; FE falls back to the
    # earliest comment author / "—".
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["legacy"]["created_by"] is None


def test_legacy_node_role_lists_are_empty_lists(store):
    # Arrange / Act — absent role lists emit as [] (FE renders "—").
    nodes = _nodes_by_id(store)
    # Assert
    assert nodes["legacy"]["collaborators"] == []
    assert nodes["legacy"]["subscribers"] == []


# EOF
