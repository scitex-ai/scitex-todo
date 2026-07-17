#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backend seam (PR-1 of the remote-hub design) — LocalBackend parity.

Three guarantees, per docs/design/remote-hub-backend.md §2/§6:

1. COMPLETENESS — every name in ``BACKEND_VERBS`` is a callable on
   ``LocalBackend`` (a backend missing a verb would fail at call time on a
   remote host, long after review).
2. RESOLUTION — ``get_backend()`` returns the local passthrough when
   ``SCITEX_CARDS_HUB_URL`` is unset, and FAILS LOUD when it is set on a
   build with no HTTP client. A silent local fallback would write a store
   the hub never sees (the one-database ruling), so the error is pinned.
3. ROUND TRIP — every verb, called through the seam against a real tmp
   store, persists exactly what a direct read of that store shows
   (read-back through ``_store``, not through the object under test).
"""

from __future__ import annotations

import pytest

from scitex_cards import _store
from scitex_cards._backend import (
    BACKEND_VERBS,
    LocalBackend,
    get_backend,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "seam-tester")
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL", raising=False)
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return str(path)


def test_every_declared_verb_is_a_callable():
    backend = LocalBackend()
    missing = [v for v in BACKEND_VERBS if not callable(getattr(backend, v, None))]
    assert missing == []


def test_resolution_local_when_hub_url_unset(monkeypatch):
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL", raising=False)
    assert isinstance(get_backend(), LocalBackend)


def test_resolution_returns_the_hub_client_when_url_set(monkeypatch):
    """PR-3 replaced PR-1's resolve-time refusal with the real client.

    The fail-loud property MOVED, not vanished: it now fires at the first
    CALL when the hub is unusable (no token / unreachable) — pinned in
    test__hub_backend.py — because resolution must stay import-safe while
    a silent local fallback stays impossible.
    """
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:8765")
    from scitex_cards._backend_http import HubBackend

    backend = get_backend()
    assert isinstance(backend, HubBackend)
    assert backend.url == "http://127.0.0.1:8765"


def test_task_verbs_round_trip_through_the_seam(store):
    backend = get_backend()

    created = backend.add_task(
        store,
        id="seam-a",
        title="Seam A",
        status="deferred",
        assignee="alice",
        created_by="seam-tester",
    )
    assert created["id"] == "seam-a"
    backend.add_task(
        store,
        id="seam-b",
        title="Seam B",
        status="deferred",
        assignee="bob",
        created_by="seam-tester",
    )

    # Read-back through the engine, never through the object under test.
    assert _store.get_task(store, "seam-a")["title"] == "Seam A"
    assert backend.get_task(store, "seam-a")["title"] == "Seam A"
    assert {t["id"] for t in backend.list_tasks(store, scope="")} == {
        "seam-a",
        "seam-b",
    }

    updated = backend.update_task(store, "seam-a", status="in_progress")
    assert _store.get_task(store, "seam-a")["status"] == "in_progress"
    assert updated["status"] == "in_progress"

    backend.comment_task(store, "seam-a", "through the seam", by="alice")
    comments = _store.get_task(store, "seam-a")["comments"]
    assert comments[-1]["text"] == "through the seam"

    backend.set_edge(
        store, action="add", kind="depends_on", source="seam-b", target="seam-a"
    )
    assert _store.get_task(store, "seam-b")["depends_on"] == ["seam-a"]

    summary = backend.summarize_tasks(store)
    assert isinstance(summary, dict) and summary

    backend.complete_task(store, "seam-a", by="alice")
    assert _store.get_task(store, "seam-a")["status"] == "done"

    reassigned = backend.reassign_task(store, "seam-b", "carol", by="seam-tester")
    assert reassigned["task"]["assignee"] == "carol"
    assert _store.get_task(store, "seam-b")["assignee"] == "carol"


def test_delete_restore_round_trip(store):
    backend = get_backend()
    backend.add_task(
        store,
        id="seam-del",
        title="To delete",
        status="deferred",
        assignee="alice",
        created_by="seam-tester",
    )
    removed = backend.delete_task(store, "seam-del")
    assert removed["removed"]["id"] == "seam-del"
    with pytest.raises(Exception):
        _store.get_task(store, "seam-del")
    backend.restore_task(store, task=removed["removed"], refs=removed.get("refs"))
    assert _store.get_task(store, "seam-del")["title"] == "To delete"


def test_help_wait_and_clear_round_trip(store):
    backend = get_backend()
    card = backend.help_wait(store, "seam-agent", question="which option?")
    assert card["status"] == "blocked"
    assert _store.get_task(store, card["id"])["blocker"] == "operator-decision"
    cleared = backend.help_clear(store, "seam-agent")
    assert cleared["cleared"] is True
    assert _store.get_task(store, card["id"])["status"] == "done"


def test_dm_and_inbox_round_trip(store):
    backend = get_backend()

    record = backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    assert record["from"] == "seam-alice" and record["to"] == "seam-bob"

    thread = backend.dm_list("seam-bob", peer="seam-alice", ack=True, store=store)
    assert [m["body"] for m in thread["messages"]] == ["hello"]
    assert thread["peer"] == "seam-alice"

    inbox = backend.poll_notifications("seam-bob", store=store)
    assert inbox["agent"] == "seam-bob"
    assert isinstance(inbox["notifications"], list)


# EOF
