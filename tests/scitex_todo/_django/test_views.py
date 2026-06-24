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


def test_favicon_view_returns_ok_status():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Assert
    assert response.status_code == 200


def test_favicon_view_sets_svg_content_type():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Assert
    assert response["Content-Type"] == "image/svg+xml"


def test_favicon_view_body_is_scitex_s_svg():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Drain FileResponse iterator into bytes.
    body = b"".join(response.streaming_content)
    # Assert — the bundled scitex-dev brand SVG declares id="scitex-logo".
    assert b'id="scitex-logo"' in body


def test_standalone_template_links_svg_favicon(store):
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    response = views.board_page(request)
    # Assert — head should declare the SVG favicon (operator 3683).
    assert b"scitex_todo/favicon.svg" in response.content


@pytest.mark.parametrize(
    "forbidden",
    [b"{#", b"#}", b"snake favicon", b"scitex-dev brand"],
)
def test_standalone_template_does_not_leak_django_comment(store, forbidden):
    """The favicon comment must NOT render as visible page text.

    Django's ``{# … #}`` syntax is single-line only — a multi-line block
    leaks into the rendered HTML. We use ``{% comment %} … {% endcomment %}``
    instead. Each parametrized row guards one raw delimiter / leaked phrase.
    """
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    body = views.board_page(request).content
    # Assert — the raw comment delimiter / body text must not leak.
    assert forbidden not in body


# --- nested-graph drill-down: parent field on graph payload ---------------


_NESTED_STORE_TEXT = (
    "tasks:\n"
    "  - {id: hub, title: Hub, status: goal}\n"
    "  - {id: child-a, title: Child A, status: pending, parent: hub}\n"
    "  - {id: child-b, title: Child B, status: in_progress, parent: hub}\n"
    "  - {id: solo, title: Solo top-level, status: pending}\n"
)


@pytest.fixture
def nested_store(tmp_path):
    """Tmp store with a parent + two children + an unrelated top-level node."""
    path = tmp_path / "tasks-nested.yaml"
    path.write_text(_NESTED_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


@pytest.mark.parametrize(
    "node_id, expected_parent",
    [
        ("child-a", "hub"),
        ("child-b", "hub"),
        ("hub", None),
        ("solo", None),
    ],
)
def test_graph_endpoint_exposes_parent_field_per_node(
    nested_store, node_id, expected_parent
):
    # Arrange
    request = RequestFactory().get(f"/graph?store={nested_store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    by_id = {n["id"]: n for n in payload["nodes"]}
    # Assert — children carry their parent id; hub/solo carry null.
    assert by_id[node_id]["parent"] == expected_parent


def test_graph_endpoint_includes_parent_key_on_every_node(store):
    """A store with NO `parent` field anywhere still emits the key per node,
    so the frontend can treat it as always-present."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    # Assert
    for node in payload["nodes"]:
        assert "parent" in node


def test_graph_endpoint_defaults_parent_to_null_when_absent_in_yaml(store):
    """With no `parent` in the YAML, every node's parent falls back to null,
    so the frontend can safely default to the top-level view."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    # Assert
    for node in payload["nodes"]:
        assert node["parent"] is None


# --- created_by: provenance role exposed per node -------------------------


@pytest.fixture
def created_by_store(tmp_path):
    """A store whose single task carries an explicit `created_by`."""
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - {id: made, title: Made It, status: pending, created_by: agent:maker}\n",
        encoding="utf-8",
    )
    _reset_cache()
    yield str(path)
    _reset_cache()


def test_graph_endpoint_exposes_created_by_on_node(created_by_store):
    """The detail panel's "Created by" line reads this wire field."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={created_by_store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    made = next(n for n in payload["nodes"] if n["id"] == "made")
    # Assert
    assert made["created_by"] == "agent:maker"


def test_graph_endpoint_includes_created_by_key_on_every_node(store):
    """A store with NO `created_by` anywhere still emits the key per node
    (null), so the frontend can render "Created by: —" without null-checks."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    # Assert
    for node in payload["nodes"]:
        assert "created_by" in node


# --- rev: cheap change-poll fingerprint -----------------------------------


def test_rev_endpoint_returns_ok(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    response = views.api_dispatch(request, "rev")
    # Assert
    assert response.status_code == 200


def test_rev_endpoint_reports_task_count(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert — the seeded store has three tasks.
    assert payload["count"] == 3


def test_rev_endpoint_includes_positive_mtime(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert — mtime is a positive number the frontend can fingerprint on.
    assert isinstance(payload["mtime"], (int, float)) and payload["mtime"] > 0


# EOF
