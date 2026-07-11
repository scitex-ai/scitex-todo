#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/dm/*`` Django endpoints — the operator↔agent DIRECT-MESSAGE API.

Backs the board's mobile-first ``/chat`` view (operator side of the
scitex-dev DM convention v1; card
``fleet-agent-direct-message-board-pane-20260707``). Distinct from
``handlers/chat.py`` — that is the per-CARD comment thread; this is the
per-AGENT direct-message thread stored in the ``threads.yaml`` sidecar
(:mod:`scitex_todo._threads`).

Endpoints::

    GET  /dm/threads
      -> 200 + {"agents": [{"name", "kind", "unread", "last_ts",
                            "last_body"}, ...]}
         The union of the ``users:`` registry and any peer that already has
         a DM thread with the operator, sorted by most-recent activity.

    GET  /dm/thread/<peer>[?mark_read=1]
      -> 200 + {"thread": <id>, "peer": <peer>, "messages": [...]}
         The operator's thread with ``peer`` (chronological). With
         ``mark_read=1``, messages addressed TO the operator are flipped
         read (poll-and-ack in one call — what the open thread pane does).

    POST /dm/thread/<peer>   body = {"body": "<text>"}
      -> 200 + {"message": <stored record>}
         Appends ``from=operator`` and dm-dispatches into the agent's
         pull-inbox (the unified channel server pushes it into the agent's
         session). 400 on an empty body.

The operator's reserved peer name is ``scitex_todo._threads.OPERATOR_NAME``
(``"operator"``). All endpoints honour the ``?store=`` query param the rest
of the board uses, so tests drive a real tmp store.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from scitex_todo import _threads
from scitex_todo._threads import OPERATOR_NAME


def _store_of(request: HttpRequest):
    """Optional explicit store path from the ``?store=`` query param."""
    return request.GET.get("store") or None


def _registry_agents(store) -> list[dict]:
    """Project the ``users:`` registry onto ``{name, kind}`` rows.

    Fail-soft: a missing/malformed registry yields ``[]`` — the chat view
    still works from thread peers alone.
    """
    try:
        from scitex_todo._users import list_users

        users = list_users(store)
    except Exception:  # noqa: BLE001 — registry optional for the chat view
        return []
    out = []
    for u in users:
        name = u.names[0] if u.names else u.id
        if name and name != OPERATOR_NAME:
            out.append({"name": name, "kind": u.kind})
    return out


def dm_threads_view(request: HttpRequest) -> HttpResponse:
    """GET the operator's agent list + per-agent thread summaries."""
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    store = _store_of(request)
    rows: dict[str, dict] = {}
    for agent in _registry_agents(store):
        rows[agent["name"]] = {
            "name": agent["name"],
            "kind": agent["kind"],
            "unread": 0,
            "last_ts": None,
            "last_body": None,
        }
    # Merge in any peer that already has a thread with the operator (covers
    # unregistered senders — the thread store is the SSOT of who talked).
    for key, summary in _threads.list_threads(store=store).items():
        a, b = summary["peers"]
        if OPERATOR_NAME not in (a, b):
            continue
        peer = b if a == OPERATOR_NAME else a
        row = rows.setdefault(
            peer,
            {"name": peer, "kind": None, "unread": 0,
             "last_ts": None, "last_body": None},
        )
        row["unread"] = summary["unread"].get(OPERATOR_NAME, 0)
        last = summary["last"]
        if last is not None:
            row["last_ts"] = last.get("ts")
            row["last_body"] = last.get("body")
    agents = sorted(
        rows.values(), key=lambda r: (r["last_ts"] or "", r["name"]), reverse=True
    )
    return JsonResponse({"agents": agents})


@csrf_exempt
def dm_thread_view(request: HttpRequest, peer: str) -> HttpResponse:
    """GET the operator↔``peer`` thread, or POST a new operator message."""
    if request.method not in {"GET", "POST"}:
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    store = _store_of(request)
    if not peer or not peer.strip():
        return JsonResponse({"error": "empty peer name"}, status=400)
    peer = peer.strip()

    if request.method == "GET":
        key = _threads.thread_key(OPERATOR_NAME, peer)
        # Poll-and-ack: the open pane passes mark_read=1 so viewing the
        # thread clears the operator-side unread counter.
        if request.GET.get("mark_read") in ("1", "true"):
            _threads.mark_read(key, OPERATOR_NAME, store=store)
        messages = _threads.get_thread(OPERATOR_NAME, peer, store=store)
        return JsonResponse(
            {"thread": key, "peer": peer, "messages": messages},
            json_dumps_params={"default": str},
        )

    # POST — the operator sends a message to `peer`.
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, str) or not body.strip():
        return JsonResponse(
            {"error": "dm send requires non-empty 'body'"}, status=400
        )
    record = _threads.append_message(OPERATOR_NAME, peer, body, store=store)
    return JsonResponse({"message": record}, json_dumps_params={"default": str})


__all__ = ["dm_thread_view", "dm_threads_view"]

# EOF
