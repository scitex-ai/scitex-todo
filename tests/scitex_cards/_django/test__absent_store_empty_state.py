#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HONEST EMPTY STATE — an absent store renders 0 cards, not an error banner.

Second half of hub card ``hub-cards-board-data-404`` (adapted from unpushed
``9db9146b`` to the SQLite-era ``services.get_board``): a fresh hub workspace
resolves to a store-identity path whose file does not exist yet. That is a
LEGITIMATE 0-card state — every read in ``get_board`` already treated it that
way except ``load_groups``, whose FileNotFoundError turned the brand-new
tenant's board into a 400 "No task store found." banner (and ``/timeline``
into a 500).

Pins the new contract:

- GET graph / tasks against a resolved-but-nonexistent store → **200** with
  0 rows and ``empty_store: true`` (the FE renders the normal zero-card
  board, never the red load-error).
- GET timeline (which reads the DEFAULT store through the same
  ``get_board``) → **200** with ``empty_store: true``.
- Honesty in the other direction: an EXISTING store reports
  ``empty_store: false``.

RequestFactory against the real views — no mocks, per the _django test
conventions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.graph import _graph_cache_reset  # noqa: E402
from scitex_cards._django.handlers.timeline import timeline_view  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: seeded, title: Seeded Card, status: in_progress, priority: 1}\n"
)


@pytest.fixture
def absent_store(tmp_path):
    """A resolved-but-never-created store path (a fresh workspace)."""
    _reset_cache()
    _graph_cache_reset()
    yield tmp_path / "fresh-workspace" / "tasks.yaml"
    _reset_cache()
    _graph_cache_reset()


def _get(endpoint: str, store: Path):
    request = RequestFactory().get(f"/{endpoint}?store={store}")
    return views.api_dispatch(request, endpoint)


# --- graph: 200 + empty + flagged -------------------------------------------


def test_graph_on_absent_store_returns_200(absent_store):
    """A fresh workspace's board is a state, not an error — no 400 banner."""
    # Arrange
    store = absent_store
    # Act
    response = _get("graph", store)
    # Assert
    assert response.status_code == 200


def test_graph_on_absent_store_has_no_nodes(absent_store):
    """The honest payload for a never-written store is zero cards."""
    # Arrange
    store = absent_store
    # Act
    payload = json.loads(_get("graph", store).content)
    # Assert
    assert payload["nodes"] == []


def test_graph_on_absent_store_sets_empty_store_flag(absent_store):
    """The FE distinguishes "no store yet" from "0 matching cards" by flag."""
    # Arrange
    store = absent_store
    # Act
    payload = json.loads(_get("graph", store).content)
    # Assert
    assert payload["empty_store"] is True


# --- tasks: same contract on the raw list endpoint --------------------------


def test_tasks_on_absent_store_returns_200(absent_store):
    """/tasks shares the read path, so it shares the honest empty state."""
    # Arrange
    store = absent_store
    # Act
    response = _get("tasks", store)
    # Assert
    assert response.status_code == 200


def test_tasks_on_absent_store_is_empty_list(absent_store):
    """The raw task list of a never-written store is []."""
    # Arrange
    store = absent_store
    # Act
    payload = json.loads(_get("tasks", store).content)
    # Assert
    assert payload["tasks"] == []


def test_tasks_on_absent_store_sets_empty_store_flag(absent_store):
    """/tasks carries the same flag the /graph payload does."""
    # Arrange
    store = absent_store
    # Act
    payload = json.loads(_get("tasks", store).content)
    # Assert
    assert payload["empty_store"] is True


# --- timeline: default-store read through the same get_board ----------------


@pytest.fixture
def absent_default_store():
    """Remove the pinned default store-identity file for this test.

    ``timeline_view`` reads the DEFAULT store (``get_board()`` with no
    explicit path); deleting the marker file the _django conftest creates
    reproduces the fresh-workspace state on the default resolution chain.
    The next test's autouse fixture recreates the marker.
    """
    _reset_cache()
    marker = Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])
    if marker.exists():
        marker.unlink()
    yield
    _reset_cache()


def test_timeline_on_absent_store_returns_200(absent_default_store):
    """/timeline no longer 500s on a fresh workspace's absent store."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    response = timeline_view(request)
    # Assert
    assert response.status_code == 200


def test_timeline_on_absent_store_has_no_events(absent_default_store):
    """The honest timeline of a never-written store is zero events."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    payload = json.loads(timeline_view(request).content)
    # Assert
    assert payload["events"] == []


def test_timeline_on_absent_store_sets_empty_store_flag(absent_default_store):
    """/timeline carries the same flag the /graph payload does."""
    # Arrange
    request = RequestFactory().get("/timeline")
    # Act
    payload = json.loads(timeline_view(request).content)
    # Assert
    assert payload["empty_store"] is True


# --- honesty in the other direction: an existing store is not "empty" -------


@pytest.fixture
def seeded_store():
    """Seed the canonical DB (marker file present via the autouse fixture)."""
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    _reset_cache()
    _graph_cache_reset()
    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()
    _graph_cache_reset()


def test_graph_on_existing_store_reports_empty_store_false(seeded_store):
    """An existing store must never masquerade as the fresh-workspace state."""
    # Arrange
    store = Path(seeded_store)
    # Act
    payload = json.loads(_get("graph", store).content)
    # Assert
    assert payload["empty_store"] is False


# EOF
