#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /create, /update, /delete handlers.

Mirrors ``src/scitex_todo/_django/handlers/crud.py``. Drives
``views.api_dispatch`` with a real ``RequestFactory`` POST against a tmp
``tasks.yaml`` (no mocks, STX-NM / PA-306), verifying both the JSON response
and the YAML written by ``save_tasks``.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo._django import views  # noqa: E402
from scitex_todo._django.services import _reset_cache  # noqa: E402

# `gate` depends on `build`; `build` nests under `north`. Lets us prove that
# delete scrubs both the depends_on edge and the parent pointer.
_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal}\n"
    "  - {id: build, title: Build It, status: in_progress, parent: north}\n"
    "  - {id: gate, title: Gate, status: blocked, depends_on: [build]}\n"
)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _post(endpoint, store_path, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _load(store_path):
    with open(store_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return {t["id"]: t for t in data["tasks"]}


# ── create ───────────────────────────────────────────────────────────────
def test_create_returns_ok(store):
    # Arrange
    body = {"title": "New Thing"}
    # Act
    response = _post("create", store, body)
    # Assert
    assert response.status_code == 200


def test_create_generates_slug_id(store):
    # Arrange
    body = {"title": "My New Thing!"}
    # Act
    payload = json.loads(_post("create", store, body).content)
    # Assert
    assert payload["task"]["id"] == "my-new-thing"


def test_create_defaults_status_to_pending(store):
    # Arrange
    body = {"title": "X"}
    # Act
    payload = json.loads(_post("create", store, body).content)
    # Assert
    assert payload["task"]["status"] == "pending"


def test_create_persists_to_store(store):
    # Arrange
    body = {"title": "Persisted", "note": "hi"}
    # Act
    _post("create", store, body)
    # Assert
    assert "persisted" in _load(store)


def test_create_dedupes_id_on_title_collision(store):
    # Arrange
    _post("create", store, {"title": "Dup"})
    # Act
    payload = json.loads(_post("create", store, {"title": "Dup"}).content)
    # Assert
    assert payload["task"]["id"] == "dup-2"


def test_create_rejects_missing_title_with_400(store):
    # Arrange
    body = {"status": "pending"}
    # Act
    response = _post("create", store, body)
    # Assert
    assert response.status_code == 400


def test_create_rejects_get_with_405(store):
    # Arrange
    request = RequestFactory().get(f"/create?store={store}")
    # Act
    response = views.api_dispatch(request, "create")
    # Assert
    assert response.status_code == 405


# ── update ───────────────────────────────────────────────────────────────
def test_update_returns_ok(store):
    # Arrange
    body = {"id": "build", "status": "done"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 200


def test_update_patches_only_given_fields(store):
    # Arrange
    _post("update", store, {"id": "build", "status": "done"})
    # Act
    build = _load(store)["build"]
    # Assert — status changed, title untouched.
    assert build["status"] == "done" and build["title"] == "Build It"


def test_update_clears_field_on_empty_value(store):
    # Arrange
    _post("update", store, {"id": "build", "parent": None})
    # Act
    build = _load(store)["build"]
    # Assert
    assert "parent" not in build


def test_update_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost", "status": "done"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 404


def test_update_rejects_invalid_status_with_400(store):
    # Arrange
    body = {"id": "build", "status": "nope"}
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 400


# ── delete ───────────────────────────────────────────────────────────────
def test_delete_returns_ok(store):
    # Arrange
    body = {"id": "build"}
    # Act
    response = _post("delete", store, body)
    # Assert
    assert response.status_code == 200


def test_delete_removes_task(store):
    # Arrange
    _post("delete", store, {"id": "build"})
    # Act
    remaining = _load(store)
    # Assert
    assert "build" not in remaining


def test_delete_scrubs_depends_on_reference(store):
    # Arrange
    _post("delete", store, {"id": "build"})
    # Act
    gate = _load(store)["gate"]
    # Assert — `gate` depended on `build`; that edge must be gone, not dangling.
    assert "depends_on" not in gate


def test_delete_scrubs_parent_reference(store):
    # Arrange — `build`'s parent is `north`; deleting `north` must clear it.
    _post("delete", store, {"id": "north"})
    # Act
    build = _load(store)["build"]
    # Assert
    assert "parent" not in build


def test_delete_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost"}
    # Act
    response = _post("delete", store, body)
    # Assert
    assert response.status_code == 404


# ── comment ──────────────────────────────────────────────────────────────
def test_comment_returns_ok(store):
    # Arrange
    body = {"id": "build", "text": "looks good", "author": "alice"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 200


def test_comment_appends_to_thread(store):
    # Arrange
    _post("comment", store, {"id": "build", "text": "one", "author": "a"})
    # Act
    _post("comment", store, {"id": "build", "text": "two", "author": "b"})
    # Assert — both comments survive (append-only, no clobber).
    texts = [c["text"] for c in _load(store)["build"]["comments"]]
    assert texts == ["one", "two"]


def test_comment_stamps_author_and_ts(store):
    # Arrange
    _post("comment", store, {"id": "build", "text": "hi", "author": "alice"})
    # Act
    entry = _load(store)["build"]["comments"][0]
    # Assert
    assert entry["author"] == "alice" and entry["ts"]


def test_comment_rejects_missing_text_with_400(store):
    # Arrange
    body = {"id": "build"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 400


def test_comment_unknown_id_returns_404(store):
    # Arrange
    body = {"id": "ghost", "text": "x"}
    # Act
    response = _post("comment", store, body)
    # Assert
    assert response.status_code == 404


# ── edge ─────────────────────────────────────────────────────────────────
def test_edge_add_depends_on_returns_ok(store):
    # Arrange
    body = {"action": "add", "kind": "depends_on", "source": "north", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 200


def test_edge_add_depends_on_writes_to_target(store):
    # Arrange — depends_on edge source->target means target.depends_on += source.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "gate", "target": "build"},
    )
    # Act
    build = _load(store)["build"]
    # Assert
    assert "gate" in build["depends_on"]


def test_edge_add_blocks_writes_to_source(store):
    # Arrange — blocks edge source->target means source.blocks += target.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    build = _load(store)["build"]
    # Assert
    assert "gate" in build["blocks"]


def test_edge_remove_drops_reference(store):
    # Arrange — `gate` depends_on `build` in the seed; remove that edge.
    _post(
        "edge",
        store,
        {
            "action": "remove",
            "kind": "depends_on",
            "source": "build",
            "target": "gate",
        },
    )
    # Act
    gate = _load(store)["gate"]
    # Assert
    assert "depends_on" not in gate


def test_edge_rejects_self_edge_with_400(store):
    # Arrange
    body = {"action": "add", "kind": "depends_on", "source": "build", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 400


def test_edge_unknown_id_returns_404(store):
    # Arrange
    body = {"action": "add", "kind": "depends_on", "source": "ghost", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 404


def test_edge_rejects_bad_kind_with_400(store):
    # Arrange
    body = {"action": "add", "kind": "related", "source": "north", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 400


# EOF
