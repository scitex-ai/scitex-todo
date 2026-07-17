#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The hub RPC surface (remote-hub PR-2) — transport, auth, and round trips.

No mocks: a REAL :class:`ThreadingHTTPServer` on an ephemeral loopback
port, driven by real ``urllib`` requests, against a real tmp store. Every
write is verified by a DIRECT store read-back through ``_store`` — never
through the server under test (the design's PR-2 acceptance row).

Pinned here:

1. AUTH — 401 without/with-bad bearer; the identity header REQUIRED (400);
   tokens auto-minted 0600 on first serve.
2. TRANSPORT — /v1/health public; unknown verb 404 naming the verb set;
   store-retarget body keys rejected 400; TaskNotFoundError → 404.
3. ROUND TRIPS — add/update/comment/get/list, delete→restore, dm pair;
   ``_log_meta`` / authorship attribution carried by explicit kwargs.
4. CONCURRENCY — two clients interleaving writes over HTTP; both survive
   (the store flock serializes beneath the server; no lost update).
5. AUDIT — one JSONL line per authenticated request.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scitex_cards import _server, _store


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """A serving rig: tmp store + tmp tokens + tmp audit + live server."""
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "rpc-tester")
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
    token = (tokens_dir / "hub.token").read_text(encoding="utf-8").strip()

    yield {
        "store": str(store),
        "port": port,
        "token": token,
        "tokens_dir": tokens_dir,
        "audit_path": audit_path,
    }

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _call(
    rig_info,
    verb: str,
    body: dict | None = None,
    *,
    token: str | None = "GOOD",
    agent: str | None = "rpc-tester",
):
    """POST /v1/rpc/<verb>; returns (status, parsed-json)."""
    url = f"http://127.0.0.1:{rig_info['port']}/v1/rpc/{verb}"
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    if token is not None:
        real = rig_info["token"] if token == "GOOD" else token
        req.add_header("Authorization", f"Bearer {real}")
    if agent is not None:
        req.add_header("X-Scitex-Agent", agent)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(rig_info, path: str):
    url = f"http://127.0.0.1:{rig_info['port']}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _add(rig_info, card_id: str, **extra):
    body = {
        "id": card_id,
        "title": f"Card {card_id}",
        "status": "deferred",
        "assignee": "alice",
        "created_by": "rpc-tester",
    }
    body.update(extra)
    return _call(rig_info, "add_task", body)


# === 1. auth ===============================================================


def test_health_is_public(rig):
    status, payload = _get(rig, "/v1/health")
    assert status == 200
    assert payload["ok"] is True and payload["verbs"] > 0


def test_missing_token_is_401(rig):
    status, payload = _call(rig, "list_tasks", token=None)
    assert status == 401
    assert "token" in payload["error"]


def test_bad_token_is_401(rig):
    status, _ = _call(rig, "list_tasks", token="not-the-token")
    assert status == 401


def test_missing_identity_header_is_400(rig):
    status, payload = _call(rig, "list_tasks", agent=None)
    assert status == 400
    assert "X-Scitex-Agent" in payload["error"]


def test_token_file_is_minted_0600(rig):
    mode = (rig["tokens_dir"] / "hub.token").stat().st_mode & 0o777
    assert mode == 0o600


# === 2. transport ==========================================================


def test_unknown_verb_is_404_naming_the_surface(rig):
    status, payload = _call(rig, "no_such_verb")
    assert status == 404
    assert "add_task" in payload["verbs"]


def test_store_retarget_is_rejected(rig):
    status, payload = _call(rig, "list_tasks", {"tasks_path": "/tmp/evil.yaml"})
    assert status == 400
    assert "pinned" in payload["error"]


def test_unknown_task_id_is_404(rig):
    status, payload = _call(rig, "get_task", {"task_id": "ghost"})
    assert status == 404
    assert payload["type"] == "TaskNotFoundError"


def test_validation_error_is_400(rig):
    # add_task without an owner trips the validator, not a 500.
    status, _ = _call(
        rig, "add_task", {"id": "x", "title": "X", "created_by": "rpc-tester"}
    )
    assert status == 400


# === 3. round trips (read-back through the ENGINE, never the server) ======


