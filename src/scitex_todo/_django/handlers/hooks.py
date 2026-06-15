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
                     (also accepts the new ``event:"pr_merged"`` shape
                      — lead+dev schema lock 2026-06-14)

Both return JSON on success (200) or error (400 / 405 / 500). See
:mod:`scitex_todo._hooks` for the wire-shape spec.

## ``event:"pr_merged"`` receiver flow (POST /hooks/done)

1. ``@csrf_exempt`` — external producers (GitHub Actions) carry no
   Django CSRF token.
2. Parse + ``event_validate`` (in ``_hooks`` — recognises the
   ``event:"pr_merged"`` field and delegates to
   ``_hooks_pr_merged.validate``). Normalised dict carries
   ``_source: "pr_merged"`` so this view routes the dedup-ledger path.
3. Card lookup via ``_pr_lookup.find_cards_by_pr(repo, pr_number)``;
   matches populate ``parsed["card_ids"]`` so the existing
   ``_handle_done`` keeps working.
4. Dedup check via ``_hooks_processed.is_processed(repo, pr_number)``.
   Hit → return 200 ``{already_processed: true, ...}`` without store
   mutation.
5. Dispatch (built-in handler + plugins).
6. Mark the ledger with the actual ``matched_cards`` + merge metadata.
7. Return the merge-aware response shape (matched_cards, ledger_key,
   merge_commit, card_writes, plugin_*).

## ``?dry=1`` query

Runs validation + lookup, skips dispatch + ledger mutation. Used by
the dev round-trip test harness to confirm wire-shape alignment
without touching the operator's board. Response carries
``dry_run: true`` + ``would_mutate: [card_ids]``.
"""

from __future__ import annotations

import datetime as _dt
import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt


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
    an HttpResponse on failure.

    ``event:"pr_merged"`` payloads are auto-tagged with the matching
    ``expected_kind`` so the legacy kind-mismatch guard (which exists
    to stop ``done`` payloads landing on ``/hooks/push``) doesn't fire.
    The :func:`event_validate` call then routes by ``event`` field
    first, ``kind`` second.
    """
    from ..._hooks import HookEventError, event_validate

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        return JsonResponse(
            {"error": "invalid-json", "detail": str(exc)}, status=400,
        )
    if not isinstance(body, dict):
        return JsonResponse(
            {"error": "invalid-body", "detail": "expected JSON object"},
            status=400,
        )
    # New event-driven payloads carry ``event:"pr_merged"`` instead of
    # ``kind``. Auto-set ``kind`` so the kind-mismatch guard (which
    # exists to stop a `done` payload from landing on `/hooks/push`)
    # doesn't fire for the new shape. event_validate routes by
    # ``event`` first regardless of any kind setdefault here.
    if body.get("event") == "pr_merged" and expected_kind == "done":
        body.setdefault("kind", expected_kind)
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


@csrf_exempt
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


@csrf_exempt
def hook_done_view(request: HttpRequest) -> HttpResponse:
    """`POST /hooks/done` — record a done/merge event on the board.

    Accepts BOTH wire shapes (lead+dev schema lock 2026-06-14):

    * Legacy ``kind:"done"`` (caller supplies ``card_ids``). Behaviour
      unchanged from PR #187.
    * New ``event:"pr_merged"`` (receiver looks up cards by
      ``pr_url``; dedup-keyed by ``(repo, pr_number)``).

    Idempotent across both: replay → 200 + ``already_processed: true``
    OR per-card noop, depending on the path.
    """
    blocked = _post_only(request)
    if blocked is not None:
        return blocked
    parsed = _parse_event(request, "done")
    if isinstance(parsed, HttpResponse):
        return parsed
    if parsed.get("_source") == "pr_merged":
        return _handle_pr_merged(request, parsed)
    # Legacy path — behaviour unchanged.
    from ..._hooks import dispatch_event

    summary = dispatch_event(parsed)
    return JsonResponse(summary, safe=False)


def _handle_pr_merged(
    request: HttpRequest, parsed: dict
) -> HttpResponse:
    """Dedup-ledger + card-lookup wired flow for the new wire shape."""
    from ..._hooks import dispatch_event
    from ..._hooks_processed import is_processed, mark_processed
    from ..._pr_lookup import find_cards_by_pr

    repo = parsed["repo"]
    pr_number = parsed["pr_number"]
    dry_run = request.GET.get("dry") in ("1", "true", "yes")

    matched_cards = find_cards_by_pr(repo, pr_number)
    parsed["card_ids"] = matched_cards
    ledger_key = f"{repo}#{pr_number}"

    if dry_run:
        return JsonResponse(
            {
                "kind": "done",
                "dry_run": True,
                "would_mutate": list(matched_cards),
                "matched_cards": list(matched_cards),
                "ledger_key": ledger_key,
                "merge_commit": parsed.get("merge_commit"),
                "note": (
                    "dry-run: validation + lookup ran, no store or ledger "
                    "mutation"
                ),
            },
            safe=False,
        )

    existing_ledger = is_processed(repo, pr_number)
    if existing_ledger is not None:
        return JsonResponse(
            {
                "kind": "done",
                "already_processed": True,
                "ledger_key": ledger_key,
                "first_processed_at": existing_ledger.get(
                    "first_processed_at"
                ),
                "matched_cards": existing_ledger.get("matched_cards") or [],
                "merge_commit": existing_ledger.get("merge_commit"),
            },
            safe=False,
        )

    summary = dispatch_event(parsed)
    ledger_entry = mark_processed(
        repo,
        pr_number,
        merge_commit=parsed.get("merge_commit"),
        matched_cards=matched_cards,
        author=parsed.get("author"),
        processed_at=_dt.datetime.utcnow()
        .replace(microsecond=0)
        .isoformat()
        + "Z",
    )
    return JsonResponse(
        {
            "kind": "done",
            "matched_cards": list(matched_cards),
            "card_writes": summary.get("card_writes", []),
            "plugin_count": summary.get("plugin_count", 0),
            "plugin_errors": summary.get("plugin_errors", []),
            "ledger_key": ledger_key,
            "first_processed_at": ledger_entry.get("first_processed_at"),
            "merge_commit": parsed.get("merge_commit"),
            "note": (
                None
                if matched_cards
                else "no card matched repo+pr_number; entry logged for audit only"
            ),
        },
        safe=False,
    )
