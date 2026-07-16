#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /priority handler -- drag-reorder persistence.

Mirrors ``src/scitex_cards/_django/handlers/priority.py``. Drives
``views.api_dispatch`` with a real ``RequestFactory`` POST against a tmp
``tasks.yaml`` (no mocks, STX-NM / PA-306), and verifies both the JSON
response and the YAML written by ``save_tasks``.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

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


@pytest.fixture
def partial_reorder_priorities(store):
    """Reorder only `build` and `gate` (omitting `north`), returning the
    on-disk {id: priority} after the POST so each assertion stays single-Act."""
    _post_priority(store, {"order": ["gate", "build"]})
    return _load_priorities(store)


def test_priority_endpoint_leaves_unlisted_task_at_original_rank(
    partial_reorder_priorities,
):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    north_rank = priorities["north"]
    # Assert — `north` was omitted from the order, so it keeps its original 5.
    assert north_rank == 5


def test_priority_endpoint_ranks_first_listed_task_one(partial_reorder_priorities):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    gate_rank = priorities["gate"]
    # Assert — `gate` was listed first, so it ranks 1.
    assert gate_rank == 1


def test_priority_endpoint_ranks_second_listed_task_two(partial_reorder_priorities):
    # Arrange
    priorities = partial_reorder_priorities
    # Act
    build_rank = priorities["build"]
    # Assert — `build` was listed second, so it ranks 2.
    assert build_rank == 2


@pytest.fixture
def unknown_id_reorder(store):
    """POST an order containing an unknown id (`ghost`), returning the
    (response payload, on-disk priorities) tuple after the POST."""
    response = _post_priority(store, {"order": ["ghost", "build", "gate"]})
    payload = json.loads(response.content)
    return payload, _load_priorities(store)


def test_priority_endpoint_response_omits_unknown_ids(unknown_id_reorder):
    # Arrange
    payload, _priorities = unknown_id_reorder
    # Act
    updated = payload["updated"]
    # Assert — response only lists ids that actually existed in the store.
    assert updated == ["build", "gate"]


def test_priority_endpoint_ranks_by_full_order_position(unknown_id_reorder):
    # Arrange
    _payload, priorities = unknown_id_reorder
    # Act
    build_rank = priorities["build"]
    # Assert — ranks follow position in the full `order` list, so build=2.
    assert build_rank == 2


def test_priority_endpoint_skips_unknown_without_shifting_ranks(unknown_id_reorder):
    # Arrange
    _payload, priorities = unknown_id_reorder
    # Act
    gate_rank = priorities["gate"]
    # Assert — `gate` is third in `order`, so it ranks 3.
    assert gate_rank == 3


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


@pytest.fixture
def commented_store_after_reorder(tmp_path):
    """Write a store with a hand-written comment, reorder it, and return the
    (raw text, {id: priority}) tuple so the fast safe-dump write can be
    inspected."""
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
    _post_priority(str(path), {"order": ["beta", "alpha"]})
    text = path.read_text(encoding="utf-8")
    priorities = _load_priorities(str(path))
    _reset_cache()
    return text, priorities


def test_priority_endpoint_drops_yaml_comment(commented_store_after_reorder):
    # Contract CHANGE (fix/fast-store-write): the write path swapped the
    # ruamel round-trip for a fast safe dump, which does NOT keep comments.
    # Arrange
    text, _priorities = commented_store_after_reorder
    # Act
    comment_survived = "top-of-file comment" in text
    # Assert — the hand-written comment is dropped (accepted trade-off).
    assert not comment_survived


def test_priority_endpoint_applies_priorities_to_commented_store(
    commented_store_after_reorder,
):
    # Arrange
    _text, priorities = commented_store_after_reorder
    # Act
    result = priorities
    # Assert — priorities are applied even for a comment-bearing store.
    assert result == {"beta": 1, "alpha": 2}


@pytest.fixture
def graph_after_reorder(store):
    """Prime the board cache via /graph, mutate via POST /priority, then
    return the post-write {id: node} map from a follow-up /graph."""
    request = RequestFactory().get(f"/graph?store={store}")
    views.api_dispatch(request, "graph")
    _post_priority(store, {"order": ["gate", "build", "north"]})
    request = RequestFactory().get(f"/graph?store={store}")
    payload = json.loads(views.api_dispatch(request, "graph").content)
    return {n["id"]: n for n in payload["nodes"]}


def test_priority_endpoint_invalidates_cache_for_first_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    gate_rank = by_id["gate"]["priority"]
    # Assert — follow-up GET re-reads disk, so `gate` ranks 1, not the cached value.
    assert gate_rank == 1


def test_priority_endpoint_invalidates_cache_for_second_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    build_rank = by_id["build"]["priority"]
    # Assert
    assert build_rank == 2


def test_priority_endpoint_invalidates_cache_for_third_node(graph_after_reorder):
    # Arrange
    by_id = graph_after_reorder
    # Act
    north_rank = by_id["north"]["priority"]
    # Assert
    assert north_rank == 3


# EOF
