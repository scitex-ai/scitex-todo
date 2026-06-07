#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL patterns for the scitex-todo board Django app."""

from django.urls import path

from . import views

app_name = "scitex_todo"

urlpatterns = [
    # ROOT = the operator-approved v3 layout. Operator TG 263 confirmed
    # post-screenshot: "はい、root においてください。http://127.0.0.1:8051/".
    # Lead-coordinated promotion per a2a `62094366` — once v3 was proven
    # on real seeded data + the operator green-lit it visually, swap the
    # root from the React-SPA GraphView to the server-rendered board_v3.
    path("", views.board_v3_page, name="board"),
    # The previous root view (React SPA GraphView built by Vite) moves to
    # `/legacy/` as a backup escape hatch — preserved (not deleted) so any
    # tool or muscle memory pointing at the old layout still has access.
    # If the operator decides v3 fully replaces the legacy board, this
    # entry can be dropped in a later PR.
    path("legacy", views.board_page, name="board_legacy"),
    path("legacy/", views.board_page, name="board_legacy_slash"),
    # `/board-v3/` alias retained for short-period back-compat (lead +
    # bookmarks may still hit it). Serves the same view as root.
    path("board-v3", views.board_v3_page, name="board_v3"),
    path("board-v3/", views.board_v3_page, name="board_v3_slash"),
    # `/favicon.ico` must precede the catch-all `<path:endpoint>` route — the
    # browser requests it implicitly and the catch-all would otherwise route
    # it to api_dispatch (→ 404). favicon_view serves the bundled SVG.
    path("favicon.ico", views.favicon_view, name="favicon"),
    path("<path:endpoint>", views.api_dispatch, name="api"),
]

# EOF
