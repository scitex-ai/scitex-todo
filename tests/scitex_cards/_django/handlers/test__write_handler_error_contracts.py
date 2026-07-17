#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Error-payload contracts for the delegated write handlers.

Pins the three repairs applied after the lock-bypass conversion's
adversarial review (2026-07-17):

1. ``handle_edge``'s 404 names the ACTUALLY-missing id even when the
   missing id itself contains the substring ``"source"`` (the old code
   substring-matched the exception text and reported the wrong card).
2. ``handle_update`` rejects an EXPLICIT ``{"status": null}`` with a 400
   instead of forwarding None to ``update_task``, which would DELETE the
   status key — a status-less card drops out of every lane, the state the
   verb's own contract forbids.
3. ``handle_comment`` turns ``comment_task``'s ``TaskNotFoundError`` (a
   lane-only card, or a delete landing after the cached fast-path) into a
   clean 404, matching ``handle_update``, instead of bubbling a 500.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import crud  # noqa: E402
from scitex_cards._django.handlers import edge as edge_handlers  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: build, title: Build It, status: in_progress,"
    " agent: alice, assignee: alice}\n"
    "  - {id: my-source-card, title: Source-ish, status: deferred,"
    " agent: bob, assignee: bob}\n"
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


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}",
        data=json.dumps(body),
        content_type="application/json",
    )


def _stale_board(store_path):
    """A BoardState snapshot that will NOT see writes landing after it."""
    _reset_cache()
    board = get_board(store_path)
    _reset_cache()
    return board


def test_edge_404_names_missing_id_even_when_it_contains_source(store):
    # Arrange — the board snapshot still holds `my-source-card`; a
    # concurrent delete removes it from the store the verb writes.
    from scitex_cards._store import delete_task

    board = _stale_board(store)
    delete_task(store, "my-source-card")
    # Act — blocks passes source/target through, so the VERB's missing id
    # is the target, whose id contains the substring "source".
    response = edge_handlers.handle_edge(
        _request(
            "edge",
            {
                "action": "add",
                "kind": "blocks",
                "source": "build",
                "target": "my-source-card",
            },
        ),
        board,
    )
    # Assert — the 404 names the card that is actually missing, not `build`.
    assert response.status_code == 404
    assert "my-source-card" in json.loads(response.content)["error"]


def test_update_rejects_explicit_null_status(store):
    # Act — no GUI control emits null; a raw client can.
    board = _stale_board(store)
    response = crud.handle_update(
        _request("update", {"id": "build", "status": None}), board
    )
    # Assert — loud 400, and the card keeps both its status key and value.
    assert response.status_code == 400
    assert "status" in json.loads(response.content)["error"]
    with open(store, encoding="utf-8") as handle:
        tasks = {t["id"]: t for t in yaml.safe_load(handle)["tasks"]}
    assert tasks["build"]["status"] == "in_progress"


def test_comment_on_concurrently_deleted_card_is_404_not_500(store):
    # Arrange — the card passes the cached fast-path, then vanishes.
    from scitex_cards._store import delete_task

    board = _stale_board(store)
    delete_task(store, "build")
    # Act
    response = crud.handle_comment(
        _request("comment", {"id": "build", "text": "still there?"}), board
    )
    # Assert
    assert response.status_code == 404
    assert "build" in json.loads(response.content)["error"]


# EOF
