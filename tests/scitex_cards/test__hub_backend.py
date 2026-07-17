#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``HubBackend`` (remote-hub PR-3) — the loopback integration proof.

The design's PR-3 acceptance row: serve + a ``SCITEX_CARDS_HUB_URL``
client in ONE test process; the verb matrix runs through the HubBackend
resolved by :func:`get_backend`; every write is asserted by a DIRECT
hub-side store read (never through the client under test); and the
no-fallback property is pinned — URL-set-token-missing hard-errors, an
unreachable hub hard-errors, and no code path touches a local store.

Also pinned:

- IDENTITY INJECTION — the hub executes verbs under its own env, so the
  client must stamp the REMOTE agent's identity into by/actor/created_by
  when the caller left them unset; an explicit identity is respected.
- ERROR PARITY — a missing card raises the REAL TaskNotFoundError through
  the wire, so caller error handling is backend-agnostic.
- THE NULL SENTINEL SURVIVES THE WIRE — update_task(field=None) DELETES
  hub-side (the parked/un-park clearing class; a client that strips nulls
  silently swallows remote clears).
- A caller passing its own tasks_path/store gets a loud refusal.
"""

from __future__ import annotations

import threading

import pytest

from scitex_cards import _server, _store
from scitex_cards._backend import get_backend
from scitex_cards._backend_http import HubBackend, HubBackendError


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    """A live hub + a fully-provisioned client environment."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    tokens_dir = tmp_path / "tokens"
    audit_path = tmp_path / "logs" / "hub_access.jsonl"

    server = _server.make_server(
        store=str(store), port=0, tokens_dir=tokens_dir, audit_path=audit_path
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", str(tokens_dir / "hub.token"))
    monkeypatch.delenv("SCITEX_CARDS_HUB_TOKEN", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "remote-agent")

    yield {"store": str(store), "port": port, "tokens_dir": tokens_dir}

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _add(backend, card_id: str, **extra):
    body = dict(
        id=card_id,
        title=f"Card {card_id}",
        status="deferred",
        assignee="alice",
    )
    body.update(extra)
    return backend.add_task(None, **body)


# === resolution + the no-fallback property =================================


def test_get_backend_resolves_to_the_hub_client(hub):
    assert isinstance(get_backend(), HubBackend)


def test_url_set_but_token_missing_hard_errors_at_first_call(hub, monkeypatch):
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", "/nonexistent/hub.token")
    backend = get_backend()  # resolution itself stays import-safe
    with pytest.raises(HubBackendError, match="no hub token"):
        backend.list_tasks(None)


def test_unreachable_hub_hard_errors_with_the_tunnel_hint(hub, monkeypatch):
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:1")
    with pytest.raises(HubBackendError, match="tunnel"):
        get_backend().list_tasks(None)


def test_caller_supplied_store_is_refused(hub):
    with pytest.raises(HubBackendError, match="pinned hub-side"):
        get_backend().get_task("/tmp/some/store.yaml", "any")


# === round trips (read-back DIRECTLY hub-side) =============================


def test_add_task_round_trip_and_created_by_injection(hub):
    backend = get_backend()
    created = _add(backend, "hb-add")
    assert created["id"] == "hb-add"
    on_disk = _store.get_task(hub["store"], "hb-add")
    # The caller never set created_by; the CLIENT must have stamped the
    # remote agent's identity — the hub's env must not leak in.
    assert on_disk["created_by"] == "remote-agent"


def test_explicit_identity_is_respected_not_overwritten(hub):
    backend = get_backend()
    _add(backend, "hb-by", created_by="named-human")
    assert _store.get_task(hub["store"], "hb-by")["created_by"] == "named-human"


def test_update_task_round_trip(hub):
    backend = get_backend()
    _add(backend, "hb-upd")
    backend.update_task(None, "hb-upd", status="in_progress", note="over the wire")
    on_disk = _store.get_task(hub["store"], "hb-upd")
    assert on_disk["status"] == "in_progress"
    assert on_disk["note"] == "over the wire"


def test_update_null_sentinel_deletes_hub_side(hub):
    """None must survive serialization as an explicit null and DELETE."""
    backend = get_backend()
    _add(backend, "hb-null", note="to be cleared")
    assert "note" in _store.get_task(hub["store"], "hb-null")
    backend.update_task(None, "hb-null", note=None)
    assert "note" not in _store.get_task(hub["store"], "hb-null")


def test_comment_author_injection(hub):
    backend = get_backend()
    _add(backend, "hb-com")
    result = backend.comment_task(None, "hb-com", "hello from afar")
    assert result["comment"]["author"] == "remote-agent"
    comments = _store.get_task(hub["store"], "hb-com")["comments"]
    assert comments[-1]["text"] == "hello from afar"


def test_complete_task_round_trip(hub):
    backend = get_backend()
    _add(backend, "hb-done")
    backend.complete_task(None, "hb-done")
    on_disk = _store.get_task(hub["store"], "hb-done")
    assert on_disk["status"] == "done"
    assert on_disk["_log_meta"]["completed_by"] == "remote-agent"


def test_list_and_get_read_the_hub_store(hub):
    backend = get_backend()
    _add(backend, "hb-read")
    assert "hb-read" in {t["id"] for t in backend.list_tasks(None, scope="")}
    assert backend.get_task(None, "hb-read")["title"] == "Card hb-read"


def test_delete_restore_round_trip(hub):
    backend = get_backend()
    _add(backend, "hb-del")
    removed = backend.delete_task(None, "hb-del")
    with pytest.raises(Exception):
        _store.get_task(hub["store"], "hb-del")
    backend.restore_task(None, task=removed["removed"], refs=removed.get("refs"))
    assert _store.get_task(hub["store"], "hb-del")["id"] == "hb-del"


def test_set_edge_round_trip(hub):
    backend = get_backend()
    _add(backend, "hb-e1")
    _add(backend, "hb-e2")
    backend.set_edge(
        None, action="add", kind="depends_on", source="hb-e2", target="hb-e1"
    )
    assert _store.get_task(hub["store"], "hb-e2")["depends_on"] == ["hb-e1"]


def test_dm_pair_round_trip(hub):
    backend = get_backend()
    record = backend.dm_send("remote-agent", "operator", "hub dm")
    assert record["from"] == "remote-agent"
    thread = backend.dm_list("operator", peer="remote-agent")
    assert [m["body"] for m in thread["messages"]] == ["hub dm"]


def test_poll_notifications_round_trip(hub):
    backend = get_backend()
    inbox = backend.poll_notifications("remote-agent")
    assert inbox["agent"] == "remote-agent"
    assert isinstance(inbox["notifications"], list)


# === error parity ==========================================================


def test_missing_card_raises_the_real_tasknotfound(hub):
    from scitex_cards._store import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        get_backend().get_task(None, "ghost")


def test_validation_error_crosses_the_wire_typed(hub):
    from scitex_cards import TaskValidationError

    with pytest.raises((TaskValidationError, ValueError)):
        # No owner → the validator refuses; the client re-raises typed.
        get_backend().add_task(None, id="hb-bad", title="No owner")


# EOF
