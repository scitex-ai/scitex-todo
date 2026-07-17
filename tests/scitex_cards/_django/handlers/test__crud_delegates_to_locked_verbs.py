#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The GUI write handlers must not lose concurrent writes (lock-bypass fix).

Until 2026-07-17 the board's write handlers (update / delete / restore / edge
/ resolve / reopen / priority / archive) read ``board.tasks`` — a
request-scoped CACHE — mutated it in memory, then saved the WHOLE list back
with no flock across the read-modify-write. Any concurrent ``_store`` write
landing between the cache read and the save was silently clobbered (lost
update). ``handle_create`` / ``handle_comment`` already delegated to the
locked ``_store`` verbs and were never affected.

This file pins the fix three ways:

1. LOST-UPDATE survival — per handler: hand the handler a deliberately STALE
   ``BoardState``, land a concurrent ``add_task`` write, then assert the
   handler's write did NOT erase the concurrent card (the old code did).
2. DELEGATION spies — ``handle_update`` -> ``update_task`` (with the GUI's
   None/""/[]-clears translated to the verb's None-deletes) and
   ``handle_edge`` -> ``set_edge`` (with the GUI's ``depends_on``
   source/target SWAPPED — the two surfaces hang the field on opposite ends).
3. EDGE-ORIENTATION on-disk parity — for both kinds x both actions the field
   lands exactly where the old handler put it.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import (  # noqa: E402
    crud,
    priority,
    reopen,
    resolve,
    stale,
    undo,
)
from scitex_cards._django.handlers import edge as edge_handlers  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal,"
    " agent: alice, assignee: alice}\n"
    "  - {id: build, title: Build It, status: in_progress, parent: north,"
    " note: keep, agent: alice, assignee: alice}\n"
    "  - {id: gate, title: Gate, status: blocked, blocker: operator-decision,"
    " depends_on: [build], agent: bob, assignee: bob}\n"
    "  - {id: done-card, title: Done Card, status: done}\n"
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Hermetic: no per-project lane union from the real ~/proj tree.
    monkeypatch.setenv("SCITEX_TODO_LANE_GLOBS", "")
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


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}",
        data=json.dumps(body),
        content_type="application/json",
    )