def test_add_task_round_trip_with_attribution(rig):
    status, created = _add(rig, "rt-add")
    assert status == 200 and created["id"] == "rt-add"
    on_disk = _store.get_task(rig["store"], "rt-add")
    assert on_disk["title"] == "Card rt-add"
    assert on_disk["created_by"] == "rpc-tester"


def test_update_task_round_trip(rig):
    _add(rig, "rt-upd")
    status, updated = _call(
        rig, "update_task", {"task_id": "rt-upd", "status": "in_progress"}
    )
    assert status == 200 and updated["status"] == "in_progress"
    assert _store.get_task(rig["store"], "rt-upd")["status"] == "in_progress"


def test_comment_task_round_trip_with_author(rig):
    _add(rig, "rt-com")
    status, result = _call(
        rig,
        "comment_task",
        {"task_id": "rt-com", "text": "over the wire", "by": "remote-agent"},
    )
    assert status == 200 and result["comment"]["author"] == "remote-agent"
    comments = _store.get_task(rig["store"], "rt-com")["comments"]
    assert comments[-1]["text"] == "over the wire"


def test_list_tasks_reads_the_pinned_store(rig):
    _add(rig, "rt-list")
    status, rows = _call(rig, "list_tasks", {"scope": ""})
    assert status == 200
    assert "rt-list" in {t["id"] for t in rows}


def test_delete_then_restore_round_trip(rig):
    _add(rig, "rt-del")
    status, removed = _call(rig, "delete_task", {"task_id": "rt-del"})
    assert status == 200 and removed["removed"]["id"] == "rt-del"
    with pytest.raises(Exception):
        _store.get_task(rig["store"], "rt-del")
    status, _ = _call(
        rig,
        "restore_task",
        {"task": removed["removed"], "refs": removed.get("refs")},
    )
    assert status == 200
    assert _store.get_task(rig["store"], "rt-del")["id"] == "rt-del"


def test_dm_send_and_list_round_trip(rig):
    status, record = _call(
        rig,
        "dm_send",
        {"sender": "remote-agent", "to": "operator", "body": "hello hub"},
    )
    assert status == 200 and record["from"] == "remote-agent"
    status, thread = _call(
        rig, "dm_list", {"sender": "operator", "peer": "remote-agent"}
    )
    assert status == 200
    assert [m["body"] for m in thread["messages"]] == ["hello hub"]


# === 4. concurrency ========================================================


def test_interleaved_remote_writers_lose_nothing(rig):
    """Two HTTP clients hammering updates on two cards; every write lands.

    The server threads call the locked verbs, so the store flock
    serializes beneath HTTP concurrency — the design's whole claim. Ten
    alternating updates per card; the final state and full comment count
    prove no write was clobbered.
    """
    _add(rig, "cc-a")
    _add(rig, "cc-b")
    errors: list = []

    def hammer(card_id: str):
        try:
            for i in range(10):
                status, _ = _call(
                    rig,
                    "comment_task",
                    {"task_id": card_id, "text": f"c{i}", "by": "hammer"},
                )
                assert status == 200
        except Exception as exc:  # noqa: BLE001 — surfaced via the list
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=("cc-a",)),
        threading.Thread(target=hammer, args=("cc-b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert errors == []
    assert len(_store.get_task(rig["store"], "cc-a")["comments"]) == 10
    assert len(_store.get_task(rig["store"], "cc-b")["comments"]) == 10


# === 5. audit ==============================================================


def test_audit_line_per_authenticated_request(rig):
    _add(rig, "au-1")
    _call(rig, "get_task", {"task_id": "au-1"})
    lines = [
        json.loads(line)
        for line in Path(rig["audit_path"]).read_text(encoding="utf-8").splitlines()
    ]
    verbs = [entry["verb"] for entry in lines]
    assert "add_task" in verbs and "get_task" in verbs
    assert all(entry["agent"] == "rpc-tester" for entry in lines)
    assert all("ts" in entry and "status" in entry for entry in lines)


def test_rotation_revokes_without_restart(rig):
    old_token = rig["token"]
    _server.mint_token(rig["tokens_dir"], "hub")
    status, _ = _call(rig, "list_tasks", token=old_token)
    assert status == 401
    new_token = (rig["tokens_dir"] / "hub.token").read_text().strip()
    status, _ = _call(rig, "list_tasks", token=new_token)
    assert status == 200


# EOF
