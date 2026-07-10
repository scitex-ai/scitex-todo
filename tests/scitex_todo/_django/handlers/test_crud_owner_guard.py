#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 fleet-identity enforcement on the ``/update`` handler.

``handle_update`` (``_django/handlers/crud.py``) mutates a task dict
directly — it does NOT go through ``_store.update_task`` — so it needs its
own wiring to ``scitex_todo._owner_guard.payload_identity_error``. This
covers that wiring, mirroring ``tests/scitex_todo/_django/handlers/
test_crud.py``'s request-factory pattern. Real round-trips, no mocks
(Req STX-NM / PA-306).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_todo import _users  # noqa: E402
from scitex_todo._django import views  # noqa: E402
from scitex_todo._django.services import _reset_cache  # noqa: E402
from scitex_todo._users import ENV_STRICT_IDENTITY  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: c1, title: Card One, status: pending, assignee: alice}\n"
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


def test_update_rejects_unknown_assignee_when_strict(store, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    response = _post(
        "update", store, {"id": "c1", "assignee": "proj-scitex-dev"}
    )
    payload = json.loads(response.content)
    assert response.status_code == 400
    assert "proj-scitex-dev" in payload["error"]


def test_update_accepts_registered_assignee_when_strict(store, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    _users.register_user(kind="agent", names=["known-owner"], store=store)
    response = _post("update", store, {"id": "c1", "assignee": "known-owner"})
    payload = json.loads(response.content)
    assert response.status_code == 200
    assert payload["task"]["assignee"] == "known-owner"


def test_update_owner_check_off_by_default(store, env):
    env.delete(ENV_STRICT_IDENTITY)
    response = _post("update", store, {"id": "c1", "assignee": "whoever"})
    assert response.status_code == 200


def test_update_rejects_forged_created_by_when_strict(store, env):
    env.set(ENV_STRICT_IDENTITY, "1")
    env.set("SCITEX_TODO_AGENT_ID", "agent:me")
    response = _post("update", store, {"id": "c1", "created_by": "someone-else"})
    payload = json.loads(response.content)
    assert response.status_code == 400
    assert "someone-else" in payload["error"]

# EOF
