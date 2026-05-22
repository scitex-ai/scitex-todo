#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo board Django views (real requests, no mocks).

Uses Django's RequestFactory against a real tmp_path task store passed via the
``?store=`` query param, so handlers exercise the real ``load_tasks`` /
``build_mermaid`` / ``STATUS_STYLE`` path end to end.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django import views  # noqa: E402
from scitex_todo._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build]}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 1}\n"
    "  - {id: gate, title: Gate, status: blocked, blocks: [build]}\n"
)


@pytest.fixture
def store(tmp_path):
    """Write a real tmp task store and reset the board cache around the test."""
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _graph_json(store_path):
    """Drive views.api_dispatch for the graph endpoint and parse the JSON."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    return response.status_code, json.loads(response.content)


def test_graph_endpoint_returns_ok_status(store):
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    # Assert
    assert response.status_code == 200


def test_graph_endpoint_returns_all_nodes(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {n["id"] for n in payload["nodes"]} == {"north", "build", "gate"}


def test_graph_endpoint_includes_depends_on_edge(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {"source": "build", "target": "north", "kind": "depends_on"} in payload[
        "edges"
    ]


def test_graph_endpoint_includes_blocks_edge(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {"source": "gate", "target": "build", "kind": "blocks"} in payload["edges"]


def test_graph_endpoint_exposes_priority_on_node(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    build = next(n for n in payload["nodes"] if n["id"] == "build")
    # Assert
    assert build["priority"] == 1


def test_graph_endpoint_colors_goal_gold(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert payload["status_colors"]["goal"]["fill"] == "#ffe082"


def test_graph_endpoint_includes_mermaid_source(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert payload["mermaid"].startswith("flowchart TB")


def test_ping_endpoint_returns_ok_without_store():
    # Arrange
    request = RequestFactory().get("/ping")
    # Act
    response = views.api_dispatch(request, "ping")
    # Assert
    assert json.loads(response.content) == {"status": "ok"}


def test_unknown_endpoint_returns_404(store):
    # Arrange
    request = RequestFactory().get(f"/bogus?store={store}")
    # Act
    response = views.api_dispatch(request, "bogus")
    # Assert
    assert response.status_code == 404


def test_board_page_serves_react_shell_when_built(store):
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    response = views.board_page(request)
    # Assert
    assert b"scitex_todo/assets/index.js" in response.content


def test_tasks_endpoint_returns_raw_task_list(store):
    # Arrange
    request = RequestFactory().get(f"/tasks?store={store}")
    # Act
    response = views.api_dispatch(request, "tasks")
    payload = json.loads(response.content)
    # Assert
    assert [t["id"] for t in payload["tasks"]] == ["north", "build", "gate"]


# EOF