def _load(store_path):
    with open(store_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return {t["id"]: t for t in data["tasks"]}


def _stale_board(store_path):
    """A BoardState snapshot that will NOT see writes landing after it."""
    _reset_cache()
    board = get_board(store_path)
    _reset_cache()
    return board


def _land_concurrent_write(store_path):
    """A concurrent MCP/CLI writer inserting a card via the locked verb."""
    from scitex_cards._store import add_task

    add_task(
        store_path,
        id="concurrent",
        title="Concurrent Card",
        status="deferred",
        assignee="carol",
        created_by="carol",
    )


# ── 1. lost-update survival, one test per converted handler ───────────────


def test_update_survives_concurrent_write(store):
    # Arrange — the handler holds a stale board; a concurrent write lands.
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = crud.handle_update(
        _request("update", {"id": "build", "status": "done"}), board
    )
    # Assert — both writes survive (the old cache-save erased `concurrent`).
    assert response.status_code == 200
    tasks = _load(store)
    assert tasks["build"]["status"] == "done" and "concurrent" in tasks


def test_delete_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = undo.handle_delete(_request("delete", {"id": "build"}), board)
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert "build" not in tasks and "concurrent" in tasks


def test_restore_survives_concurrent_write(store):
    # Arrange — delete first (fresh), then restore against a stale board.
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = undo.handle_restore(
        _request("restore", {"task": deleted["removed"], "refs": deleted["refs"]}),
        board,
    )
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert "build" in tasks and "concurrent" in tasks


def test_edge_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    body = {"action": "add", "kind": "blocks", "source": "build", "target": "gate"}
    # Act
    response = edge_handlers.handle_edge(_request("edge", body), board)
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert "gate" in tasks["build"]["blocks"] and "concurrent" in tasks


def test_resolve_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = resolve.handle_resolve(
        _request("resolve", {"id": "gate", "actor": "operator"}), board
    )
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert tasks["gate"]["status"] == "done" and "concurrent" in tasks


def test_reopen_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = reopen.handle_reopen(
        _request("reopen", {"id": "done-card", "actor": "operator"}), board
    )
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert tasks["done-card"]["status"] == "blocked" and "concurrent" in tasks


def test_priority_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = priority.handle_priority(
        _request("priority", {"order": ["gate", "build", "north"]}), board
    )
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert tasks["gate"]["priority"] == 1 and "concurrent" in tasks


def test_archive_survives_concurrent_write(store):
    # Arrange
    board = _stale_board(store)
    _land_concurrent_write(store)
    # Act
    response = stale.handle_archive(
        _request("archive", {"id": "north", "reason": "stale", "by": "operator"}),
        board,
    )
    # Assert
    assert response.status_code == 200
    tasks = _load(store)
    assert tasks["north"]["status"] == "deferred" and "concurrent" in tasks


# ── 2a. handle_update delegates to the locked update_task verb ────────────


def test_update_delegates_to_update_task_with_translated_clears(store, monkeypatch):
    # Arrange — spy on the verb; the GUI's None/""/[] clears must all arrive
    # as the verb's None-deletes, and real values must pass through verbatim.
    calls = []

    def spy(store_arg, task_id, **fields):
        calls.append((store_arg, task_id, fields))
        return {"id": task_id, "title": "Build It", "status": "in_progress"}

    monkeypatch.setattr("scitex_cards._store.update_task", spy)
    body = {
        "id": "build",
        "title": "Renamed",
        "note": "",
        "repo": None,
        "blocks": [],
        "priority": 2,
    }
    # Act
    response = _post("update", store, body)
    # Assert
    assert response.status_code == 200
    store_arg, task_id, fields = calls[0]
    assert str(store_arg) == store
    assert task_id == "build"
    assert fields == {
        "title": "Renamed",
        "note": None,
        "repo": None,
        "blocks": None,
        "priority": 2,
    }


def test_update_patches_and_clears_through_the_verb(store):
    # Arrange / Act — end-to-end: real verb, empty-string clear + real patch.
    _post("update", store, {"id": "build", "note": "", "status": "done"})
    build = _load(store)["build"]
    # Assert — note cleared, status patched, untouched fields intact.
    assert "note" not in build
    assert build["status"] == "done"
    assert build["title"] == "Build It"


def test_update_stamps_last_activity_via_the_verb(store):
    # The verb's D11 auto-stamp now applies to GUI updates too (the old
    # cache-write path silently skipped it).
    # Arrange / Act
    _post("update", store, {"id": "build", "priority": 3})
    # Assert
    assert _load(store)["build"]["last_activity"]


def test_update_response_keeps_contract_keys(store):
    # Arrange / Act — an owner change makes the verb annotate liveness; the
    # endpoint must not leak that transport-only key into its response.
    payload = json.loads(
        _post("update", store, {"id": "build", "assignee": "dave"}).content
    )
    # Assert
    assert set(payload) == {"task", "store_path"}
    assert "assignee_liveness" not in payload["task"]
    assert _load(store)["build"]["assignee"] == "dave"


def test_update_unknown_id_still_404(store):
    # Act
    response = _post("update", store, {"id": "ghost", "status": "done"})
    # Assert
    assert response.status_code == 404


def test_update_invalid_status_still_400(store):
    # Act
    response = _post("update", store, {"id": "build", "status": "nope"})
    # Assert
    assert response.status_code == 400


# ── 2b. handle_edge delegates to set_edge with the depends_on SWAP ────────


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_depends_on_swaps_source_and_target_for_set_edge(
    store, monkeypatch, action
):
    # Arrange — set_edge hangs the field on ITS source; the GUI's depends_on
    # payload hangs it on the GUI target. The handler must SWAP.
    calls = []

    def spy(store_arg, action=None, kind=None, source=None, target=None):
        calls.append(
            {"action": action, "kind": kind, "source": source, "target": target}
        )
        return {
            "action": action,
            "kind": kind,
            "source": source,
            "target": target,
            "subscribed": None,
        }

    monkeypatch.setattr("scitex_cards._store.set_edge", spy)
    body = {
        "action": action,
        "kind": "depends_on",
        "source": "north",
        "target": "build",
    }
    # Act
    response = _post("edge", store, body)
    # Assert — verb source = GUI target, verb target = GUI source.
    assert response.status_code == 200
    assert calls[0] == {
        "action": action,
        "kind": "depends_on",
        "source": "build",
        "target": "north",
    }


@pytest.mark.parametrize("action", ["add", "remove"])
def test_edge_blocks_passes_source_and_target_through(store, monkeypatch, action):
    # Arrange — for kind=blocks the two orientations already agree.
    calls = []

    def spy(store_arg, action=None, kind=None, source=None, target=None):
        calls.append(
            {"action": action, "kind": kind, "source": source, "target": target}
        )
        return {
            "action": action,
            "kind": kind,
            "source": source,
            "target": target,
            "subscribed": None,
        }

    monkeypatch.setattr("scitex_cards._store.set_edge", spy)
    body = {"action": action, "kind": "blocks", "source": "north", "target": "build"}
    # Act
    response = _post("edge", store, body)
    # Assert
    assert response.status_code == 200
    assert calls[0] == {
        "action": action,
        "kind": "blocks",
        "source": "north",
        "target": "build",
    }


# ── 2c. edge-orientation ON-DISK parity with the old handler ──────────────


def test_edge_depends_on_add_lands_on_gui_target(store):
    # Old handler: owner = GUI target, gains GUI source in depends_on.
    # Act
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "north", "target": "build"},
    )
    tasks = _load(store)
    # Assert — field on `build` (GUI target), never on `north` (GUI source).
    assert "north" in tasks["build"]["depends_on"]
    assert "depends_on" not in tasks["north"]


