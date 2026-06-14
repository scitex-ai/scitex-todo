#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL patterns for the scitex-todo board Django app."""

from django.urls import path

from . import views
from .handlers.fleet import fleet_ci_status_view
from .handlers.runnable import blocked_batch_view, runnable_view

app_name = "scitex_todo"

urlpatterns = [
    # Fleet dashboard — Phase 1 surface (CI-status pills strip). The
    # registry-reader harness lives in ``handlers/fleet/`` and is the
    # template subsequent waves (hosts, mesh, timing, chat) plug into.
    # Registered BEFORE the catch-all ``<path:endpoint>`` route so the
    # slashed path is matched cleanly instead of getting routed to
    # ``api_dispatch`` (which would 404 — no handler named
    # "fleet/ci-status"). The fleet surfaces are intentionally
    # namespaced under ``/fleet/`` so future panels sit next to it.
    path("fleet/ci-status", fleet_ci_status_view, name="fleet_ci_status"),
    # T1.4 (lead a2a `74db4f2d`, 2026-06-14) — TRACK-1 dispatch backbone
    # HTTP surface. /runnable returns the FULL runnable set per the
    # T1.2 `runnable_tasks` predicate; /blocked-batch returns the
    # inverse view per T1.3. The lead-side parallelism dispatcher
    # consumes these instead of shelling out to the CLI verbs.
    path("runnable", runnable_view, name="runnable"),
    path("blocked-batch", blocked_batch_view, name="blocked_batch"),
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
