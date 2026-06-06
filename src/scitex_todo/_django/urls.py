#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL patterns for the scitex-todo board Django app."""

from django.urls import path

from . import views

app_name = "scitex_todo"

urlpatterns = [
    path("", views.board_page, name="board"),
    # board-v3 — operator's visual deliverable (ADR-0006). Parallel to root
    # per lead a2a `62094366` so we keep today's GraphView untouched while
    # the v3 layout proves out on real data. Once approved by the operator,
    # promote to root + demote the current GraphView to /legacy.
    path("board-v3", views.board_v3_page, name="board_v3"),
    path("board-v3/", views.board_v3_page, name="board_v3_slash"),
    # `/favicon.ico` must precede the catch-all `<path:endpoint>` route — the
    # browser requests it implicitly and the catch-all would otherwise route
    # it to api_dispatch (→ 404). favicon_view serves the bundled SVG.
    path("favicon.ico", views.favicon_view, name="favicon"),
    path("<path:endpoint>", views.api_dispatch, name="api"),
]

# EOF