def test_edge_depends_on_remove_clears_gui_target(store):
    # Seed: gate.depends_on == [build]; GUI remove(source=build, target=gate).
    # Act
    _post(
        "edge",
        store,
        {"action": "remove", "kind": "depends_on", "source": "build", "target": "gate"},
    )
    # Assert — emptied list drops the key (old handler's convention).
    assert "depends_on" not in _load(store)["gate"]


def test_edge_blocks_add_lands_on_gui_source(store):
    # Old handler: owner = GUI source, gains GUI target in blocks.
    # Act
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    tasks = _load(store)
    # Assert
    assert "gate" in tasks["build"]["blocks"]
    assert "blocks" not in tasks["gate"]


def test_edge_blocks_remove_clears_gui_source(store):
    # Arrange — add then remove through the endpoint.
    _post(
        "edge",
        store,
        {"action": "add", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Act
    _post(
        "edge",
        store,
        {"action": "remove", "kind": "blocks", "source": "build", "target": "gate"},
    )
    # Assert
    assert "blocks" not in _load(store)["build"]


def test_edge_add_now_subscribes_the_waiter_owner(store):
    # Deliberate behavior CHANGE: set_edge subscribes the waiting card's
    # owner to the card they wait on (2026-07-13 fix the GUI path had been
    # silently missing). GUI depends_on(source=north, target=build) means
    # `build` (owner alice) waits on `north` -> alice subscribes to north.
    # Act
    _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "north", "target": "build"},
    )
    # Assert
    assert _load(store)["north"]["subscribers"] == ["alice"]


def test_edge_response_shape_is_unchanged(store):
    # The FE contract predates set_edge's `subscribed` key — it must not leak.
    # Act
    payload = json.loads(
        _post(
            "edge",
            store,
            {
                "action": "add",
                "kind": "depends_on",
                "source": "north",
                "target": "build",
            },
        ).content
    )
    # Assert — GUI orientation echoed back, exactly the historical keys.
    assert payload == {
        "action": "add",
        "kind": "depends_on",
        "source": "north",
        "target": "build",
        "store_path": store,
    }


def test_edge_unknown_id_still_404(store):
    # Act
    response = _post(
        "edge",
        store,
        {"action": "add", "kind": "depends_on", "source": "ghost", "target": "build"},
    )
    # Assert
    assert response.status_code == 404


# ── 3. delete/restore keep the FE Undo contract (refs = {id, field}) ──────


def test_delete_refs_shape_and_restore_reapplies(store):
    # The `_store` verbs return bare-id refs and never re-apply them; the GUI
    # pair must keep the lossless {id, field} contract the FE Undo replays.
    # Act
    deleted = json.loads(_post("delete", store, {"id": "build"}).content)
    # Assert — refs carry field placement...
    assert {"id": "gate", "field": "depends_on"} in deleted["refs"]
    # ...and restore puts the edge back exactly.
    _post("restore", store, {"task": deleted["removed"], "refs": deleted["refs"]})
    assert "build" in _load(store)["gate"]["depends_on"]
