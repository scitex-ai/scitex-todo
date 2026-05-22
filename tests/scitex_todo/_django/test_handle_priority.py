#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /priority handler -- drag-reorder persistence.

Drives ``views.api_dispatch`` with a real ``RequestFactory`` POST against a
tmp ``tasks.yaml`` (no mocks, STX-NM / PA-306), and verifies both the JSON
response and the YAML written by ``save_tasks``.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django import views  # noqa: E402
from scitex_todo._django.services import _reset_cache  # noqa: E402

# Three connected tasks with a deterministic baseline priority layout.
# `gate` blocks `build`, and `north` depends on `build`. All three start with
# priorities 5/6/7 so we can prove the handler actually overwrites them.
_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build], priority: 5}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 6}\n"
    "  - {id: gate, title: Gate, status: blocked, blocks: [build], priority: 7}\n"
)


@pytest.fixture
def store(tmp_path):
    """Write a real tmp task store and reset the board cache around the test."""
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _post_priority(store_path, body):
    """Drive views.api_dispatch for a POST /priority request."""
    request = RequestFactory().post(
        f"/priority?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, "priority")


def _load_priorities(store_path):
    """Read the YAML store and return {id: priority} for inspection."""
    with open(store_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return {t["id"]: t.get("priority") for t in data["tasks"]}


def test_priority_endpoint_returns_ok_on_post(store):
    # Arrange
    body = {"order": ["build", "gate", "north"]}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 200


def test_priority_endpoint_assigns_sequential_priorities(store):
    # Arrange — order says build=1, gate=2, north=3.
    body = {"order": ["build", "gate", "north"]}
    # Act
    _post_priority(store, body)
    # Assert — YAML on disk now reflects the new priorities.
    priorities = _load_priorities(store)
    assert priorities == {"build": 1, "gate": 2, "north": 3}


def test_priority_endpoint_response_lists_updated_ids(store):
    # Arrange
    body = {"order": ["gate", "build"]}
    # Act
    response = _post_priority(store, body)
    payload = json.loads(response.content)
    # Assert
    assert payload["updated"] == ["gate", "build"]


def test_priority_endpoint_response_includes_store_path(store):
    # Arrange
    body = {"order": ["build"]}
    # Act
    response = _post_priority(store, body)
    payload = json.loads(response.content)
    # Assert
    assert payload["store_path"] == store


def test_priority_endpoint_leaves_unlisted_tasks_untouched(store):
    # Arrange — only reorder `build` and `gate`; `north` is omitted.
    body = {"order": ["gate", "build"]}
    # Act
    _post_priority(store, body)
    # Assert — `north` keeps its original priority (5), the other two are
    # updated to the new 1/2 ranks.
    priorities = _load_priorities(store)
    assert priorities["north"] == 5
    assert priorities["gate"] == 1
    assert priorities["build"] == 2


def test_priority_endpoint_ignores_unknown_ids(store):
    # Arrange — `ghost` is not in the store; should be silently skipped, but
    # the real ids around it still get their sequential ranks.
    body = {"order": ["ghost", "build", "gate"]}
    # Act
    response = _post_priority(store, body)
    payload = json.loads(response.content)
    # Assert — response only lists ids that actually existed, and ranks are
    # assigned by their position in the full `order` list (so build=2, gate=3).
    assert payload["updated"] == ["build", "gate"]
    priorities = _load_priorities(store)
    assert priorities["build"] == 2
    assert priorities["gate"] == 3


def test_priority_endpoint_rejects_get_with_405(store):
    # Arrange
    request = RequestFactory().get(f"/priority?store={store}")
    # Act
    response = views.api_dispatch(request, "priority")
    # Assert
    assert response.status_code == 405


def test_priority_endpoint_rejects_invalid_json_with_400(store):
    # Arrange — POST a body that is not valid JSON.
    request = RequestFactory().post(
        f"/priority?store={store}",
        data="{not json",
        content_type="application/json",
    )
    # Act
    response = views.api_dispatch(request, "priority")
    # Assert
    assert response.status_code == 400


def test_priority_endpoint_rejects_missing_order_with_400(store):
    # Arrange — well-formed JSON but no `order` key.
    body = {"something_else": []}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 400


def test_priority_endpoint_rejects_non_string_ids_with_400(store):
    # Arrange — `order` must be a list of strings; ints are an error.
    body = {"order": [1, 2, 3]}
    # Act
    response = _post_priority(store, body)
    # Assert
    assert response.status_code == 400


def test_priority_endpoint_preserves_yaml_comments(store, tmp_path):
    # Arrange — write a store with a hand-written comment we want to keep.
    path = tmp_path / "commented.yaml"
    path.write_text(
        "# top-of-file comment about the task store\n"
        "tasks:\n"
        "  - id: alpha\n"
        "    title: First\n"
        "    status: pending\n"
        "  - id: beta\n"
        "    title: Second\n"
        "    status: pending\n",
        encoding="utf-8",
    )
    _reset_cache()
    # Act
    _post_priority(str(path), {"order": ["beta", "alpha"]})
    # Assert — comment survives the ruamel round-trip, AND priorities applied.
    text = path.read_text(encoding="utf-8")
    assert "top-of-file comment" in text
    priorities = _load_priorities(str(path))
    assert priorities == {"beta": 1, "alpha": 2}


def test_priority_endpoint_invalidates_board_cache(store):
    # Arrange — prime the cache by hitting /graph once, then mutate via POST.
    request = RequestFactory().get(f"/graph?store={store}")
    views.api_dispatch(request, "graph")
    # Act
    _post_priority(store, {"order": ["gate", "build", "north"]})
    # Assert — a follow-up /graph must observe the new priorities, not the
    # cached pre-write snapshot.
    request = RequestFactory().get(f"/graph?store={store}")
    payload = json.loads(views.api_dispatch(request, "graph").content)
    by_id = {n["id"]: n for n in payload["nodes"]}
    assert by_id["gate"]["priority"] == 1
    assert by_id["build"]["priority"] == 2
    assert by_id["north"]["priority"] == 3


# EOF
