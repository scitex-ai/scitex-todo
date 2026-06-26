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
# Every create now REQUIRES an owner (handle_create delegates to add_task,
# fail-loud without one), so happy-path bodies carry an `assignee`.
def test_create_returns_ok(store):
    response = _post("create", store, {"title": "New Thing", "assignee": "alice"})
    assert response.status_code == 200


def test_create_generates_slug_id(store):
    payload = json.loads(
        _post("create", store, {"title": "My New Thing!", "assignee": "alice"}).content
    )
    assert payload["task"]["id"] == "my-new-thing"


def test_create_defaults_status_to_pending(store):
    payload = json.loads(
        _post("create", store, {"title": "X", "assignee": "alice"}).content
    )
    assert payload["task"]["status"] == "pending"


def test_create_persists_to_store(store):
    _post("create", store, {"title": "Persisted", "note": "hi", "assignee": "alice"})
    assert "persisted" in _load(store)


def test_create_dedupes_id_on_title_collision(store):
    _post("create", store, {"title": "Dup", "assignee": "alice"})
    payload = json.loads(
        _post("create", store, {"title": "Dup", "assignee": "alice"}).content
    )
    assert payload["task"]["id"] == "dup-2"


def test_create_rejects_missing_title_with_400(store):
    response = _post("create", store, {"status": "pending", "assignee": "alice"})
    assert response.status_code == 400


def test_create_rejects_get_with_405(store):
    # Arrange
    request = RequestFactory().get(f"/create?store={store}")
    # Act
    response = views.api_dispatch(request, "create")
    # Assert
    assert response.status_code == 405


def test_create_owns_card_and_stamps_creator(store, monkeypatch):
    # Fully-OWNED card + stamped creator: assignee set, agent in lock-step,
    # created_by defaulting to "operator" (the board's identity).
    monkeypatch.delenv("SCITEX_TODO_AGENT", raising=False)
    task = json.loads(
        _post("create", store, {"title": "Owned Card", "assignee": "bob"}).content
    )["task"]
    assert task["assignee"] == "bob"
    assert task["agent"] == "bob"
    assert task["created_by"] == "operator"


def test_create_accepts_agent_as_owner(store):
    # `agent` alone also satisfies the owner requirement; assignee == agent.
    task = json.loads(
        _post("create", store, {"title": "Agent Owned", "agent": "carol"}).content
    )["task"]
    assert task["agent"] == "carol" and task["assignee"] == "carol"


def test_create_rejects_missing_assignee_with_400(store):
    # No owner -> 400 AND nothing written: fail-loud, not fail-corrupt.
    response = _post("create", store, {"title": "Ownerless"})
    payload = json.loads(response.content)
    assert response.status_code == 400
    assert "assignee" in payload["error"]
    assert "ownerless" not in _load(store)


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


def test_delete_returns_removed_task_and_refs(store):
    # Arrange — `gate` depends_on `build`; deleting `build` scrubs that ref.
    # Act
    payload = json.loads(_post("delete", store, {"id": "build"}).content)
    # Assert
    assert (
        payload["removed"]["id"] == "build"
        and {
            "id": "gate",
            "field": "depends_on",
        }
        in payload["refs"]
    )


# ── restore (undo delete) ──────────────────────────────────────────────────
def test_restore_reinserts_task(store):
    # Arrange — delete then restore `build` from the response.
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Act
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    # Assert
    assert "build" in _load(store)


def test_restore_reapplies_scrubbed_refs(store):
    # Arrange
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Act
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    # Assert — `gate.depends_on` regained `build`.
    assert "build" in _load(store)["gate"]["depends_on"]


def test_restore_requires_task_with_id(store):
    # Arrange
    body = {"task": {"title": "x"}, "refs": []}
    # Act
    response = _post("restore", store, body)
    # Assert
    assert response.status_code == 400


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


# ── comment relay: never hang, fail loud (operator P1, 2026-06-25) ────────
def _closed_port() -> int:
    """Bind+release a port so it is currently refusing connections."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _store_with_agent(tmp_path):
    """A store whose card is OWNED by 'owner-agent' so the relay fires."""
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - {id: owned, title: Owned, status: in_progress, agent: owner-agent}\n",
        encoding="utf-8",
    )
    _reset_cache()
    return str(path)


def test_comment_relay_returns_promptly_when_owner_unreachable(
    tmp_path, monkeypatch
):
    # Operator P1: posting a comment must NOT hang ~30 s when the owning
    # agent's /v1/turn is unreachable. Point the owner at a CLOSED port
    # (real refused connection, no mocks) and assert the POST returns in
    # a few seconds — not the old 30 s.
    # Arrange
    import json as _json
    import time

    store_path = _store_with_agent(tmp_path)
    port = _closed_port()
    monkeypatch.setenv(
        "SCITEX_TODO_AGENT_TURN_URLS",
        _json.dumps({"owner-agent": f"http://127.0.0.1:{port}/v1/turn"}),
    )
    monkeypatch.delenv("SCITEX_TODO_PUSH_DRY_RUN", raising=False)
    try:
        # Act
        t0 = time.monotonic()
        _post("comment", store_path, {"id": "owned", "text": "ping", "author": "operator"})
        elapsed = time.monotonic() - t0
    finally:
        _reset_cache()
    # Assert
    assert elapsed < 5.0


def test_comment_relay_reports_failure_in_response(tmp_path, monkeypatch):
    # The notify failure must be VISIBLE (loud toast), not swallowed: the
    # /comment JSON carries relay.sent=False so the board toasts it.
    # Arrange
    import json as _json

    store_path = _store_with_agent(tmp_path)
    port = _closed_port()
    monkeypatch.setenv(
        "SCITEX_TODO_AGENT_TURN_URLS",
        _json.dumps({"owner-agent": f"http://127.0.0.1:{port}/v1/turn"}),
    )
    monkeypatch.delenv("SCITEX_TODO_PUSH_DRY_RUN", raising=False)
    try:
        # Act
        resp = _post(
            "comment", store_path,
            {"id": "owned", "text": "ping", "author": "operator"},
        )
        payload = json.loads(resp.content)
    finally:
        _reset_cache()
    # Assert
    assert payload["relay"]["sent"] is False


def test_comment_still_saved_when_relay_fails(tmp_path, monkeypatch):
    # Fail-loud, not fail-closed: a relay miss must NOT lose the comment.
    # Arrange
    import json as _json

    store_path = _store_with_agent(tmp_path)
    port = _closed_port()
    monkeypatch.setenv(
        "SCITEX_TODO_AGENT_TURN_URLS",
        _json.dumps({"owner-agent": f"http://127.0.0.1:{port}/v1/turn"}),
    )
    monkeypatch.delenv("SCITEX_TODO_PUSH_DRY_RUN", raising=False)
    try:
        # Act
        _post(
            "comment", store_path,
            {"id": "owned", "text": "saved-anyway", "author": "operator"},
        )
        texts = [c["text"] for c in _load(store_path)["owned"]["comments"]]
    finally:
        _reset_cache()
    # Assert
    assert "saved-anyway" in texts


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
