#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Starlette app factory + endpoint handlers for the listen server.

:func:`create_app` builds the ASGI app the ``scitex-todo listen`` CLI serves
via uvicorn. It wires:

* ``GET  /v1/health`` — PUBLIC liveness probe (no auth). Returns a tiny JSON
  banner so an operator / peer can poll "is scitex-todo's door up?".
* ``POST /v1/notify`` — bearer-gated generic intake. Any external channel
  pushes ``{agent, body, card_id?, from_agent?, event_type?}`` and the server
  ENQUEUES it into that user's own pull-inbox (:func:`scitex_todo._inbox
  .enqueue`) — the always-works standalone delivery sink. Mirrors sac's
  ``/v1/notify`` request/response shape so the SAME client works against
  either door.
* a lifespan that, unless disabled, runs the EXISTING delivery + reminder loop
  (:func:`scitex_todo._delivery._daemon.run_notifyd`) in a background daemon
  thread — so the one process both RECEIVES pushes and DRIVES outbound
  delivery. If a standalone ``notifyd`` already holds the delivery lock, the
  embedded loop steps aside (HTTP door still served) rather than double-send.

No sac import anywhere — only scitex-todo's own primitives.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import BearerAuthMiddleware

logger = logging.getLogger(__name__)

#: Service banner returned by the health probe.
_SERVICE_NAME = "scitex-todo-listen"

#: Default event type stamped on a direct ``/v1/notify`` push that doesn't
#: name one (a generic "someone notified this user" signal, not a card event).
_DEFAULT_EVENT_TYPE = "notify"

#: Synthetic ``card_id`` for a direct notify that isn't about a specific card.
_DIRECT_CARD_ID = "(direct)"


async def _health(_request: Request) -> JSONResponse:
    """Public liveness probe — no auth, tiny stable banner."""
    return JSONResponse({"ok": True, "service": _SERVICE_NAME, "v": 1})


async def _notify(request: Request) -> JSONResponse:
    """Enqueue a pushed notification into a user's pull-inbox.

    Request body (JSON), sac-``/v1/notify``-compatible::

        {"agent": "<user id/name>", "body": "<text>",
         "card_id": "<opt>", "from_agent": "<opt>", "event_type": "<opt>"}

    ``agent`` (aliased ``recipient``) + ``body`` are REQUIRED — a push with no
    recipient or no text is a client error (``400``), never a silent no-op.
    Returns ``{"agent", "msg_id", "enqueued"}``; ``enqueued`` is ``False`` when
    the inbox deduped an identical re-push (same type/card/ts/actor).
    """
    store = request.app.state.store
    try:
        payload: Any = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON → client error, not 500
        return JSONResponse({"error": "body must be valid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    recipient = (payload.get("agent") or payload.get("recipient") or "").strip()
    body = payload.get("body")
    if not recipient:
        return JSONResponse(
            {"error": "field 'agent' (recipient) is required"}, status_code=400
        )
    if not isinstance(body, str) or not body:
        return JSONResponse(
            {"error": "field 'body' (non-empty string) is required"},
            status_code=400,
        )

    event_type = payload.get("event_type") or _DEFAULT_EVENT_TYPE
    card_id = payload.get("card_id") or _DIRECT_CARD_ID
    actor = payload.get("from_agent")

    from .._inbox import enqueue

    try:
        record = enqueue(
            recipient,
            event_type=str(event_type),
            card_id=str(card_id),
            body=body,
            actor=actor if actor is None else str(actor),
            store=store,
        )
    except Exception as exc:  # noqa: BLE001 — a store fault is a 500, surfaced
        logger.exception("listen: enqueue failed for recipient %r", recipient)
        return JSONResponse(
            {"error": f"enqueue failed: {type(exc).__name__}"}, status_code=500
        )

    return JSONResponse(
        {
            "agent": recipient,
            "msg_id": record["id"] if record else None,
            "enqueued": record is not None,
        }
    )


def _build_lifespan(store, *, run_delivery_loop: bool, interval: float):
    """Return an ASGI lifespan that runs the delivery loop in a bg thread.

    On startup (AFTER the app is ready): unless ``run_delivery_loop`` is
    False, spawn a daemon thread running :func:`run_notifyd`. The thread uses
    an interruptible sleep (``stop.wait``) so shutdown is prompt. If a
    standalone notifyd already holds the delivery single-instance lock, the
    embedded loop logs that it is stepping aside and the server still serves
    the HTTP door. On shutdown: set the stop event and join (bounded).

    CRITICAL (learned from sac's bind-hang incident): nothing here blocks the
    event loop before/at startup — the thread is fire-and-forget, so uvicorn
    binds immediately regardless of the loop's state.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        stop = threading.Event()
        thread: "threading.Thread | None" = None
        if run_delivery_loop:
            def _loop() -> None:
                from .._delivery._daemon import DaemonAlreadyRunning, run_notifyd

                try:
                    run_notifyd(
                        store=store,
                        interval=interval,
                        stop=stop,
                        sleep=stop.wait,  # interruptible: wakes instantly on stop
                    )
                except DaemonAlreadyRunning:
                    logger.warning(
                        "listen: a standalone notifyd already owns the delivery "
                        "lock — serving the HTTP door only, NOT running an "
                        "embedded delivery loop (avoids double-send)."
                    )
                except Exception:  # noqa: BLE001 — loop crash must not kill server
                    logger.exception(
                        "listen: embedded delivery loop crashed; HTTP door "
                        "stays up (delivery paused until restart)."
                    )

            thread = threading.Thread(
                target=_loop, name="scitex-todo-listen-delivery", daemon=True
            )
            thread.start()
            logger.info("listen: embedded delivery loop thread started")
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=10.0)
                if thread.is_alive():
                    logger.warning(
                        "listen: delivery loop thread did not stop within 10s "
                        "(daemon thread — process exit will reap it)."
                    )

    return lifespan


def create_app(
    *,
    token: "str | None",
    store: "str | Path | None" = None,  # noqa: F821 — Path in type-only string
    run_delivery_loop: bool = True,
    interval: float = 120.0,
) -> Starlette:
    """Build the listen-server ASGI app.

    Parameters
    ----------
    token : str | None
        The bearer secret every gated route requires. The runner passes the
        ensured token; an empty token makes gated routes fail CLOSED (503).
    store : path-like | None
        Task-store override, forwarded to the inbox enqueue + the embedded
        delivery loop. ``None`` → the normal resolution chain.
    run_delivery_loop : bool
        When True (default) the lifespan runs the embedded delivery/reminder
        loop. Tests pass False to exercise the HTTP door in isolation.
    interval : float
        Seconds between delivery ticks for the embedded loop.
    """
    routes = [
        Route("/v1/health", _health, methods=["GET"]),
        Route("/v1/notify", _notify, methods=["POST"]),
    ]
    middleware = [Middleware(BearerAuthMiddleware, token=token)]
    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=_build_lifespan(
            store, run_delivery_loop=run_delivery_loop, interval=interval
        ),
    )
    app.state.store = store
    app.state.token = token
    return app


__all__ = ["create_app"]

# EOF
