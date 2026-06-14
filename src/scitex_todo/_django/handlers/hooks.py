#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP endpoints for the hook-consumer contract.

Loose-coupling consumer side (lead a2a `6fff33d6` + `fbffb879`). The
producer-side (SAC's push-hook, dev's merge-Action) POSTs events here
in the canonical wire shape. The dispatcher
(:func:`scitex_todo._hooks.dispatch_event`) runs the built-in handler
+ every entry-point plugin and returns the summary.

Endpoints (idempotent):

  POST /hooks/push   body = canonical `push` event payload
  POST /hooks/done   body = canonical `done` event payload

Both return JSON on success (200) or error (400 / 405 / 500). See
:mod:`scitex_todo._hooks` for the wire-shape spec.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse


def _post_only(request: HttpRequest) -> HttpResponse | None:
    """Return a 405 JSON response if the request is not POST; else None."""
    if request.method != "POST":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method},
            status=405,
        )
    return None


def _parse_event(request: HttpRequest, expected_kind: str) -> HttpResponse | dict:
    """Parse + validate the JSON body. Returns the dict on success or
    an HttpResponse on failure."""
    from ..._hooks import HookEventError, event_validate

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        return JsonResponse(
            {"error": "invalid-json", "detail": str(exc)}, status=400,
        )
    # The endpoint binds the expected kind so a producer can't ship
    # a `done` to /hooks/push (and vice versa). The validator still
    # checks the OTHER fields per kind.
    if not isinstance(body, dict):
        return JsonResponse(
            {"error": "invalid-body", "detail": "expected JSON object"},
            status=400,
        )
    body.setdefault("kind", expected_kind)
    if body.get("kind") != expected_kind:
        return JsonResponse(
            {
                "error": "kind-mismatch",
                "detail": (
                    f"endpoint expects kind={expected_kind!r}; payload "
                    f"declared kind={body.get('kind')!r}"
                ),
            },
            status=400,
        )
    try:
        return event_validate(body)
    except HookEventError as exc:
        return JsonResponse(
            {"error": "invalid-event", "detail": str(exc)}, status=400,
        )


def hook_push_view(request: HttpRequest) -> HttpResponse:
    """`POST /hooks/push` — record a push event on the board."""
    blocked = _post_only(request)
    if blocked is not None:
        return blocked
    parsed = _parse_event(request, "push")
    if isinstance(parsed, HttpResponse):
        return parsed
    from ..._hooks import dispatch_event

    summary = dispatch_event(parsed)
    return JsonResponse(summary, safe=False)


def hook_done_view(request: HttpRequest) -> HttpResponse:
    """`POST /hooks/done` — record a done/merge event on the board.

    Idempotent: re-posting the same `pr_url` to an already-done card
    with matching `pr_url` is a noop.
    """
    blocked = _post_only(request)
    if blocked is not None:
        return blocked
    parsed = _parse_event(request, "done")
    if isinstance(parsed, HttpResponse):
        return parsed
    from ..._hooks import dispatch_event

    summary = dispatch_event(parsed)
    return JsonResponse(summary, safe=False)
