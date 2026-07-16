#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django app for the scitex-todo dependency-graph board.

Mirrors figrecipe's ``_django`` subpackage: a Django app shipped inside the
library that renders the task dependency graph in a browser, both standalone
(``scitex-todo board``) and embedded in a scitex-cloud / scitex-hub workspace.

Usage (standalone):
    scitex-todo board
    # or, directly:
    python -m scitex_cards._django.management.commands.scitex_cards_board

Usage (integrated into a Django project):
    # settings.py
    INSTALLED_APPS = [..., "scitex_cards._django", ...]

    # urls.py
    path("scitex-todo/", include("scitex_cards._django.urls")),
"""

default_app_config = "scitex_cards._django.apps.ScitexTodoConfig"

__all__ = ["default_app_config"]

# EOF
