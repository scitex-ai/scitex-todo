#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Views for the scitex-todo board Django app.

``board_page`` renders the React SPA inside the scitex-ui workspace shell
(falling back to a server-rendered static graph when the built frontend assets
are absent). ``api_dispatch`` routes ``/<endpoint>`` to the ``HANDLERS`` dict.
"""

import logging
from pathlib import Path

from django.http import FileResponse, HttpResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .handlers import HANDLERS, NO_BOARD_ENDPOINTS
from .services import get_board

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "scitex_todo"
_FAVICON_PATH = _STATIC_DIR / "favicon.svg"


def _tasks_path_from_request(request):
    """Optional explicit store path from the ``?store=`` query param."""
    return request.GET.get("store") or None


def favicon_view(request):
    """Serve the bundled SciTeX "S" SVG for the implicit `/favicon.ico` request.

    Modern browsers honor `Content-Type: image/svg+xml` for `.ico` URLs, so we
    serve the SVG directly. The standalone template also declares a
    `<link rel="icon" type="image/svg+xml">`, but browsers still request
    `/favicon.ico` on first visit before parsing <head>; without this route
    that request would fall through to `api_dispatch` and 404 (operator 3683).
    """
    if not _FAVICON_PATH.exists():
        return HttpResponseNotFound()
    # FileResponse handles streaming and the Content-Length header for us.
    return FileResponse(_FAVICON_PATH.open("rb"), content_type="image/svg+xml")


def board_page(request):
    """Serve the React SPA inside the scitex-ui shell, or a static fallback."""
    from django.template.loader import render_to_string

    built = (_STATIC_DIR / "assets" / "index.js").exists()

    if built:
        try:
            html = render_to_string(
                "scitex_todo/standalone.html",
                {"app_name": "scitex-todo", "app_label": "SciTeX Todo Board"},
                request=request,
            )
            return HttpResponse(html)
        except Exception:
            logger.exception("[scitex-todo] shell render failed; using fallback")

    # Fallback: server-rendered static graph (no Node/Vite build available).
    return HttpResponse(_static_graph_page(request))


def board_v3_page(request):
    """Serve the live board-v3 layout — operator's visual deliverable.

    Parallel to ``board_page`` (per lead a2a `62094366` — isolable, screen-
    shottable, A/B-comparable against the static :8052 prototype). Renders
    a self-contained HTML page that fetches ``/graph`` for real tasks.yaml
    data + renders the operator-co-designed layout (project columns +
    BLOCKING YOU panel + Resolve→``/resolve`` button per ADR-0006/0007).

    Server-rendered + inline-everything so it works regardless of Vite
    build state. The future React-SPA equivalent can re-render the same
    shape at the same URL when the FE rewrite lands.
    """
    from django.template.loader import render_to_string

    try:
        html = render_to_string(
            "scitex_todo/board_v3.html",
            {"app_name": "scitex-todo", "app_label": "SciTeX Todo — Board v3"},
            request=request,
        )
        return HttpResponse(html)
    except Exception:
        logger.exception("[scitex-todo] board_v3 render failed; using fallback")
        return HttpResponse(_static_graph_page(request))


def _static_graph_page(request) -> str:
    """Render a self-contained mermaid graph page (no React build needed).

    Uses mermaid.js from a CDN to draw the same ``build_mermaid`` source the
    PNG export uses, so the operator can view the graph even when the frontend
    toolchain has not produced a Vite bundle.
    """
    from scitex_todo._mermaid import build_mermaid

    try:
        board = get_board(_tasks_path_from_request(request))
        mermaid_src = build_mermaid(board.tasks)
        store = str(board.store_path)
        count = len(board.tasks)
    except Exception as exc:  # surface the load error in the page, not a 500
        mermaid_src = ""
        store = ""
        count = 0
        error = str(exc)
    else:
        error = ""

    body = (
        f'<pre class="mermaid">{mermaid_src}</pre>'
        if mermaid_src
        else f'<p class="err">Failed to load task store: {error}</p>'
    )
    meta = (
        f'<p class="meta">{count} tasks &middot; <code>{store}</code></p>'
        if store
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SciTeX Todo Board</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #1e1e2e;
    color: #e0e0e0; margin: 0; padding: 24px; }}
  h1 {{ color: #7c5cbf; font-size: 1.3rem; }}
  .meta {{ color: #a0a0b0; font-size: 0.85rem; }}
  .err {{ color: #ff6b6b; }}
  code {{ background: #313145; padding: 2px 6px; border-radius: 4px; }}
  .mermaid {{ background: #fafafa; border-radius: 8px; padding: 16px; }}
</style>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, theme: "default" }});
</script>
</head>
<body>
  <h1>SciTeX Todo &mdash; dependency graph</h1>
  {meta}
  {body}
</body>
</html>"""


def _get_board(request):
    """Return the board for this request, or None when the store can't load."""
    try:
        return get_board(_tasks_path_from_request(request))
    except FileNotFoundError:
        logger.warning("[scitex-todo] task store not found")
        return None


@csrf_exempt
def api_dispatch(request, endpoint):
    """Dispatch ``/<endpoint>`` to its handler function."""
    handler = HANDLERS.get(endpoint)
    if handler is None:
        return JsonResponse({"error": f"Unknown endpoint: {endpoint}"}, status=404)

    if endpoint in NO_BOARD_ENDPOINTS:
        return handler(request, None)

    board = _get_board(request)
    if board is None:
        return JsonResponse({"error": "No task store found."}, status=400)

    try:
        return handler(request, board)
    except Exception as exc:
        logger.exception("[scitex-todo] API error on /%s", endpoint)
        return JsonResponse({"error": str(exc)}, status=500)


# EOF
