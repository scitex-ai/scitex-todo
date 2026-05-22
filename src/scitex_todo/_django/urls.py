#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL patterns for the scitex-todo board Django app."""

from django.urls import path

from . import views

app_name = "scitex_todo"

urlpatterns = [
    path("", views.board_page, name="board"),
    path("<path:endpoint>", views.api_dispatch, name="api"),
]

# EOF
